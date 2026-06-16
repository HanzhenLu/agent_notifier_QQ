"""HTTP 鉴权依赖。

每个 QQ 用户首次 ``/bind`` 时会得到一个独立的 ``agent_token``。
所有 ``/v1/*`` 接口通过 ``Authorization: Bearer <agent_token>`` 鉴权，
服务端把 token 做 sha256 后查 ``targets`` 表，命中即放行并返回该用户的 target。
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from fastapi import Header, HTTPException

from app import db

logger = logging.getLogger(__name__)


_INVALID_TOKEN_DETAIL = (
    "invalid or unbound token, send /bind <BIND_SECRET> to the bot first"
)


def hash_token(token: str) -> str:
    """对 agent_token 做 sha256，统一存库与比对方式。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _parse_bearer(authorization: str) -> str:
    """从 Authorization Header 提取 Bearer token，否则抛 401。"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail=_INVALID_TOKEN_DETAIL)
    token = authorization[len("Bearer ") :].strip()
    if not token:
        raise HTTPException(status_code=401, detail=_INVALID_TOKEN_DETAIL)
    return token


async def require_agent_auth(authorization: str = Header(default="")) -> dict[str, Any]:
    """校验 ``Authorization: Bearer <agent_token>``。

    成功时返回该 token 对应的 target dict（含 ``user_openid`` 等字段，
    不包含明文 token）。失败时抛 401。
    """
    token = _parse_bearer(authorization)
    target = db.get_target_by_token_hash(hash_token(token))
    if target is None or not target.get("enabled"):
        # 仅日志中输出 prefix，避免泄漏
        logger.warning("auth failed: token_prefix=%r", token[:8] + "***")
        raise HTTPException(status_code=401, detail=_INVALID_TOKEN_DETAIL)

    # 异步路径里更新 last_used_at（吞错）
    try:
        db.touch_target_last_used(target["id"])
    except Exception:  # noqa: BLE001
        pass

    return target
