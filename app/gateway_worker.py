"""QQ WebSocket 网关 Worker。

独立进程，监听 C2C_MESSAGE_CREATE 事件并处理 /bind /ping /whoami 命令。

启动：``python -m app.gateway_worker``
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
from typing import Any, Optional

import websockets
from websockets.exceptions import ConnectionClosed

from app import db
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
        elif content == "/ping":
            await self._reply(user_openid, "pong", msg_id=msg_id)
        elif content == "/whoami":
            await self._reply(
                user_openid,
                f"your user_openid: {user_openid}",
                msg_id=msg_id,
            )
        else:
            logger.debug("ignored non-command message: %s", masked_content)

    async def _handle_bind(
        self, content: str, user_openid: str, msg_id: str
    ) -> None:
        """处理 /bind <secret> 命令。"""
        expected = f"/bind {self._settings.bind_secret}"
        if content == expected:
            db.upsert_target("me", user_openid)
            logger.info("bind success: openid=%s", user_openid)
            await self._reply(
                user_openid,
                "✅ 绑定成功，后续 agent 结束通知会发送到当前 QQ。",
                msg_id=msg_id,
            )
        else:
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

    def _signal_handler() -> None:
        logger.info("received stop signal")
        asyncio.create_task(worker.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows 等平台不支持
            pass

    await worker.run_forever()


def main() -> None:
    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
