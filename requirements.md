# 开发任务书：Agent 结束后通过 QQ 机器人沙箱私聊通知

## 0. 项目目标

实现一个云端后端服务 `agent-notifier`，用于在多个本地或远程 agent 任务结束时，自动向指定 QQ 账号发送私聊通知。

目标效果：

1. 每个 agent 结束时，调用云端 HTTP 接口。
2. 云端后端验证调用方身份。
3. 云端后端记录通知事件。
4. 云端后端通过 QQ 机器人沙箱环境向我的 QQ 单聊发送消息。
5. 支持通过 QQ 给机器人发送 `/bind <secret>` 自动绑定我的 `user_openid`。
6. agent 端提供一个 shell wrapper，能够包裹任意命令并在命令结束后自动通知。

本项目只实现 QQ 沙箱环境，不实现正式环境。

---

## 1. 技术选型

使用 Python 实现。

后端技术栈：

* Python 3.12+
* FastAPI
* Uvicorn
* httpx
* websockets
* pydantic
* SQLite

项目需要包含两个运行入口：

1. `api-server`：HTTP 后端，接收 agent 通知并调用 QQ OpenAPI 发送消息。
2. `gateway-worker`：QQ WebSocket 监听器，用于监听用户给机器人的 `/bind` 消息，并保存 `user_openid`。

MVP 阶段也可以只运行 `api-server`，前提是手动配置了 `QQ_TARGET_OPENID`。

---

## 2. 关键背景和接口约定

QQ 机器人不能直接用 QQ 号发私聊，需要使用 `user_openid`。

单聊发送接口：

```http
POST /v2/users/{openid}/messages
Host: sandbox.api.sgroup.qq.com
Authorization: QQBot <ACCESS_TOKEN>
Content-Type: application/json
```

请求体发送普通文本消息：

```json
{
  "content": "消息内容",
  "msg_type": 0
}
```

AccessToken 获取接口：

```http
POST https://bots.qq.com/app/getAppAccessToken
Content-Type: application/json
```

请求体：

```json
{
  "appId": "<QQ_APP_ID>",
  "clientSecret": "<QQ_APP_SECRET>"
}
```

返回体：

```json
{
  "access_token": "<ACCESS_TOKEN>",
  "expires_in": "7200"
}
```

AccessToken 缓存策略：

* 后端内存缓存 access_token。
* `expires_in` 通常约 7200 秒。
* 到期前 60 秒刷新。
* token 获取失败时，接口返回 502，并记录错误。

QQ 沙箱 OpenAPI Base URL：

```text
https://sandbox.api.sgroup.qq.com
```

QQ WebSocket 网关地址获取接口：

```http
GET /gateway
Host: sandbox.api.sgroup.qq.com
Authorization: QQBot <ACCESS_TOKEN>
```

返回：

```json
{
  "url": "wss://sandbox.api.sgroup.qq.com/websocket/"
}
```

WebSocket 鉴权流程：

1. 连接 WSS 地址。
2. 服务端返回：

```json
{
  "op": 10,
  "d": {
    "heartbeat_interval": 45000
  }
}
```

3. 客户端发送 Identify：

```json
{
  "op": 2,
  "d": {
    "token": "QQBot <ACCESS_TOKEN>",
    "intents": 33554432,
    "shard": [0, 1],
    "properties": {
      "$os": "linux",
      "$browser": "agent-notifier",
      "$device": "agent-notifier"
    }
  }
}
```

这里 `33554432` 等于 `1 << 25`，用于接收单聊消息事件 `C2C_MESSAGE_CREATE`。

4. 之后按 `heartbeat_interval` 周期发送心跳：

```json
{
  "op": 1,
  "d": <last_seq_or_null>
}
```

其中 `last_seq_or_null` 是最近一次收到的 payload 里的 `s` 字段。如果还没有收到任何 `s`，则传 `null`。

用户给机器人发单聊消息时，收到的事件大致如下：

```json
{
  "op": 0,
  "s": 123,
  "t": "C2C_MESSAGE_CREATE",
  "d": {
    "author": {
      "user_openid": "USER_OPENID_HERE"
    },
    "content": "/bind abc123",
    "id": "MESSAGE_ID",
    "timestamp": "2026-06-16T10:00:00+08:00"
  }
}
```

