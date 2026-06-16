# Agent Notifier QQ

一个用于 **agent 任务结束后自动通过 QQ 机器人沙箱私聊通知** 的轻量后端，
支持多用户：每个 QQ 用户都通过 `/bind` 拿到独立的 `agent_token`，
彼此互不干扰，自部署一次即可服务团队多人。

> 本项目只支持 QQ 机器人 **沙箱环境**。

## 功能

- 接收 agent 完成事件，主动私聊推送到该 token 所属的 QQ。
- 在 QQ 中通过 `/bind <BIND_SECRET>` 即可注册自己，机器人会私信返回**仅显示一次**的 `agent_token`。
- 每个用户的 token 相互独立；服务端不存明文，只存 sha256。
- 支持 `/unbind` 立即撤销 token、`/whoami` 查看绑定状态。
- 提供 shell wrapper 包裹任意命令，结束后自动通知（成功/失败）。
- SQLite 落库，按 `event_id` 去重；`/v1/events` 仅返回当前 token 所属用户的事件。

## 架构

```
agent → POST /v1/notify/agent-done  →  api-server  → QQ 沙箱 OpenAPI → 该用户的 QQ
        (Bearer <agent_token>)         │                ▲
                                       │ 共享 SQLite     │
                                       ▼                │
                                    gateway-worker ←────┘
                                    (WSS 监听 /bind /unbind /whoami /ping)
```

两个独立进程：

| 进程 | 命令 | 作用 |
| --- | --- | --- |
| api-server | `uvicorn app.main:app` | 接收 agent 通知并主动推送 QQ |
| gateway-worker | `python -m app.gateway_worker` | 长连接 WSS，处理 `/bind` 等命令 |

## 1. QQ 后台准备（手动）

1. 在 QQ 开放平台创建机器人，获取 `AppID` 与 `AppSecret`。
2. 进入沙箱配置，配置 **消息列表单聊** 沙箱账号，把要使用的 QQ 加入沙箱。
3. 把测试机器人添加到这些 QQ 的消息列表单聊。
4. 在 QQ 私聊窗口能给机器人发消息。
5. 如需在通知中包含 URL，请在后台配置 URL 白名单（**默认通知不含 URL**）。

## 2. 准备 .env

```bash
cp .env.example .env
```

生成 `BIND_SECRET`（视同**邀请码**，不要公开）：

```bash
openssl rand -hex 32
```

至少要填：

```bash
QQ_APP_ID=...
QQ_APP_SECRET=...
BIND_SECRET=...            # 任何持有此值的 QQ 用户都可注册自己
```

`DB_PATH`：

- 本地直接运行：`./data/agent_notifier.db`（默认）
- Docker 部署：由 `docker-compose.yml` 覆盖为 `/data/agent_notifier.db`

> 注意：本项目**不再使用全局 `AGENT_NOTIFY_TOKEN` 与 `QQ_TARGET_OPENID`**。
> 每个用户通过 `/bind` 自助获得专属 token；旧版部署需要重新 `/bind`。

## 3. 启动

### 3.1 Docker（推荐）

```bash
# 让容器以宿主当前用户身份运行，避免容器以 root 把 ./data 写成 root 所有
# 注意：bash 中 UID 是只读内建变量，所以这里用 HOST_UID / HOST_GID 作为别名
export HOST_UID=$(id -u) HOST_GID=$(id -g)

docker compose up -d --build
```

> 不 `export HOST_UID/HOST_GID` 也能跑（默认回落 `1000:1000`），但宿主当前用户 uid 不是 1000 时，
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

## 4. 绑定 QQ（每个用户自助）

在 QQ 私聊窗口给机器人发送：

```
/bind <BIND_SECRET>
```

机器人会私信回复（**仅此一次显示**）：

```
✅ 绑定成功。
你的 agent_token（仅此一次显示）：
ant_XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX

请妥善保管：丢失需重新 /bind 重置。
使用方式：
  curl -H "Authorization: Bearer <agent_token>" \
       -X POST <AGENT_NOTIFY_URL>/v1/notify/test
```

把这段 `agent_token` 复制保存，之后 agent 调用 API 时通过 `Authorization: Bearer <agent_token>` 鉴权。

其他命令：

| 命令 | 作用 |
| --- | --- |
| `/bind <BIND_SECRET>` | 首次绑定 / 重置 token（旧 token 立即失效） |
| `/unbind` | 解绑：当前 token 立即失效，机器人不再推送 |
| `/whoami` | 查看自己的绑定状态（不显示 token 明文） |
| `/ping` | 健康检查 |

> ❌ 若 `BIND_SECRET` 不正确，机器人会回复 `❌ 绑定失败：BIND_SECRET 不正确。`，
> 服务端不会落库 secret 原文（已 mask）。

## 5. 测试

健康检查：

```bash
curl http://127.0.0.1:8000/health
```

测试发送（替换为自己的 `agent_token`）：

```bash
export AGENT_TOKEN="ant_xxxx..."

curl -X POST "http://127.0.0.1:8000/v1/notify/test" \
  -H "Authorization: Bearer $AGENT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"content":"✅ 测试消息"}'
```

模拟 agent 完成：

```bash
curl -X POST "http://127.0.0.1:8000/v1/notify/agent-done" \
  -H "Authorization: Bearer $AGENT_TOKEN" \
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
# 当前 token 所属的绑定信息
curl -H "Authorization: Bearer $AGENT_TOKEN" \
  http://127.0.0.1:8000/v1/targets/me

# 当前 token 所属的最近事件
curl -H "Authorization: Bearer $AGENT_TOKEN" \
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
export AGENT_TOKEN="ant_xxxx..."        # 从 /bind 私信中复制
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

> 脚本兼容旧变量名：若已设置 `AGENT_NOTIFY_TOKEN`，脚本会回退使用它。

## 7. 安全

- `/v1/*` 全部需要 `Authorization: Bearer <agent_token>`，token 与 QQ 用户一一对应。
- `BIND_SECRET` 视同**邀请码**，泄露后任何人都能注册自己 → 收到所有该 token 持有者发起的通知（其实只能收到 ta 自己发起的）。**不要公开 BIND_SECRET**。
- 服务端只保存 `sha256(agent_token)`；明文只在 `/bind` 私信中出现一次。
- 所有日志/数据库都对 `BIND_SECRET` 做 mask，不记录原文。
- `QQ_APP_SECRET` 仅存于服务端 `.env`，不分发到 agent 机器。
- agent 机器只持有 `AGENT_TOKEN` 与 `AGENT_NOTIFY_URL`。

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
| QQ 没收到消息 | `/v1/targets/me` 是否 `enabled=true`；`/v1/events` 看 `send_status` 与 `error` |
| 401 invalid or unbound token | 是否拼错 token；是否之前 `/unbind` 过；老库迁移后需要重新 `/bind` |
| gateway-worker 收不到消息 | QQ 沙箱账号是否生效；机器人是否已加好友；`docker compose logs -f agent-notifier-gateway` |
| 鉴权失败 | Header 必须是 `Authorization: Bearer <agent_token>` |
| 含 URL 不发出 | QQ 后台需要配置 URL 白名单 |
