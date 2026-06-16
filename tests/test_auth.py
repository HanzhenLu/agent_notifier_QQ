"""auth 单元测试（per-user agent_token 模式）。"""

from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app import db
from app.auth import hash_token, require_agent_auth


def _build_app() -> FastAPI:
    app = FastAPI()

    @app.get("/protected")
    def protected(target=Depends(require_agent_auth)):
        return {"ok": True, "user_openid": target["user_openid"]}

    return app


def _bind_user(openid: str, token: str, name: str = "user_test") -> None:
    db.upsert_target_with_token(
        user_openid=openid,
        name=name,
        agent_token_hash=hash_token(token),
        token_prefix=token[:8],
    )


def test_missing_authorization_returns_401():
    app = _build_app()
    client = TestClient(app)
    resp = client.get("/protected")
    assert resp.status_code == 401
    assert "/bind" in resp.json()["detail"]


def test_unbound_token_returns_401():
    app = _build_app()
    client = TestClient(app)
    resp = client.get(
        "/protected",
        headers={"Authorization": "Bearer ant_does_not_exist"},
    )
    assert resp.status_code == 401


def test_correct_token_passes():
    _bind_user("openid_aaa", "ant_token_aaa")
    app = _build_app()
    client = TestClient(app)
    resp = client.get(
        "/protected",
        headers={"Authorization": "Bearer ant_token_aaa"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "user_openid": "openid_aaa"}


def test_authorization_without_bearer_prefix_returns_401():
    _bind_user("openid_bbb", "ant_token_bbb")
    app = _build_app()
    client = TestClient(app)
    resp = client.get(
        "/protected",
        headers={"Authorization": "ant_token_bbb"},  # 缺少 "Bearer "
    )
    assert resp.status_code == 401


def test_disabled_target_token_rejected():
    _bind_user("openid_ccc", "ant_token_ccc")
    db.disable_target_by_openid("openid_ccc")
    app = _build_app()
    client = TestClient(app)
    resp = client.get(
        "/protected",
        headers={"Authorization": "Bearer ant_token_ccc"},
    )
    assert resp.status_code == 401


def test_two_users_isolated():
    _bind_user("openid_a", "ant_token_a")
    _bind_user("openid_b", "ant_token_b")
    app = _build_app()
    client = TestClient(app)

    resp_a = client.get(
        "/protected", headers={"Authorization": "Bearer ant_token_a"}
    )
    resp_b = client.get(
        "/protected", headers={"Authorization": "Bearer ant_token_b"}
    )
    assert resp_a.json()["user_openid"] == "openid_a"
    assert resp_b.json()["user_openid"] == "openid_b"
