"""HTTP 鉴权依赖。"""

from __future__ import annotations

import logging

from fastapi import Header, HTTPException

from app.config import get_settings

logger = logging.getLogger(__name__)


def require_agent_auth(authorization: str = Header(default="")) -> None:
    """校验 ``Authorization: Bearer <AGENT_NOTIFY_TOKEN>``。

    比对失败时抛 401。日志中不打印 token 原文。
    """
    settings = get_settings()
    expected = f"Bearer {settings.agent_notify_token}"
    if not authorization or authorization != expected:
        logger.warning(
            "auth failed: header_prefix=%r",
            (authorization[:10] + "***") if authorization else "<empty>",
        )
        raise HTTPException(status_code=401, detail="invalid token")
