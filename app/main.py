"""FastAPI 应用入口。

使用新版 lifespan 上下文管理器代替已弃用的 on_event。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import router
from app.db import init_db
from app.qq_client import close_qq_client_singleton, get_qq_client


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _setup_logging()
    logger = logging.getLogger(__name__)

    # 启动：建表、预热全局 QQ 客户端
    init_db()
    get_qq_client()  # 触发单例创建
    logger.info("agent-notifier api-server started")

    try:
        yield
    finally:
        # 关闭：释放 httpx 客户端
        await close_qq_client_singleton()
        logger.info("agent-notifier api-server stopped")


app = FastAPI(title="Agent Notifier", lifespan=lifespan)
app.include_router(router)
