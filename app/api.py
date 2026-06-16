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
# 工具：解析当前默认目标 openid
# ---------------------------------------------------------------------------


def _resolve_target_openid() -> Optional[str]:
    """优先用环境变量；否则取 DB 中 name='me' 的目标。"""
    s = get_settings()
    if s.qq_target_openid:
        return s.qq_target_openid
    target = db.get_target("me")
    if target:
        return target["user_openid"]
    return None


# ---------------------------------------------------------------------------
# /v1/notify/test
# ---------------------------------------------------------------------------


@router.post(
    "/v1/notify/test",
    dependencies=[Depends(require_agent_auth)],
)
async def notify_test(req: Optional[TestNotifyReq] = None) -> dict[str, Any]:
    """发送一条测试 QQ 消息。"""
    target_openid = _resolve_target_openid()
    if not target_openid:
        raise HTTPException(
            status_code=400,
            detail=(
                'target openid is not configured; '
                'send "/bind <BIND_SECRET>" to bot first or set QQ_TARGET_OPENID'
            ),
        )

    content = (req.content if req else None) or "✅ agent-notifier 测试消息发送成功"

    client = get_qq_client()
    try:
        qq_resp = await client.send_c2c_message(target_openid, content)
    except QQClientError as e:
        logger.error("notify_test send failed: %s", e)
        raise HTTPException(status_code=502, detail=f"QQ send failed: {e}") from e

    return {
        "ok": True,
        "target": "me",
        "qq_response": qq_resp,
    }


# ---------------------------------------------------------------------------
# /v1/notify/agent-done
# ---------------------------------------------------------------------------


@router.post(
    "/v1/notify/agent-done",
    dependencies=[Depends(require_agent_auth)],
)
async def notify_agent_done(req: AgentDoneReq) -> dict[str, Any]:
    """接收 agent 结束事件并推送 QQ。"""
    # 1. 去重检查（命中重复时不写库）
    if db.event_exists(req.event_id):
        logger.info("event already processed: %s", req.event_id)
        return {
            "ok": True,
            "duplicated": True,
            "message": "event already processed",
        }

    # 2. 解析目标
    target_openid = _resolve_target_openid()
    if not target_openid:
        raise HTTPException(
            status_code=400,
            detail=(
                'target openid is not configured; '
                'send "/bind <BIND_SECRET>" to bot first or set QQ_TARGET_OPENID'
            ),
        )

    # 3. 格式化消息
    s = get_settings()
    content = format_agent_done_message(req, max_len=s.max_message_length)

    # 4. 发送
    client = get_qq_client()
    qq_resp: dict[str, Any] = {}
    send_status = "sent"
    err_text: Optional[str] = None
    qq_message_id: Optional[str] = None
    try:
        qq_resp = await client.send_c2c_message(
            target_openid, content, event_id=req.event_id
        )
        # QQ API 通常返回 {"id": "...", "timestamp": ...}
        qq_message_id = qq_resp.get("id") if isinstance(qq_resp, dict) else None
    except QQClientError as e:
        send_status = "failed"
        err_text = str(e)
        logger.error("notify_agent_done send failed: %s", e)

    # 5. 落库（无论成功失败都写）
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
# /v1/events
# ---------------------------------------------------------------------------


@router.get(
    "/v1/events",
    dependencies=[Depends(require_agent_auth)],
)
async def list_events(limit: int = Query(default=20, ge=1, le=200)) -> dict[str, Any]:
    return {"events": db.list_events(limit=limit)}


# ---------------------------------------------------------------------------
# /v1/targets
# ---------------------------------------------------------------------------


@router.get(
    "/v1/targets",
    dependencies=[Depends(require_agent_auth)],
)
async def list_targets() -> dict[str, Any]:
    s = get_settings()
    rows = db.list_targets()
    # 把 enabled 转为 bool
    targets = []
    for r in rows:
        targets.append(
            {
                "name": r["name"],
                "user_openid": r["user_openid"],
                "enabled": bool(r["enabled"]),
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
        )
    return {
        "targets": targets,
        "env_target_configured": bool(s.qq_target_openid),
    }
