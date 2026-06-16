"""应用配置。

通过 pydantic-settings 从环境变量或 .env 文件加载配置。
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置。"""

    # QQ Bot 凭证
    qq_app_id: str
    qq_app_secret: str
    qq_api_base: str = "https://sandbox.api.sgroup.qq.com"

    # /bind 命令使用的共享邀请密钥（任何持有此密钥的 QQ 用户都可注册）
    bind_secret: str

    # 生成 agent_token 时使用的可读前缀（便于在日志/日志扫描中识别）
    token_prefix: str = "ant_"

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
