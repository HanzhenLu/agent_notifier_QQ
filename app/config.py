"""应用配置。

通过 pydantic-settings 从环境变量或 .env 文件加载配置。
"""

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置。"""

    # QQ Bot 凭证
    qq_app_id: str
    qq_app_secret: str
    qq_api_base: str = "https://sandbox.api.sgroup.qq.com"
    # 默认通知目标 openid，若为空则查 SQLite 中 name='me' 的目标
    qq_target_openid: Optional[str] = None

    # 鉴权
    agent_notify_token: str
    bind_secret: str

    # SQLite
    db_path: str = "./data/agent_notifier.db"

    # API server
    host: str = "0.0.0.0"
    port: int = 8000

    # 消息行为
    max_message_length: int = 1800

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """获取全局配置单例。"""
    return Settings()  # type: ignore[call-arg]
