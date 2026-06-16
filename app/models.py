"""Pydantic 请求/响应模型。"""

from typing import Literal, Optional

from pydantic import BaseModel, Field


AgentStatus = Literal["success", "failed", "cancelled", "timeout", "unknown"]


class AgentDoneReq(BaseModel):
    """`/v1/notify/agent-done` 请求体。"""

    event_id: str = Field(..., max_length=200, description="去重用的事件 ID")
    project: str = Field(..., max_length=100)
    agent: str = Field(default="unknown", max_length=100)
    status: AgentStatus
    exit_code: Optional[int] = None
    duration_sec: Optional[int] = None
    cwd: Optional[str] = Field(default=None, max_length=500)
    host: Optional[str] = Field(default=None, max_length=100)
    branch: Optional[str] = Field(default=None, max_length=100)
    commit: Optional[str] = Field(default=None, max_length=100)
    summary: Optional[str] = Field(default=None, max_length=1200)
    log_tail: Optional[str] = Field(default=None, max_length=3000)


class TestNotifyReq(BaseModel):
    """`/v1/notify/test` 请求体。"""

    content: Optional[str] = Field(default=None, max_length=1800)