绑定逻辑：

* 如果 `content` 等于 `/bind <BIND_SECRET>`，则把 `author.user_openid` 保存为默认通知目标。
* 保存成功后，调用 QQ 单聊发送接口给该用户回复“绑定成功”。

---

## 3. QQ 管理后台需要手动完成的前置条件

编码 agent 不需要自动化这些步骤，但 README 中必须写清楚。

手动步骤：

1. 在 QQ 开放平台创建机器人。
2. 获取 `AppID` 和 `AppSecret`。
3. 进入沙箱配置。
4. 配置“消息列表单聊”沙箱账号，把我的 QQ 号加入沙箱。
5. 将测试机器人添加到我的 QQ 消息列表单聊。
6. 确认机器人可以在 QQ 私聊窗口收到我发送的消息。
7. 如果消息内容包含 URL，需要在 QQ 后台配置消息 URL 白名单。默认通知内容不要包含 URL。

---

## 4. 项目目录结构

实现如下目录结构：

```text
agent-notifier/
  README.md
  .env.example
  requirements.txt
  Dockerfile
  docker-compose.yml

  app/
    __init__.py
    main.py
    config.py
    db.py
    models.py
    qq_client.py
    formatter.py
    auth.py
    api.py
    gateway_worker.py

  scripts/
    run-with-notify.sh
    notify-agent-done.sh

  tests/
    test_formatter.py
    test_auth.py
```

---

## 5. 环境变量设计

`.env.example` 内容：

```bash
# QQ Bot credentials
QQ_APP_ID=
QQ_APP_SECRET=

# QQ sandbox API base
QQ_API_BASE=https://sandbox.api.sgroup.qq.com

# If set, this openid is used as default target.
# If empty, use the default target stored in SQLite by /bind.
QQ_TARGET_OPENID=

# Secret used by agents to call this service
AGENT_NOTIFY_TOKEN=

# Secret used by QQ /bind command
BIND_SECRET=

# SQLite db path
DB_PATH=/data/agent_notifier.db

# API server
HOST=0.0.0.0
PORT=8000

# Message behavior
MAX_MESSAGE_LENGTH=1800
```

生成密钥命令：

```bash
openssl rand -hex 32
```

---

## 6. 数据库设计

使用 SQLite。

启动时自动建表。

表 1：通知目标表 `targets`

```sql
CREATE TABLE IF NOT EXISTS targets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    user_openid TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

约定：

* 默认目标名为 `me`。
* `/bind` 成功后，将 `name='me'` 的记录 upsert。
* 如果 `QQ_TARGET_OPENID` 环境变量存在，优先使用环境变量。
* 如果环境变量不存在，则读取数据库中 `name='me' AND enabled=1` 的记录。

表 2：通知事件表 `notify_events`

```sql
CREATE TABLE IF NOT EXISTS notify_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    project TEXT NOT NULL,
    agent TEXT NOT NULL,
    status TEXT NOT NULL,
    exit_code INTEGER,
    duration_sec INTEGER,
    cwd TEXT,
    host TEXT,
    branch TEXT,
    commit_id TEXT,
    summary TEXT,
    log_tail TEXT,
    target_openid TEXT,
    qq_message_id TEXT,
    send_status TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL
);
```

`send_status` 取值：

```text
sent
failed
duplicated
```

表 3：QQ 入站消息表 `inbound_messages`

```sql
CREATE TABLE IF NOT EXISTS inbound_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    qq_msg_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    user_openid TEXT,
    content TEXT,
    raw_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

用于 WebSocket 收到事件时去重和排查问题。

---

## 7. HTTP API 设计

### 7.1 健康检查

```http
GET /health
```

返回：

```json
{
  "ok": true
}
```

### 7.2 测试发送 QQ 消息

```http
POST /v1/notify/test
Authorization: Bearer <AGENT_NOTIFY_TOKEN>
Content-Type: application/json
```

请求体可选：

```json
{
  "content": "测试消息"
}
```

如果没有 content，默认发送：

```text
✅ agent-notifier 测试消息发送成功
```

返回：

