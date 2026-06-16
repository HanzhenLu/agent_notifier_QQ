"""QQ WebSocket 网关 Worker。

独立进程，监听 C2C_MESSAGE_CREATE 事件并处理 /bind /ping /whoami 命令。

启动：``python -m app.gateway_worker``
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import signal
from typing import Any, Optional

import websockets
from websockets.exceptions import ConnectionClosed

from app import db
from app.auth import hash_token
from app.config import get_settings
from app.qq_client import QQClient, QQClientError, new_qq_client_from_settings

logger = logging.getLogger(__name__)


# WebSocket OpCodes
OP_DISPATCH = 0
OP_HEARTBEAT = 1
OP_IDENTIFY = 2
OP_RECONNECT = 7
OP_INVALID_SESSION = 9
OP_HELLO = 10
OP_HEARTBEAT_ACK = 11

# 单聊事件 intent
INTENT_PUBLIC_MESSAGES = 1 << 25  # 33554432

RECONNECT_DELAY_SEC = 5


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )


def _mask_secret(text: str, secret: str, mask: str = "***") -> str:
    """将 ``text`` 中出现的 ``secret`` 替换为 ``mask``。"""
    if not secret:
        return text
    return text.replace(secret, mask)


class GatewayWorker:
    """单实例、长连接的 QQ WebSocket Worker。"""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._qq: QQClient = new_qq_client_from_settings()
        self._last_seq: Optional[int] = None
        self._stopping = asyncio.Event()

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    async def run_forever(self) -> None:
        """重连主循环。"""
        while not self._stopping.is_set():
            try:
                await self._connect_and_loop()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.exception("gateway worker crashed: %s", e)

            if self._stopping.is_set():
                break
            logger.info("reconnecting in %ss ...", RECONNECT_DELAY_SEC)
            try:
                await asyncio.wait_for(
                    self._stopping.wait(), timeout=RECONNECT_DELAY_SEC
                )
            except asyncio.TimeoutError:
                pass

        await self._qq.aclose()
        logger.info("gateway worker stopped")

    async def stop(self) -> None:
        """请求优雅停止。"""
        self._stopping.set()

    # ------------------------------------------------------------------
    # 单次连接 + 事件循环
    # ------------------------------------------------------------------

    async def _connect_and_loop(self) -> None:
        # 1. 获取网关地址
        gateway_url = await self._qq.get_gateway_url()
        logger.info("gateway url: %s", gateway_url)

        # 2. 拿 access_token，用于 Identify
        token = await self._qq.get_access_token()

        async with websockets.connect(gateway_url, max_size=2**20) as ws:
            logger.info("websocket connected")

            heartbeat_task: Optional[asyncio.Task] = None
            try:
                # 3. 等待 HELLO
                hello_raw = await ws.recv()
                hello = json.loads(hello_raw)
                if hello.get("op") != OP_HELLO:
                    raise RuntimeError(
                        f"expected HELLO op=10, got: {hello}"
                    )
                heartbeat_interval = int(
                    hello.get("d", {}).get("heartbeat_interval", 30000)
                )
                logger.info(
                    "received HELLO, heartbeat_interval=%sms", heartbeat_interval
                )

                # 4. 启动心跳
                heartbeat_task = asyncio.create_task(
                    self._heartbeat_loop(ws, heartbeat_interval)
                )

                # 5. 发送 Identify
                identify_payload = {
                    "op": OP_IDENTIFY,
                    "d": {
                        "token": f"QQBot {token}",
                        "intents": INTENT_PUBLIC_MESSAGES,
                        "shard": [0, 1],
                        "properties": {
                            "$os": "linux",
                            "$browser": "agent-notifier",
                            "$device": "agent-notifier",
                        },
                    },
                }
                await ws.send(json.dumps(identify_payload))
                logger.info("identify sent")

                # 6. 事件循环
                async for raw in ws:
                    await self._dispatch(ws, raw)
            finally:
                if heartbeat_task is not None:
                    heartbeat_task.cancel()
                    try:
                        await heartbeat_task
                    except (asyncio.CancelledError, Exception):
                        pass

    # ------------------------------------------------------------------
    # 心跳
    # ------------------------------------------------------------------

    async def _heartbeat_loop(self, ws, interval_ms: int) -> None:
        """周期性发送心跳。"""
        try:
            while True:
                await asyncio.sleep(interval_ms / 1000.0)
                payload = {"op": OP_HEARTBEAT, "d": self._last_seq}
                await ws.send(json.dumps(payload))
                logger.debug("heartbeat sent, seq=%s", self._last_seq)
        except asyncio.CancelledError:
            raise
        except ConnectionClosed:
            logger.info("heartbeat: connection closed")
        except Exception as e:  # noqa: BLE001
            logger.exception("heartbeat error: %s", e)

    # ------------------------------------------------------------------
    # 派发
    # ------------------------------------------------------------------

    async def _dispatch(self, ws, raw: Any) -> None:
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning("invalid ws payload: %r", raw)
            return

        op = payload.get("op")

        if op == OP_DISPATCH:
            seq = payload.get("s")
            if seq is not None:
                self._last_seq = seq
            event_type = payload.get("t")
            data = payload.get("d") or {}
            if event_type == "READY":
                logger.info("READY event received: %s", data)
            elif event_type == "C2C_MESSAGE_CREATE":
                await self._handle_c2c_message(data)
            else:
                logger.debug("ignored event: %s", event_type)

        elif op == OP_HEARTBEAT_ACK:
            logger.debug("heartbeat ack")

        elif op == OP_RECONNECT:
            logger.info("server requested reconnect (op=7), closing socket")
            await ws.close()

        elif op == OP_INVALID_SESSION:
            logger.warning("invalid session (op=9), closing socket to re-identify")
            await ws.close()

        else:
            logger.debug("unknown op: %s", op)

    # ------------------------------------------------------------------
    # C2C 消息处理
    # ------------------------------------------------------------------

    async def _handle_c2c_message(self, data: dict[str, Any]) -> None:
        msg_id = data.get("id") or ""
        content = (data.get("content") or "").strip()
        author = data.get("author") or {}
        user_openid = author.get("user_openid")

        # 写入入站消息（用于排查 + 去重，避免崩溃后重复处理）
        # 注意：mask 入库内容中的 BIND_SECRET，避免 secret 落库
        bind_secret = self._settings.bind_secret
        masked_content = _mask_secret(content, bind_secret)
        # 同时把 raw_json 中的 secret 也 mask 掉
        raw_json = json.dumps(data, ensure_ascii=False)
        masked_raw_json = _mask_secret(raw_json, bind_secret)

        is_new = db.insert_inbound_message(
            qq_msg_id=msg_id,
            event_type="C2C_MESSAGE_CREATE",
            user_openid=user_openid,
            content=masked_content,
            raw_json=masked_raw_json,
        )
        if not is_new:
            logger.info("duplicate inbound message ignored: %s", msg_id)
            return

        if not user_openid:
            logger.warning("c2c message without user_openid: %s", masked_content)
            return

        # 安全日志：不直接打印原始 content（可能含 secret）
        logger.info(
            "c2c message from %s msg_id=%s content=%r",
            user_openid,
            msg_id,
            masked_content,
        )

        # 命令派发
        if content.startswith("/bind"):
            await self._handle_bind(content, user_openid, msg_id)
        elif content == "/unbind":
            await self._handle_unbind(user_openid, msg_id)
        elif content == "/whoami":
            await self._handle_whoami(user_openid, msg_id)
        elif content == "/ping":
            await self._reply(user_openid, "pong", msg_id=msg_id)
        else:
            logger.debug("ignored non-command message: %s", masked_content)

    # ------------------------------------------------------------------
    # 命令处理
    # ------------------------------------------------------------------

    async def _handle_bind(
        self, content: str, user_openid: str, msg_id: str
    ) -> None:
        """处理 ``/bind <secret>`` 命令。

        校验通过后：
        - 生成新的 agent_token，覆盖该 user_openid 的旧 token；
        - 私信回复 token 明文（仅此一次）和使用方法。
        """
        expected = f"/bind {self._settings.bind_secret}"
        if content != expected:
            # 校验失败：回复绑定失败，但不要回显错误的 secret 文本
            logger.warning(
                "bind failed: openid=%s msg_id=%s (secret mismatch)",
                user_openid,
                msg_id,
            )
            await self._reply(
                user_openid,
                "❌ 绑定失败：BIND_SECRET 不正确。",
                msg_id=msg_id,
            )
            return

        # 自动起名：user_<openid 后 6 位>
        suffix = (user_openid or "")[-6:] or "anon"
        name = f"user_{suffix}"

        # 生成 32 字节 URL-safe token，加可读前缀
        raw_token = secrets.token_urlsafe(32)
        prefix = self._settings.token_prefix or "ant_"
        agent_token = f"{prefix}{raw_token}"
        token_prefix_for_log = agent_token[:8]
        token_hash = hash_token(agent_token)

        try:
            db.upsert_target_with_token(
                user_openid=user_openid,
                name=name,
                agent_token_hash=token_hash,
                token_prefix=token_prefix_for_log,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("bind upsert failed: %s", e)
            await self._reply(
                user_openid,
                "❌ 绑定失败：服务器内部错误，请稍后重试。",
                msg_id=msg_id,
            )
            return

        logger.info(
            "bind success: openid=%s name=%s token_prefix=%s",
            user_openid,
            name,
            token_prefix_for_log,
        )

        # 私信回复 token 明文（仅此一次）
        reply = (
            "✅ 绑定成功。\n"
            f"你的 agent_token（仅此一次显示）：\n{agent_token}\n"
            "\n请妥善保管：丢失需重新 /bind 重置。"
            "\n使用方式：\n"
            "  curl -H \"Authorization: Bearer <agent_token>\" \\\n"
            "       -X POST <AGENT_NOTIFY_URL>/v1/notify/test"
        )
        await self._reply(user_openid, reply, msg_id=msg_id)

    async def _handle_unbind(self, user_openid: str, msg_id: str) -> None:
        """处理 ``/unbind`` 命令：禁用绑定并使 token 立即失效。"""
        affected = db.disable_target_by_openid(user_openid)
        if affected:
            logger.info("unbind success: openid=%s", user_openid)
            await self._reply(
                user_openid,
                "✅ 已解绑：原 agent_token 立即失效，后续不会再向你推送通知。\n"
                "重新使用请发送 /bind <BIND_SECRET>。",
                msg_id=msg_id,
            )
        else:
            await self._reply(
                user_openid,
                "ℹ️ 你当前没有有效绑定，无需解绑。",
                msg_id=msg_id,
            )

    async def _handle_whoami(self, user_openid: str, msg_id: str) -> None:
        """处理 ``/whoami`` 命令：展示当前绑定状态（不显示 token 明文）。"""
        target = db.get_target_by_openid(user_openid)
        if target is None:
            reply = (
                "ℹ️ 当前未绑定。\n"
                f"你的 user_openid: {user_openid}\n"
                "发送 /bind <BIND_SECRET> 完成绑定。"
            )
        else:
            enabled = bool(target.get("enabled"))
            status_emoji = "✅" if enabled else "⛔"
            reply = (
                f"{status_emoji} 当前绑定信息：\n"
                f"name: {target.get('name')}\n"
                f"user_openid: {user_openid}\n"
                f"enabled: {enabled}\n"
                f"token_prefix: {target.get('token_prefix') or '<none>'}\n"
                f"last_used_at: {target.get('last_used_at') or '<never>'}\n"
                f"created_at: {target.get('created_at')}\n"
                f"updated_at: {target.get('updated_at')}"
            )
        await self._reply(user_openid, reply, msg_id=msg_id)

    async def _reply(
        self,
        user_openid: str,
        content: str,
        *,
        msg_id: Optional[str] = None,
    ) -> None:
        """被动回复一条文本消息。"""
        try:
            await self._qq.send_c2c_message(
                user_openid, content, msg_id=msg_id, msg_seq=1
            )
        except QQClientError as e:
            logger.error("reply failed: openid=%s err=%s", user_openid, e)


async def _amain() -> None:
    _setup_logging()
    db.init_db()

    worker = GatewayWorker()

    loop = asyncio.get_running_loop()
    main_task = asyncio.current_task()

    stop_count = {"n": 0}

    def _signal_handler() -> None:
        stop_count["n"] += 1
        logger.info("received stop signal (%d)", stop_count["n"])
        # 第一次：标记停止 + 取消主任务，让 WebSocket recv / sleep 立刻退出
        worker._stopping.set()
        if main_task is not None and not main_task.done():
            main_task.cancel()
        # 连续按多次仍然卡住时，直接强退
        if stop_count["n"] >= 3:
            logger.warning("force exit after %d stop signals", stop_count["n"])
            import os
            os._exit(130)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows 等平台不支持
            pass

    try:
        await worker.run_forever()
    except asyncio.CancelledError:
        logger.info("main task cancelled, shutting down")
        try:
            await worker._qq.aclose()
        except Exception:
            pass


def main() -> None:
    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
