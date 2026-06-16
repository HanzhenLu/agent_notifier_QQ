"""消息格式化。

把 AgentDoneReq 渲染成发送给 QQ 的纯文本消息。
"""

from __future__ import annotations

from typing import Optional

from app.models import AgentDoneReq


STATUS_ICONS: dict[str, str] = {
    "success": "✅",
    "failed": "❌",
    "cancelled": "⚠️",
    "timeout": "⏰",
    "unknown": "ℹ️",
}

TRUNCATED_SUFFIX = "\n...（消息过长，已截断）"


def format_duration(seconds: Optional[int]) -> str:
    """把秒数渲染为 ``1h2m3s`` 格式。

    - ``None`` -> ``unknown``
    - 0 -> ``0s``
    - 65 -> ``1m5s``
    - 3605 -> ``1h0m5s``
    """
    if seconds is None:
        return "unknown"
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return "unknown"
    if s < 0:
        s = 0

    hours, rem = divmod(s, 3600)
    minutes, secs = divmod(rem, 60)
    if hours > 0:
        return f"{hours}h{minutes}m{secs}s"
    if minutes > 0:
        return f"{minutes}m{secs}s"
    return f"{secs}s"


def _truncate(text: str, max_len: int) -> str:
    """超长则截断并追加固定后缀。"""
    if max_len <= 0 or len(text) <= max_len:
        return text
    suffix = TRUNCATED_SUFFIX
    if max_len <= len(suffix):
        # 极端情况，直接硬截断
        return text[:max_len]
    keep = max_len - len(suffix)
    return text[:keep] + suffix


def format_agent_done_message(req: AgentDoneReq, max_len: int = 1800) -> str:
    """把 agent 结束事件渲染成 QQ 消息文本。

    None 字段不会输出多余空行。
    默认不会包含 log_tail，避免消息过长。
    """
    icon = STATUS_ICONS.get(req.status, STATUS_ICONS["unknown"])

    lines: list[str] = [f"{icon} Agent 结束", ""]

    # 主体字段，按需求顺序输出
    lines.append(f"项目：{req.project}")
    lines.append(f"Agent：{req.agent}")
    lines.append(f"状态：{req.status}")

    if req.exit_code is not None:
        lines.append(f"退出码：{req.exit_code}")

    lines.append(f"耗时：{format_duration(req.duration_sec)}")

    if req.host:
        lines.append(f"主机：{req.host}")
    if req.cwd:
        lines.append(f"路径：{req.cwd}")
    if req.branch:
        lines.append(f"分支：{req.branch}")
    if req.commit:
        lines.append(f"提交：{req.commit}")

    if req.summary:
        lines.append("")
        lines.append("摘要：")
        lines.append(req.summary)

    text = "\n".join(lines)
    return _truncate(text, max_len)