```json
{
  "ok": true,
  "target": "me",
  "qq_response": {
    "id": "...",
    "timestamp": 1234567890
  }
}
```

### 7.3 agent 完成通知

```http
POST /v1/notify/agent-done
Authorization: Bearer <AGENT_NOTIFY_TOKEN>
Content-Type: application/json
```

请求体：

```json
{
  "event_id": "voltron-server-2-20260616120000-12345",
  "project": "voltron",
  "agent": "codex-voltron",
  "status": "success",
  "exit_code": 0,
  "duration_sec": 2538,
  "cwd": "/home/ubuntu/voltron",
  "host": "server-2",
  "branch": "main",
  "commit": "a1b2c3d",
  "summary": "修复代理配置问题，并完成测试。",
  "log_tail": "optional last lines"
}
```

字段约束：

* `event_id`：必填，最大 200 字符，用于去重。
* `project`：必填，最大 100 字符。
* `agent`：默认 `unknown`，最大 100 字符。
* `status`：必填，只允许：

  * `success`
  * `failed`
  * `cancelled`
  * `timeout`
  * `unknown`
* `exit_code`：可选整数。
* `duration_sec`：可选整数。
* `cwd`：可选，最大 500 字符。
* `host`：可选，最大 100 字符。
* `branch`：可选，最大 100 字符。
* `commit`：可选，最大 100 字符。
* `summary`：可选，最大 1200 字符。
* `log_tail`：可选，最大 3000 字符，不默认发送到 QQ，只落库。

成功返回：

```json
{
  "ok": true,
  "duplicated": false,
  "qq_response": {
    "id": "...",
    "timestamp": 1234567890
  }
}
```

重复事件返回：

```json
{
  "ok": true,
  "duplicated": true,
  "message": "event already processed"
}
```

发送失败返回 HTTP 502：

```json
{
  "detail": "QQ send failed: ..."
}
```

### 7.4 查询最近通知事件

```http
GET /v1/events?limit=20
Authorization: Bearer <AGENT_NOTIFY_TOKEN>
```

返回最近事件，按 `created_at DESC` 排序。

### 7.5 查询当前绑定目标

```http
GET /v1/targets
Authorization: Bearer <AGENT_NOTIFY_TOKEN>
```

返回：

```json
{
  "targets": [
    {
      "name": "me",
      "user_openid": "...",
      "enabled": true,
      "created_at": "...",
      "updated_at": "..."
    }
  ],
  "env_target_configured": true
}
```

---

## 8. 消息格式

实现 `formatter.py`。

函数：

```python
def format_agent_done_message(req: AgentDoneReq, max_len: int = 1800) -> str:
    ...
```

格式：

```text
✅ Agent 结束

项目：voltron
Agent：codex-voltron
状态：success
退出码：0
耗时：42m18s
主机：server-2
路径：/home/ubuntu/voltron
分支：main
提交：a1b2c3d

摘要：
修复代理配置问题，并完成测试。
```

状态图标：

```python
{
    "success": "✅",
    "failed": "❌",
    "cancelled": "⚠️",
    "timeout": "⏰",
    "unknown": "ℹ️",
}
```

耗时格式：

* `65` -> `1m5s`
* `3605` -> `1h0m5s`
* `None` -> `unknown`

消息长度：

* 默认最大 1800 字符。
* 超出时截断并追加：

```text
...（消息过长，已截断）
```

注意：

* 默认不要把 `log_tail` 加进 QQ 消息，避免消息过长。
* `log_tail` 只入库。
* 消息内容默认不要包含 URL。

---

## 9. 核心模块实现要求

### 9.1 config.py

实现 `Settings`：

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    qq_app_id: str
    qq_app_secret: str
    qq_api_base: str = "https://sandbox.api.sgroup.qq.com"
    qq_target_openid: str | None = None

    agent_notify_token: str
    bind_secret: str

    db_path: str = "/data/agent_notifier.db"
    host: str = "0.0.0.0"
    port: int = 8000
    max_message_length: int = 1800

    class Config:
        env_file = ".env"
        extra = "ignore"
