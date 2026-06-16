"""pytest 全局 fixture。"""

from __future__ import annotations

import os

# 在导入 app 之前设置必要的环境变量，避免 Settings 校验失败。
os.environ.setdefault("QQ_APP_ID", "test_app_id")
os.environ.setdefault("QQ_APP_SECRET", "test_app_secret")
os.environ.setdefault("AGENT_NOTIFY_TOKEN", "test_agent_token")
os.environ.setdefault("BIND_SECRET", "test_bind_secret")
os.environ.setdefault("DB_PATH", "./data/agent_notifier_test.db")
