FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
# 1) 通过 ARG 让用户在 build 时可选切换 pip 镜像源（默认走清华源，国内构建更稳定）
#    海外/企业内网构建可：docker build --build-arg PIP_INDEX_URL=https://pypi.org/simple ...
# 2) 禁用 rich 进度条，避免老 Docker (19.03) 默认 pids cgroup limit 过低
#    触发 "RuntimeError: can't start new thread"
# 3) 调高超时并增加重试，缓解偶发的网络抖动
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn
RUN pip install --no-cache-dir --progress-bar off \
        --index-url "${PIP_INDEX_URL}" \
        --trusted-host "${PIP_TRUSTED_HOST}" \
        --timeout 120 --retries 5 \
        -r requirements.txt

COPY app /app/app

# 创建数据目录（默认 DB_PATH=/data/agent_notifier.db，由 compose 注入）
# 并确保非 root 用户对 /app /data 都可写
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /app /data

USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--loop", "asyncio", "--http", "h11"]