```

注意：

* 如果使用 pydantic v2，需要依赖 `pydantic-settings`。
* `QQ_TARGET_OPENID` 可以为空。
* `BIND_SECRET` 必须配置。

### 9.2 models.py

定义 Pydantic 模型：

```python
from pydantic import BaseModel, Field
from typing import Optional, Literal

class AgentDoneReq(BaseModel):
    event_id: str = Field(..., max_length=200)
    project: str = Field(..., max_length=100)
    agent: str = Field(default="unknown", max_length=100)
    status: Literal["success", "failed", "cancelled", "timeout", "unknown"]
    exit_code: Optional[int] = None
    duration_sec: Optional[int] = None
    cwd: Optional[str] = Field(default=None, max_length=500)
    host: Optional[str] = Field(default=None, max_length=100)
    branch: Optional[str] = Field(default=None, max_length=100)
    commit: Optional[str] = Field(default=None, max_length=100)
    summary: Optional[str] = Field(default=None, max_length=1200)
    log_tail: Optional[str] = Field(default=None, max_length=3000)

class TestNotifyReq(BaseModel):
    content: Optional[str] = Field(default=None, max_length=1800)
```

### 9.3 db.py

使用标准库 `sqlite3`。

必须实现：

```python
def init_db() -> None
def event_exists(event_id: str) -> bool
def insert_notify_event(...)
def list_events(limit: int = 20) -> list[dict]
def upsert_target(name: str, user_openid: str) -> None
def get_target(name: str = "me") -> dict | None
def list_targets() -> list[dict]
def insert_inbound_message(...)
```

要求：

* SQLite 连接使用 `row_factory = sqlite3.Row`。
* 所有时间使用 UTC ISO 字符串。
* `event_id` 重复时不要抛出未处理异常。
* 入站消息 `qq_msg_id` 重复时忽略。

### 9.4 auth.py

实现 HTTP 鉴权：

```python
from fastapi import Header, HTTPException

def require_agent_auth(authorization: str = Header(default="")):
    expected = f"Bearer {settings.agent_notify_token}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="invalid token")
```

### 9.5 qq_client.py

实现 QQ OpenAPI 客户端。

类设计：

```python
class QQClient:
    def __init__(self, app_id: str, app_secret: str, api_base: str):
        ...

    async def get_access_token(self) -> str:
        ...

    async def request(self, method: str, path: str, **kwargs) -> dict:
        ...

    async def send_c2c_message(
        self,
        user_openid: str,
        content: str,
        msg_id: str | None = None,
        msg_seq: int | None = None,
        event_id: str | None = None,
    ) -> dict:
        ...

    async def get_gateway_url(self) -> str:
        ...
```

`get_access_token`：

* URL 固定为 `https://bots.qq.com/app/getAppAccessToken`。
* 请求体：

```json
{
  "appId": "...",
  "clientSecret": "..."
}
```

* 缓存 token。
* 到期前 60 秒刷新。
* 如果响应里没有 `access_token`，抛出异常。

`request`：

* 所有 QQ API 都使用 `settings.qq_api_base`。
* header：

```python
{
    "Authorization": f"QQBot {token}",
    "Content-Type": "application/json",
}
```

* timeout 至少 10 秒。
* 如果状态码 >= 400，抛出异常，异常信息包含状态码和响应体。
* QQ API 返回 JSON 则返回 dict；如果返回空，返回 `{}`。

`send_c2c_message`：

* path：

```python
f"/v2/users/{user_openid}/messages"
```

* 普通主动消息请求体：

```python
{
    "content": content,
    "msg_type": 0
}
```

* 如果传入 `msg_id`，则加入：

```python
{
    "msg_id": msg_id,
    "msg_seq": msg_seq or 1
}
```

用于被动回复 `/bind` 消息，避免重复回复。

`get_gateway_url`：

* GET `/gateway`
* 返回响应体里的 `url`

### 9.6 api.py

实现 FastAPI router。

路由：

```python
GET /health
POST /v1/notify/test
POST /v1/notify/agent-done
GET /v1/events
GET /v1/targets
```

`POST /v1/notify/agent-done` 逻辑：

