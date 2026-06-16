"""auth 单元测试。

通过最小 FastAPI app + httpx 直接请求受保护路由验证鉴权。
"""

from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.auth import require_agent_auth
from app.config import get_settings


def _build_app() -> FastAPI:
    app = FastAPI()

    @app.get("/protected", dependencies=[Depends(require_agent_auth)])
    def protected():
        return {"ok": True}

    return app


def test_missing_authorization_returns_401():
    app = _build_app()
    client = TestClient(app)
    resp = client.get("/protected")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid token"


def test_wrong_authorization_returns_401():
    app = _build_app()
    client = TestClient(app)
    resp = client.get(
        "/protected",
        headers={"Authorization": "Bearer wrong_token"},
    )
    assert resp.status_code == 401


def test_correct_authorization_passes():
    app = _build_app()
    client = TestClient(app)
    token = get_settings().agent_notify_token
    resp = client.get(
        "/protected",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_authorization_without_bearer_prefix_returns_401():
    app = _build_app()
    client = TestClient(app)
    token = get_settings().agent_notify_token
    resp = client.get(
        "/protected",
        headers={"Authorization": token},  # 缺少 "Bearer "
    )
    assert resp.status_code == 401
