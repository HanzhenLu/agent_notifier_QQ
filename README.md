# Agent Notifier QQ

一个用于 **agent 任务结束后自动通过 QQ 机器人沙箱私聊通知** 的轻量后端。

> 本项目只支持 QQ 机器人 **沙箱环境**。

## 功能

- 接收 agent 完成事件，主动私聊推送到指定 QQ。
- 支持在 QQ 中通过 `/bind <BIND_SECRET>` 自动绑定接收方 openid。
- 提供 shell wrapper 包裹任意命令，结束后自动通知（成功/失败）。
- SQLite 落库，按 `event_id` 去重。

## 架构

```
agent → POST /v1/notify/agent-done  →  api-server  → QQ 沙箱 OpenAPI → 我的 QQ
                                          │                ▲
                                          │ 共享 SQLite     │
                                          ▼                │
                                       gateway-worker ←────┘
                                       (WSS 监听 /bind /ping /whoami)
```

两个独立进程：

| 进程 | 命令 | 作用 |
| --- | --- | --- |
| api-server | `uvicorn app.main:app` | 接收 agent 通知并主动推送 QQ |
| gateway-worker | `python -m app.gateway_worker` | 长连接 WSS，处理 `/bind` 等命令 |

## 1. QQ 后台准备（手动）

1. 在 QQ 开放平台创建机器人，获取 `AppID` 与 `AppSecret`。
2. 进入沙箱配置，配置 **消息列表单聊** 沙箱账号，把自己的 QQ 加入沙箱。
3. 把测试机器人添加到自己的 QQ 消息列表单聊。
4. 在 QQ 私聊窗口能给机器人发消息。
5. 如需在通知中包含 URL，请在后台配置 URL 白名单（**默认通知不含 URL**）。

## 2. 准备 .env

```bash
cp .env.example .env
```

生成 secret：

```bash
openssl rand -hex 32
```

至少要填：

```bash
QQ_APP_ID=...
QQ_APP_SECRET=...
AGENT_NOTIFY_TOKEN=...     # openssl rand -hex 32
BIND_SECRET=...            # openssl rand -hex 32
# QQ_TARGET_OPENID=        # 可选；为空时使用 /bind 写入 SQLite 的目标
```

`DB_PATH`：

- 本地直接运行：`./data/agent_notifier.db`（默认）
- Docker 部署：由 `docker-compose.yml` 覆盖为 `/data/agent_notifier.db`

## 3. 启动

### 3.1 Docker（推荐）

```bash
# 让容器以宿主当前用户身份运行，避免容器以 root 把 ./data 写成 root 所有
export UID=$(id -u) GID=$(id -g)

docker compose up -d --build
```

> 不 `export UID/GID` 也能跑（默认回落 `1000:1000`），但宿主当前用户 uid 不是 1000 时，
> 容器写出来的 `./data/agent_notifier.db` 会变成另一个用户所有，
> 之后再切回宿主直接运行 `python -m ...` 会报 `attempt to write a readonly database`。

查看日志：

```bash
docker compose logs -f agent-notifier-api
docker compose logs -f agent-notifier-gateway
```

### 3.2 本地直接运行

```bash
pip install -r requirements.txt

# 终端 A
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 终端 B
python -m app.gateway_worker
```

## 4. 绑定 QQ

在 QQ 私聊窗口给机器人发送：

```
/bind <BIND_SECRET>
```

预期回复：

```
✅ 绑定成功，后续 agent 结束通知会发送到当前 QQ。
```

> ❌ 若 secret 不正确，机器人会回复 `❌ 绑定失败：BIND_SECRET 不正确。`，并且服务端不会落库 secret 原文（已 mask）。

## 5. 测试

健康检查：

```bash
curl http://127.0.0.1:8000/health
```

测试发送：

```bash
curl -X POST "http://127.0.0.1:8000/v1/notify/test" \
  -H "Authorization: Bearer $AGENT_NOTIFY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"content":"✅ 测试消息"}'
```

模拟 agent 完成：

```bash
curl -X POST "http://127.0.0.1:8000/v1/notify/agent-done" \
  -H "Authorization: Bearer $AGENT_NOTIFY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "event_id": "demo-1",
    "project": "voltron",
    "agent": "codex-voltron",
    "status": "success",
    "exit_code": 0,
    "duration_sec": 65,
    "summary": "demo"
  }'
```

查询：

```bash
# 当前绑定目标
curl -H "Authorization: Bearer $AGENT_NOTIFY_TOKEN" \
  http://127.0.0.1:8000/v1/targets

# 最近事件
curl -H "Authorization: Bearer $AGENT_NOTIFY_TOKEN" \
  http://127.0.0.1:8000/v1/events
```

## 6. agent 端接入

依赖：

```bash
sudo apt-get install -y jq curl
```

设置环境变量：

```bash
export AGENT_NOTIFY_URL="https://你的域名/v1/notify/agent-done"
export AGENT_NOTIFY_TOKEN="你的AGENT_NOTIFY_TOKEN"
export AGENT_NAME="codex-voltron"
```

包裹一条命令（结束后自动通知，退出码透传）：

```bash
./scripts/run-with-notify.sh voltron python run_agent.py
./scripts/run-with-notify.sh voltron bash -c 'sleep 3 && exit 0'
./scripts/run-with-notify.sh demo bash -c 'exit 3'   # 失败也会通知，脚本退出码=3
```

或直接手动通知：

```bash
./scripts/notify-agent-done.sh voltron success "本轮任务完成"
```

## 7. 安全

- `/v1/*` 全部需要 `Authorization: Bearer $AGENT_NOTIFY_TOKEN`。
- `/bind` 必须正确携带 `BIND_SECRET` 才会写入目标。
- 所有日志/数据库都对 `BIND_SECRET` 做 mask，不记录原文。
- `QQ_APP_SECRET` 仅存于云端 `.env`，不要分发到 agent 机器。
- agent 机器只持有 `AGENT_NOTIFY_TOKEN` 和 `AGENT_NOTIFY_URL`。

## 8. 测试

```bash
pip install -r requirements-dev.txt
pytest -v
```

## 9. 反向代理（可选）

```nginx
server {
    listen 80;
    server_name notify.example.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

```bash
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d notify.example.com
```

## 10. 排错

| 现象 | 检查项 |
| --- | --- |
| QQ 没收到消息 | `GET /v1/targets` 是否有目标；`GET /v1/events` 看 `send_status` 与 `error` |
| gateway-worker 收不到消息 | QQ 沙箱账号是否生效；机器人是否已加好友；`docker compose logs -f agent-notifier-gateway` |
| 鉴权失败 | Header 必须是 `Authorization: QQBot <token>`（QQ 一侧）/ `Bearer <token>`（本服务一侧） |
| 含 URL 不发出 | QQ 后台需要配置 URL 白名单 |