1. 校验 Bearer token。
2. 校验请求体。
3. 检查 `event_id` 是否已存在。
4. 如果已存在，返回 duplicated。
5. 获取目标 openid：

   * 优先 `settings.qq_target_openid`
   * 否则数据库 `targets.name='me'`
   * 都没有则返回 400：`target openid is not configured; send "/bind <BIND_SECRET>" to bot first or set QQ_TARGET_OPENID`
6. 格式化消息。
7. 调用 `qq_client.send_c2c_message`。
8. 发送成功后写入 `notify_events`，`send_status='sent'`。
9. 发送失败也写入 `notify_events`，`send_status='failed'` 和 `error`。
10. 失败返回 502。

### 9.7 main.py

创建 FastAPI app。

```python
from fastapi import FastAPI
from app.db import init_db
from app.api import router

app = FastAPI(title="Agent Notifier")

@app.on_event("startup")
def startup():
    init_db()

app.include_router(router)
```

---

## 10. gateway_worker.py 实现要求

`gateway_worker.py` 是一个独立进程。

启动命令：

```bash
python -m app.gateway_worker
```

功能：

1. 启动时初始化数据库。
2. 获取 QQ AccessToken。
3. 调用 `/gateway` 获取 WSS URL。
4. 连接 WebSocket。
5. 收到 `op=10` 后，读取 `heartbeat_interval`，启动心跳任务。
6. 发送 Identify。
7. 循环接收消息。
8. 如果收到 `op=0`，保存最新 `s` 到内存变量 `last_seq`。
9. 如果事件 `t == "C2C_MESSAGE_CREATE"`：

   * 读取 `d.id`
   * 读取 `d.content`
   * 读取 `d.author.user_openid`
   * 把原始 JSON 保存到 `inbound_messages`
   * 如果 content 去除空白后等于 `/bind <BIND_SECRET>`：

     * upsert `targets(name='me', user_openid=...)`
     * 给该 openid 发送回复：

       * `✅ 绑定成功，后续 agent 结束通知会发送到当前 QQ。`
       * 发送时带上 `msg_id=d.id` 和 `msg_seq=1`
   * 如果 content 等于 `/whoami`：

     * 回复当前 `user_openid`
   * 如果 content 等于 `/ping`：

     * 回复 `pong`
10. 如果收到 `op=7`，服务端要求重连，主动断开并重新进入连接流程。
11. 如果收到 `op=9`，Identify 或 Resume 无效，重新连接并重新 Identify。
12. 如果连接断开，等待 5 秒后重连。
13. 日志打印：

* gateway url 获取成功
* websocket connected
* READY event
* bind success
* reconnect reason

伪代码：

```python
async def run_forever():
    while True:
        try:
            await connect_and_loop()
        except Exception as e:
            logger.exception("gateway worker crashed: %s", e)
            await asyncio.sleep(5)
```

心跳伪代码：

```python
async def heartbeat_loop(ws, interval_ms):
    while True:
        await asyncio.sleep(interval_ms / 1000)
        await ws.send(json.dumps({"op": 1, "d": last_seq}))
```

Identify payload：

```python
{
  "op": 2,
  "d": {
    "token": f"QQBot {access_token}",
    "intents": 1 << 25,
    "shard": [0, 1],
    "properties": {
      "$os": "linux",
      "$browser": "agent-notifier",
      "$device": "agent-notifier"
    }
  }
}
```

---

## 11. agent 端脚本

### 11.1 scripts/notify-agent-done.sh

用途：手动发送一条通知。

```bash
#!/usr/bin/env bash
set -euo pipefail

: "${AGENT_NOTIFY_URL:?missing AGENT_NOTIFY_URL}"
: "${AGENT_NOTIFY_TOKEN:?missing AGENT_NOTIFY_TOKEN}"

PROJECT="${1:?usage: notify-agent-done.sh <project> <status> [summary]}"
STATUS="${2:?usage: notify-agent-done.sh <project> <status> [summary]}"
SUMMARY="${3:-任务结束}"

HOSTNAME_VALUE="$(hostname)"
EVENT_ID="${PROJECT}-${HOSTNAME_VALUE}-$(date +%Y%m%d%H%M%S)-$$"

curl -fsS -X POST "$AGENT_NOTIFY_URL" \
  -H "Authorization: Bearer $AGENT_NOTIFY_TOKEN" \
  -H "Content-Type: application/json" \
  -d "$(jq -n \
    --arg event_id "$EVENT_ID" \
    --arg project "$PROJECT" \
    --arg agent "${AGENT_NAME:-agent}" \
    --arg status "$STATUS" \
    --arg cwd "$PWD" \
    --arg host "$HOSTNAME_VALUE" \
    --arg summary "$SUMMARY" \
    '{
      event_id: $event_id,
      project: $project,
      agent: $agent,
      status: $status,
      cwd: $cwd,
      host: $host,
      summary: $summary
    }'
  )"
```

