"""HTTP API 端到端测试（含多用户隔离）。

通过 monkeypatch 替换 QQClient.send_c2c_message，避免真实外呼。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import db
from app.auth import hash_token


class _FakeQQClient:
    """伪 QQ 客户端：记录每次发送，并返回固定 id。"""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_c2c_message(
        self,
        openid: str,
        content: str,
        *,
        msg_id: str | None = None,
        msg_seq: int = 1,
        event_id: str | None = None,
    ) -> dict[str, Any]:
        self.sent.append(
            {
                "openid": openid,
                "content": content,
                "event_id": event_id,
            }
        )
        return {"id": f"qqmsg-{len(self.sent)}", "timestamp": 0}


@pytest.fixture
def fake_client(monkeypatch):
    """注入 FakeQQClient 到 api 模块。"""
    fake = _FakeQQClient()
    from app import api

    monkeypatch.setattr(api, "get_qq_client", lambda: fake)
    return fake


def _bind_user(openid: str, token: str, name: str) -> None:
    db.upsert_target_with_token(
        user_openid=openid,
        name=name,
        agent_token_hash=hash_token(token),
        token_prefix=token[:8],
    )


def _make_client():
    # 延迟导入，确保 conftest 中的 isolated_db 已生效
    from app.main import app
    return TestClient(app)


def test_health_no_auth():
    client = _make_client()
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_notify_test_requires_auth():
    client = _make_client()
    resp = client.post("/v1/notify/test", json={"content": "hi"})
    assert resp.status_code == 401


def test_notify_test_sends_to_token_owner(fake_client):
    _bind_user("openid_alice", "ant_alice", name="user_alice")
    client = _make_client()

    resp = client.post(
        "/v1/notify/test",
        json={"content": "hello"},
        headers={"Authorization": "Bearer ant_alice"},
    )
    assert resp.status_code == 200
    assert len(fake_client.sent) == 1
    assert fake_client.sent[0]["openid"] == "openid_alice"
    assert fake_client.sent[0]["content"] == "hello"


def test_two_users_isolation(fake_client):
    _bind_user("openid_alice", "ant_alice", name="user_alice")
    _bind_user("openid_bob", "ant_bob", name="user_bob")
    client = _make_client()

    client.post(
        "/v1/notify/test",
        json={"content": "for alice"},
        headers={"Authorization": "Bearer ant_alice"},
    )
    client.post(
        "/v1/notify/test",
        json={"content": "for bob"},
        headers={"Authorization": "Bearer ant_bob"},
    )

    assert len(fake_client.sent) == 2
    assert fake_client.sent[0]["openid"] == "openid_alice"
    assert fake_client.sent[1]["openid"] == "openid_bob"


def test_agent_done_writes_event_to_caller_only(fake_client):
    _bind_user("openid_alice", "ant_alice", name="user_alice")
    _bind_user("openid_bob", "ant_bob", name="user_bob")
    client = _make_client()

    body = {
        "event_id": "evt-1",
        "project": "demo",
        "agent": "demo-agent",
        "status": "success",
    }
    resp = client.post(
        "/v1/notify/agent-done",
        json=body,
        headers={"Authorization": "Bearer ant_alice"},
    )
    assert resp.status_code == 200, resp.text
    assert fake_client.sent[0]["openid"] == "openid_alice"

    # /v1/events: alice 能看到，bob 看不到
    alice_events = client.get(
        "/v1/events", headers={"Authorization": "Bearer ant_alice"}
    ).json()["events"]
    bob_events = client.get(
        "/v1/events", headers={"Authorization": "Bearer ant_bob"}
    ).json()["events"]
    assert any(e["event_id"] == "evt-1" for e in alice_events)
    assert all(e["event_id"] != "evt-1" for e in bob_events)


def test_agent_done_dedup(fake_client):
    _bind_user("openid_alice", "ant_alice", name="user_alice")
    client = _make_client()
    body = {
        "event_id": "evt-dup",
        "project": "demo",
        "agent": "demo-agent",
        "status": "success",
    }
    headers = {"Authorization": "Bearer ant_alice"}

    r1 = client.post("/v1/notify/agent-done", json=body, headers=headers)
    r2 = client.post("/v1/notify/agent-done", json=body, headers=headers)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r2.json()["duplicated"] is True
    # 只发了一次
    assert len(fake_client.sent) == 1


def test_targets_me_endpoint(fake_client):
    _bind_user("openid_alice", "ant_alice", name="user_alice")
    client = _make_client()
    resp = client.get(
        "/v1/targets/me",
        headers={"Authorization": "Bearer ant_alice"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["user_openid"] == "openid_alice"
    assert body["name"] == "user_alice"
    assert body["enabled"] is True
    # token 明文绝不能出现在响应里
    assert "ant_alice" not in resp.text
