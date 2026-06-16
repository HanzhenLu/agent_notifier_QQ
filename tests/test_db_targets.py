"""targets 表（含 agent_token）相关 DB 测试。"""

from __future__ import annotations

from app import db
from app.auth import hash_token


def _bind(openid: str, token: str, name: str = "user_x") -> None:
    db.upsert_target_with_token(
        user_openid=openid,
        name=name,
        agent_token_hash=hash_token(token),
        token_prefix=token[:8],
    )


def test_upsert_creates_row():
    _bind("openid_1", "ant_t1", name="user_111111")
    target = db.get_target_by_openid("openid_1")
    assert target is not None
    assert target["user_openid"] == "openid_1"
    assert target["name"] == "user_111111"
    assert target["enabled"] == 1


def test_get_by_token_hash():
    _bind("openid_2", "ant_t2")
    found = db.get_target_by_token_hash(hash_token("ant_t2"))
    assert found is not None
    assert found["user_openid"] == "openid_2"

    # 不同 token 查不到
    assert db.get_target_by_token_hash(hash_token("ant_other")) is None


def test_rebind_invalidates_old_token():
    _bind("openid_3", "ant_old")
    _bind("openid_3", "ant_new")  # 同一 openid 第二次绑定

    # 老 token 失效
    assert db.get_target_by_token_hash(hash_token("ant_old")) is None
    # 新 token 生效
    found = db.get_target_by_token_hash(hash_token("ant_new"))
    assert found is not None
    assert found["user_openid"] == "openid_3"


def test_disable_target_invalidates_token():
    _bind("openid_4", "ant_t4")
    affected = db.disable_target_by_openid("openid_4")
    assert affected is True

    # 禁用后 token hash 也已清空，查不到
    assert db.get_target_by_token_hash(hash_token("ant_t4")) is None

    # get_target_by_openid 仍可查到（含 enabled=0），用于 /whoami
    target = db.get_target_by_openid("openid_4")
    assert target is not None
    assert target["enabled"] == 0


def test_disable_unbound_user_returns_false():
    affected = db.disable_target_by_openid("openid_never_bound")
    assert affected is False


def test_two_users_have_unique_token_hash():
    _bind("openid_a", "ant_a", name="user_aaa")
    _bind("openid_b", "ant_b", name="user_bbb")

    a = db.get_target_by_token_hash(hash_token("ant_a"))
    b = db.get_target_by_token_hash(hash_token("ant_b"))
    assert a["user_openid"] == "openid_a"
    assert b["user_openid"] == "openid_b"


def test_touch_last_used_updates_timestamp():
    _bind("openid_t", "ant_t")
    target = db.get_target_by_openid("openid_t")
    assert target["last_used_at"] is None

    db.touch_target_last_used(target["id"])
    after = db.get_target_by_openid("openid_t")
    assert after["last_used_at"] is not None