### 11.2 scripts/run-with-notify.sh

用途：包裹任意 agent 命令，命令结束后自动通知。

```bash
#!/usr/bin/env bash
set -u

if [ "$#" -lt 2 ]; then
  echo "usage: run-with-notify.sh <project> <command> [args...]"
  exit 2
fi

: "${AGENT_NOTIFY_URL:?missing AGENT_NOTIFY_URL}"
: "${AGENT_NOTIFY_TOKEN:?missing AGENT_NOTIFY_TOKEN}"

PROJECT="$1"
shift

START_TS="$(date +%s)"
HOSTNAME_VALUE="$(hostname)"
CMD_TEXT="$*"

"$@"
EXIT_CODE=$?

END_TS="$(date +%s)"
DURATION=$((END_TS - START_TS))

if [ "$EXIT_CODE" -eq 0 ]; then
  STATUS="success"
else
  STATUS="failed"
fi

EVENT_ID="${PROJECT}-${HOSTNAME_VALUE}-$(date +%Y%m%d%H%M%S)-$$"

BRANCH=""
COMMIT=""
if command -v git >/dev/null 2>&1 && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  BRANCH="$(git branch --show-current 2>/dev/null || true)"
  COMMIT="$(git rev-parse --short HEAD 2>/dev/null || true)"
fi

SUMMARY="命令结束：${CMD_TEXT}"

curl -fsS -X POST "$AGENT_NOTIFY_URL" \
  -H "Authorization: Bearer $AGENT_NOTIFY_TOKEN" \
  -H "Content-Type: application/json" \
  -d "$(jq -n \
    --arg event_id "$EVENT_ID" \
    --arg project "$PROJECT" \
    --arg agent "${AGENT_NAME:-agent}" \
    --arg status "$STATUS" \
    --arg cwd "$PWD" \
    --arg host "$HOSTNAME_VALUE" \
    --arg branch "$BRANCH" \
    --arg commit "$COMMIT" \
    --arg summary "$SUMMARY" \
    --argjson exit_code "$EXIT_CODE" \
    --argjson duration_sec "$DURATION" \
    '{
      event_id: $event_id,
      project: $project,
      agent: $agent,
      status: $status,
      cwd: $cwd,
      host: $host,
      branch: $branch,
      commit: $commit,
      summary: $summary,
      exit_code: $exit_code,
      duration_sec: $duration_sec
    }'
  )" >/dev/null 2>&1 || true

exit "$EXIT_CODE"
```

脚本要求：

* `run-with-notify.sh` 的退出码必须等于被包裹命令的退出码。
* 通知失败不能影响原命令退出。
* 依赖 `jq` 和 `curl`。
* README 里说明安装：

```bash
sudo apt-get install -y jq curl
```

---

## 12. requirements.txt

```text
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
httpx>=0.27.0
pydantic>=2.7.0
pydantic-settings>=2.3.0
websockets>=12.0
```

---

## 13. Dockerfile

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY app /app/app
COPY scripts /app/scripts

