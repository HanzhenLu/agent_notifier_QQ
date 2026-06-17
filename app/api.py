"""HTTP API 路由。"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app import db
from app.auth import require_agent_auth
from app.config import get_settings
from app.formatter import format_agent_done_message
from app.models import AgentDoneReq, TestNotifyReq
from app.qq_client import QQClientError, get_qq_client

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------


@router.get("/health")
async def health() -> dict[str, Any]:
    """无鉴权健康检查。"""
    return {"ok": True}


# ---------------------------------------------------------------------------
# /v1/notify/test
# ---------------------------------------------------------------------------


@router.post("/v1/notify/test")
async def notify_test(
    req: Optional[TestNotifyReq] = None,
    target: dict[str, Any] = Depends(require_agent_auth),
) -> dict[str, Any]:
    """发送一条测试 QQ 消息到 token 所属用户。"""
    target_openid = target["user_openid"]
    content = (req.content if req else None) or "✅ agent-notifier 测试消息发送成功"

    client = get_qq_client()
    try:
        qq_resp = await client.send_c2c_message(target_openid, content)
    except QQClientError as e:
        logger.error("notify_test send failed: %s", e)
        raise HTTPException(status_code=502, detail=f"QQ send failed: {e}") from e

    return {
        "ok": True,
        "target": target["name"],
        "qq_response": qq_resp,
    }


# ---------------------------------------------------------------------------
# /v1/notify/agent-done
# ---------------------------------------------------------------------------


@router.post("/v1/notify/agent-done")
async def notify_agent_done(
    req: AgentDoneReq,
    target: dict[str, Any] = Depends(require_agent_auth),
) -> dict[str, Any]:
    """接收 agent 结束事件并推送 QQ。"""
    target_openid = target["user_openid"]

    # 1. 去重检查（命中重复时不写库）
    if db.event_exists(req.event_id):
        logger.info("event already processed: %s", req.event_id)
        return {
            "ok": True,
            "duplicated": True,
            "message": "event already processed",
        }

    # 2. 格式化消息
    s = get_settings()
    content = format_agent_done_message(req, max_len=s.max_message_length)

    # 3. 发送
    client = get_qq_client()
    qq_resp: dict[str, Any] = {}
    send_status = "sent"
    err_text: Optional[str] = None
    qq_message_id: Optional[str] = None
    try:
        # 注意：req.event_id 仅用于本服务自身去重 / 落库，不能透传给 QQ。
        # QQ /v2/users/{openid}/messages 接口的 event_id 字段语义是
        # 「响应哪个 QQ 回调事件」，主动消息场景下不应携带该字段，
        # 否则 QQ 会返回 40034025 "请求参数event_id无效"。
        qq_resp = await client.send_c2c_message(target_openid, content)
        qq_message_id = qq_resp.get("id") if isinstance(qq_resp, dict) else None
    except QQClientError as e:
        send_status = "failed"
        err_text = str(e)
        logger.error("notify_agent_done send failed: %s", e)

    # 4. 落库（无论成功失败都写）
    db.insert_notify_event(
        event_id=req.event_id,
        project=req.project,
        agent=req.agent,
        status=req.status,
        exit_code=req.exit_code,
        duration_sec=req.duration_sec,
        cwd=req.cwd,
        host=req.host,
        branch=req.branch,
        commit=req.commit,
        summary=req.summary,
        log_tail=req.log_tail,
        target_openid=target_openid,
        qq_message_id=qq_message_id,
        send_status=send_status,
        error=err_text,
    )

    if send_status == "failed":
        raise HTTPException(status_code=502, detail=f"QQ send failed: {err_text}")

    return {
        "ok": True,
        "duplicated": False,
        "qq_response": qq_resp,
    }


# ---------------------------------------------------------------------------
# /v1/events  —— 仅返回当前 token 所属用户的事件
# ---------------------------------------------------------------------------


@router.get("/v1/events")
async def list_events(
    limit: int = Query(default=20, ge=1, le=200),
    target: dict[str, Any] = Depends(require_agent_auth),
) -> dict[str, Any]:
    rows = db.list_events(limit=limit)
    target_openid = target["user_openid"]
    rows = [r for r in rows if r.get("target_openid") == target_openid]
    return {"events": rows}


# ---------------------------------------------------------------------------
# /v1/targets/me  —— 返回当前 token 所属 target 的元信息（不含 token）
# ---------------------------------------------------------------------------


@router.get("/v1/targets/me")
async def get_my_target(
    target: dict[str, Any] = Depends(require_agent_auth),
) -> dict[str, Any]:
    return {
        "name": target["name"],
        "user_openid": target["user_openid"],
        "enabled": bool(target["enabled"]),
        "token_prefix": target.get("token_prefix"),
        "last_used_at": target.get("last_used_at"),
        "created_at": target["created_at"],
        "updated_at": target["updated_at"],
    }
