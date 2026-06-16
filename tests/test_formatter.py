"""formatter 单元测试。"""

from __future__ import annotations

from app.formatter import (
    STATUS_ICONS,
    TRUNCATED_SUFFIX,
    format_agent_done_message,
    format_duration,
)
from app.models import AgentDoneReq


def _make_req(**overrides) -> AgentDoneReq:
    base: dict = {
        "event_id": "evt-1",
        "project": "voltron",
        "agent": "codex-voltron",
        "status": "success",
        "exit_code": 0,
        "duration_sec": 2538,
        "cwd": "/home/ubuntu/voltron",
        "host": "server-2",
        "branch": "main",
        "commit": "a1b2c3d",
        "summary": "修复代理配置问题，并完成测试。",
    }
    base.update(overrides)
    return AgentDoneReq(**base)


# ---------- format_duration ----------


def test_format_duration_none():
    assert format_duration(None) == "unknown"


def test_format_duration_seconds_only():
    assert format_duration(0) == "0s"
    assert format_duration(45) == "45s"


def test_format_duration_minutes_seconds():
    assert format_duration(65) == "1m5s"


def test_format_duration_hours_minutes_seconds():
    assert format_duration(3605) == "1h0m5s"
    assert format_duration(7325) == "2h2m5s"


# ---------- icons ----------


def test_success_icon_in_message():
    msg = format_agent_done_message(_make_req(status="success"))
    assert msg.startswith(STATUS_ICONS["success"])
    assert "状态：success" in msg


def test_failed_icon_in_message():
    msg = format_agent_done_message(_make_req(status="failed", exit_code=2))
    assert msg.startswith(STATUS_ICONS["failed"])
    assert "状态：failed" in msg
    assert "退出码：2" in msg


# ---------- 字段缺失不应输出空行 ----------


def test_optional_fields_omitted_when_none():
    req = _make_req(
        host=None,
        cwd=None,
        branch=None,
        commit=None,
        summary=None,
        exit_code=None,
        duration_sec=None,
    )
    msg = format_agent_done_message(req)
    # 不应出现这些字段名
    for token in ["主机：", "路径：", "分支：", "提交：", "退出码：", "摘要："]:
        assert token not in msg
    # 也不应出现连续两个以上的换行（除了开头那个）
    body = msg.split("\n\n", 1)[1] if "\n\n" in msg else msg
    assert "\n\n" not in body


# ---------- 截断 ----------


def test_long_summary_is_truncated():
    # summary 字段本身有 1200 字符上限，这里用 max_len=80 让正常消息也会被截断
    long_summary = "x" * 1200
    req = _make_req(summary=long_summary)
    msg = format_agent_done_message(req, max_len=80)
    assert len(msg) <= 80
    assert msg.endswith(TRUNCATED_SUFFIX)


def test_short_message_not_truncated():
    req = _make_req(summary="ok")
    msg = format_agent_done_message(req, max_len=1800)
    assert TRUNCATED_SUFFIX not in msg


# ---------- log_tail 不应进入消息 ----------


def test_log_tail_not_in_message():
    req = _make_req(log_tail="should not appear in message body")
    msg = format_agent_done_message(req)
    assert "should not appear" not in msg
