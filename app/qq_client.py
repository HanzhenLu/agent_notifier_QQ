"""QQ OpenAPI 客户端。

特性：
- 内存缓存 access_token，到期前 60 秒刷新。
- asyncio.Lock 防并发刷新。
- 复用 httpx.AsyncClient。
- 提供 send_c2c_message（支持主动消息和被动回复）和 get_gateway_url。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

# 提前刷新阈值（秒）
TOKEN_REFRESH_LEEWAY = 60
# 默认超时
DEFAULT_TIMEOUT = 10.0
# AccessToken 获取地址（与沙箱 base 不同，是固定的鉴权域名）
ACCESS_TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"


class QQClientError(Exception):
    """QQ 客户端调用异常。"""


class QQClient:
    """QQ OpenAPI 异步客户端。"""

    def __init__(self, app_id: str, app_secret: str, api_base: str) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._api_base = api_base.rstrip("/")
        self._access_token: Optional[str] = None
        # 单调时间戳（秒），表示 token 失效时间
        self._token_expire_at: float = 0.0
        self._token_lock = asyncio.Lock()
        self._http = httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)

    async def aclose(self) -> None:
        """关闭底层 HTTP 客户端。"""
        await self._http.aclose()

    # ------------------------------------------------------------------
    # AccessToken
    # ------------------------------------------------------------------

    async def get_access_token(self) -> str:
        """获取 access_token，自动缓存与刷新。"""
        now = time.monotonic()
        if self._access_token and now < self._token_expire_at - TOKEN_REFRESH_LEEWAY:
            return self._access_token

        async with self._token_lock:
            # double-check
            now = time.monotonic()
            if (
                self._access_token
                and now < self._token_expire_at - TOKEN_REFRESH_LEEWAY
            ):
                return self._access_token

            payload = {
                "appId": self._app_id,
                "clientSecret": self._app_secret,
            }
            try:
                resp = await self._http.post(
                    ACCESS_TOKEN_URL,
                    json=payload,
                    timeout=DEFAULT_TIMEOUT,
                )
            except httpx.HTTPError as e:
                logger.error("get_access_token network error: %s", e)
                raise QQClientError(f"get_access_token network error: {e}") from e

            if resp.status_code >= 400:
                # 注意：不要打印 payload（含 secret）
                logger.error(
                    "get_access_token failed: status=%s body=%s",
                    resp.status_code,
                    resp.text,
                )
                raise QQClientError(
                    f"get_access_token failed: status={resp.status_code} body={resp.text}"
                )

            try:
                data = resp.json()
            except ValueError as e:
                raise QQClientError(
                    f"get_access_token: invalid json body={resp.text}"
                ) from e

            token = data.get("access_token")
            expires_in_raw = data.get("expires_in", "7200")
            if not token:
                raise QQClientError(
                    f"get_access_token: missing access_token in response: {data}"
                )

            try:
                expires_in = int(expires_in_raw)
            except (TypeError, ValueError):
                expires_in = 7200

            self._access_token = token
            self._token_expire_at = time.monotonic() + max(expires_in, 60)
            logger.info(
                "qq access_token refreshed, expires_in=%ss", expires_in
            )
            return token

    # ------------------------------------------------------------------
    # 通用请求
    # ------------------------------------------------------------------

    async def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """对 QQ OpenAPI 发起请求。"""
        token = await self.get_access_token()
        url = f"{self._api_base}{path}"
        headers = {
            "Authorization": f"QQBot {token}",
            "Content-Type": "application/json",
        }
        try:
            resp = await self._http.request(
                method,
                url,
                headers=headers,
                json=json_body,
                params=params,
                timeout=DEFAULT_TIMEOUT,
            )
        except httpx.HTTPError as e:
            logger.error("qq request network error: %s %s -> %s", method, path, e)
            raise QQClientError(
                f"qq request network error: {method} {path}: {e}"
            ) from e

        if resp.status_code >= 400:
            logger.error(
                "qq request failed: %s %s status=%s body=%s",
                method,
                path,
                resp.status_code,
                resp.text,
            )
            raise QQClientError(
                f"qq request failed: {method} {path} status={resp.status_code} body={resp.text}"
            )

        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            # 非 JSON 响应
            return {"raw": resp.text}

    # ------------------------------------------------------------------
    # 单聊消息
    # ------------------------------------------------------------------

    async def send_c2c_message(
        self,
        user_openid: str,
        content: str,
        *,
        msg_id: Optional[str] = None,
        msg_seq: Optional[int] = None,
        event_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """单聊发送文本消息。

        - 主动消息：不传 msg_id。
        - 被动回复：传入 msg_id（事件中的 d.id），msg_seq 默认 1。
        """
        path = f"/v2/users/{user_openid}/messages"
        body: dict[str, Any] = {
            "content": content,
            "msg_type": 0,
        }
        if msg_id:
            body["msg_id"] = msg_id
            body["msg_seq"] = msg_seq if msg_seq is not None else 1
        if event_id:
            body["event_id"] = event_id
        return await self.request("POST", path, json_body=body)

    # ------------------------------------------------------------------
    # 网关
    # ------------------------------------------------------------------

    async def get_gateway_url(self) -> str:
        """获取 WebSocket 网关地址。"""
        data = await self.request("GET", "/gateway")
        url = data.get("url")
        if not url:
            raise QQClientError(f"get_gateway_url: missing url in response: {data}")
        return url


# ---------------------------------------------------------------------------
# 全局单例（供 api-server 复用）
# ---------------------------------------------------------------------------


_qq_client_singleton: Optional[QQClient] = None


def get_qq_client() -> QQClient:
    """获取（或创建）全局 QQClient 单例。

    ⚠️ 调用本函数前必须已经初始化好 Settings（环境变量已加载）。
    用于 api-server 进程；gateway-worker 应自行 ``new_qq_client_from_settings``。
    """
    global _qq_client_singleton
    if _qq_client_singleton is None:
        _qq_client_singleton = new_qq_client_from_settings()
    return _qq_client_singleton


def new_qq_client_from_settings() -> QQClient:
    """从全局配置构造一个新的 QQClient 实例。"""
    from app.config import get_settings

    s = get_settings()
    return QQClient(
        app_id=s.qq_app_id,
        app_secret=s.qq_app_secret,
        api_base=s.qq_api_base,
    )


async def close_qq_client_singleton() -> None:
    """关闭全局单例（供 lifespan 使用）。"""
    global _qq_client_singleton
    if _qq_client_singleton is not None:
        await _qq_client_singleton.aclose()
        _qq_client_singleton = None