RUN chmod +x /app/scripts/*.sh

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## 14. docker-compose.yml

同时运行 API server 和 gateway worker：

```yaml
services:
  agent-notifier-api:
    build: .
    container_name: agent-notifier-api
    restart: unless-stopped
    env_file:
      - .env
    ports:
      - "8000:8000"
    volumes:
      - ./data:/data
    command: ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

  agent-notifier-gateway:
    build: .
    container_name: agent-notifier-gateway
    restart: unless-stopped
    env_file:
      - .env
    volumes:
      - ./data:/data
    command: ["python", "-m", "app.gateway_worker"]
```

注意：

* 两个容器共享 `./data:/data`，因此共享同一个 SQLite 文件。
* SQLite 对这种轻量写入足够使用。
* 如果后续并发很高，再迁移到 PostgreSQL。

---

## 15. README 必须包含的启动步骤

README 里写：

### 15.1 准备 `.env`

```bash
cp .env.example .env
```

编辑：

```bash
QQ_APP_ID=你的AppID
QQ_APP_SECRET=你的AppSecret
QQ_API_BASE=https://sandbox.api.sgroup.qq.com
QQ_TARGET_OPENID=
AGENT_NOTIFY_TOKEN=用openssl生成
BIND_SECRET=用openssl生成
DB_PATH=/data/agent_notifier.db
```

生成 secret：

```bash
openssl rand -hex 32
```

### 15.2 启动服务

```bash
docker compose up -d --build
```

查看日志：

```bash
docker compose logs -f agent-notifier-api
docker compose logs -f agent-notifier-gateway
```

### 15.3 绑定 QQ

在 QQ 私聊窗口给机器人发送：

```text
/bind 你的BIND_SECRET
```

预期机器人回复：

```text
✅ 绑定成功，后续 agent 结束通知会发送到当前 QQ。
```

### 15.4 测试发送

```bash
curl -X POST "http://127.0.0.1:8000/v1/notify/test" \
  -H "Authorization: Bearer $AGENT_NOTIFY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"content":"✅ 测试消息"}'
```

### 15.5 agent 端使用

```bash
export AGENT_NOTIFY_URL="https://你的域名/v1/notify/agent-done"
export AGENT_NOTIFY_TOKEN="你的AGENT_NOTIFY_TOKEN"
export AGENT_NAME="codex-voltron"

./scripts/run-with-notify.sh voltron codex
```

或者：

```bash
./scripts/run-with-notify.sh voltron python run_agent.py
```

---

## 16. Nginx 反向代理建议

如果部署到公网，建议用 HTTPS。

Nginx 示例：

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

使用 certbot 配 HTTPS：

```bash
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d notify.example.com
```

---

## 17. 安全要求

必须实现：

1. 所有 `/v1/*` 管理接口都需要 `Authorization: Bearer <AGENT_NOTIFY_TOKEN>`。
2. QQ `AppSecret` 只存放在云端 `.env`，不要放到 agent 项目里。
3. agent 端只保存 `AGENT_NOTIFY_TOKEN`。
4. `/bind` 必须校验 `BIND_SECRET`，不能收到任何人的消息就绑定。
5. 入站消息要按 `qq_msg_id` 去重。
6. 出站通知按 `event_id` 去重。
7. 默认不发送完整日志，只发送摘要。
8. 所有错误日志不得打印 `QQ_APP_SECRET`、`AGENT_NOTIFY_TOKEN`、`BIND_SECRET` 的原文。

建议实现：

* 日志中对 secret 做 mask。
* 后续可增加 IP 白名单，只允许自己的服务器调用 `/v1/notify/agent-done`。

---

## 18. 测试要求

### 18.1 单元测试

`tests/test_formatter.py`：

* 测试 success 图标。
* 测试 failed 图标。
* 测试 duration 格式。
* 测试超长 summary 会被截断。
* 测试 None 字段不会输出多余空行。

`tests/test_auth.py`：

* 没有 Authorization 返回 401。
* Authorization 错误返回 401。
* Authorization 正确允许通过。

### 18.2 手动验收

验收步骤：

1. `docker compose up -d --build` 成功。
2. `GET /health` 返回 `{"ok": true}`。
3. QQ 给机器人发送 `/ping`，机器人回复 `pong`。
4. QQ 给机器人发送 `/whoami`，机器人回复当前 `user_openid`。
5. QQ 给机器人发送 `/bind <BIND_SECRET>`，机器人回复绑定成功。
6. 调用 `/v1/notify/test`，QQ 收到测试消息。
7. 调用 `/v1/notify/agent-done`，QQ 收到 agent 结束通知。
8. 使用相同 `event_id` 重复调用，第二次返回 `duplicated: true`，QQ 不重复收到消息。
9. 使用 `run-with-notify.sh` 包裹一个成功命令：

```bash
./scripts/run-with-notify.sh demo sleep 1
```

QQ 收到 success 通知。

10. 使用 `run-with-notify.sh` 包裹一个失败命令：

```bash
./scripts/run-with-notify.sh demo bash -c 'exit 3'
```

QQ 收到 failed 通知，并且脚本退出码为 3。

---

## 19. 推荐实现顺序

编码 agent 按以下顺序实现：

1. 创建项目结构。
2. 实现 `config.py`。
3. 实现 `db.py` 并确保启动时自动建表。
4. 实现 `formatter.py` 和单元测试。
5. 实现 `qq_client.py`：

   * token 获取
   * token 缓存
   * send_c2c_message
   * get_gateway_url
6. 实现 `api.py`：

   * health
   * notify/test
   * notify/agent-done
   * events
   * targets
7. 实现 `main.py`。
8. 实现 `gateway_worker.py`：

   * WebSocket 连接
   * Identify
   * heartbeat
   * C2C_MESSAGE_CREATE
   * /bind
   * /ping
   * /whoami
9. 实现 shell scripts。
10. 写 Dockerfile、docker-compose.yml、README。
11. 跑单元测试。
12. 本地启动 API。
13. 配置 QQ 沙箱。
14. 启动 gateway worker。
15. 完成手动验收。

---

## 20. 常见错误和处理方式

### 20.1 发送消息报 openid 错误

原因：

* 使用了 QQ 号，而不是 `user_openid`。
* 使用了其他 bot 获取到的 openid。
* openid 不是当前 bot 沙箱环境下的 openid。

处理：

* 重新在 QQ 私聊里发送 `/bind <BIND_SECRET>`。
* 确保机器人和 QQ 账号在同一个沙箱配置中。

### 20.2 发送消息报鉴权错误

原因：

* `QQ_APP_ID` / `QQ_APP_SECRET` 错误。
* AccessToken 过期但未刷新。
* Header 格式不对。

处理：

* Header 必须是：

```text
Authorization: QQBot <ACCESS_TOKEN>
```

### 20.3 gateway worker 收不到消息

原因：

* 没有把 QQ 账号加入消息列表单聊沙箱。
* 没有把测试机器人添加到消息列表。
* WebSocket Identify 的 intents 没有包含 `1 << 25`。
* gateway worker 没有运行。

处理：

* 检查 `docker compose logs -f agent-notifier-gateway`。
* 给机器人发送 `/ping` 测试。
* 确认 Identify payload 中 `intents` 是 `33554432`。

### 20.4 API 能收到请求但 QQ 没收到

原因：

* 没有绑定 target openid。
* QQ_TARGET_OPENID 配错。
* QQ API 返回错误。
* 消息内容包含 URL，但后台没有配置 URL 白名单。

处理：

* 调用：

```bash
curl -H "Authorization: Bearer $AGENT_NOTIFY_TOKEN" \
  http://127.0.0.1:8000/v1/targets
```

* 查看最近事件：

```bash
curl -H "Authorization: Bearer $AGENT_NOTIFY_TOKEN" \
  http://127.0.0.1:8000/v1/events
```

---

## 21. 最终交付物

编码 agent 最终需要交付：

1. 完整 `agent-notifier` 项目源码。
2. `README.md`，包含部署、绑定、测试、agent 端使用方式。
3. `.env.example`。
4. `Dockerfile`。
5. `docker-compose.yml`。
6. `scripts/run-with-notify.sh`。
7. `scripts/notify-agent-done.sh`。
8. 基础单元测试。
9. 手动验收清单。

项目完成后，我应该可以执行：

```bash
docker compose up -d --build
```

然后在 QQ 里发送：

```text
/bind <BIND_SECRET>
```

接着执行：

```bash
export AGENT_NOTIFY_URL="https://notify.example.com/v1/notify/agent-done"
export AGENT_NOTIFY_TOKEN="<AGENT_NOTIFY_TOKEN>"
export AGENT_NAME="codex-voltron"

./scripts/run-with-notify.sh voltron bash -c 'sleep 3 && exit 0'
```

并在 QQ 私聊里收到 agent 完成通知。
