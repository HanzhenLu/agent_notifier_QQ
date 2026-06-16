"""pytest 全局 fixture。"""

from __future__ import annotations

import os
import tempfile

import pytest


# 在导入 app 之前设置必要的环境变量，避免 Settings 校验失败。
os.environ.setdefault("QQ_APP_ID", "test_app_id")
os.environ.setdefault("QQ_APP_SECRET", "test_app_secret")
os.environ.setdefault("BIND_SECRET", "test_bind_secret")
# DB_PATH 在每个 test 前会被指向独立临时文件，这里只给一个 fallback
os.environ.setdefault("DB_PATH", "./data/agent_notifier_test.db")


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    """每个测试用例使用独立的 SQLite 文件，互不污染。"""
    db_file = tmp_path / "agent_notifier_test.db"
    monkeypatch.setenv("DB_PATH", str(db_file))

    # 清空 Settings lru_cache，让新的 DB_PATH 生效
    from app.config import get_settings
    get_settings.cache_clear()

    from app import db as db_module
    db_module.init_db()

    yield db_file

    get_settings.cache_clear()
