---
name: agent-notifier-qq
description: |
  在长耗时任务（构建、训练、部署、批量运行、CI 等）结束后，通过 agent-notifier-qq
  服务把结果推送到用户的 QQ。当用户表达 "任务完成后通知我"、"跑完 ping 我一下"、
  "结束后发消息到 QQ"、"后台跑请告诉我结果"、"long-running task notify me when done"
  等意图时触发。也可在你（agent）即将结束一段较长、需要用户回到电脑前查看结果的
  对话/任务时主动建议使用本 skill。
---

# agent-notifier-qq · agent 完成通知

把"任务结束"事件 POST 给一个自部署的 HTTP 服务，由它转发到用户的 QQ。
本 skill 只负责**正确发出请求**，不负责服务端部署（部署见仓库根 README）。

---

## 1. 何时使用

**应该用**：
- 用户明确说"结束后通知我 / ping 我 / 发 QQ"。
- 你（agent）正在执行预计耗时 > 1 分钟的命令，且用户大概率不会盯着终端。
- 一轮多步骤任务的**最终一步**完成，需要给用户一个明确收尾信号。

**不应该用**：
- 任务只有几秒钟，用户就在屏幕前。
- 用户没有配置过 `.env`（见第 2 步）。此时应先提示用户配置，而不是直接调用。
- 任务**中途**的进度上报。本 skill 是**结束信号**，不是进度通道。每个 `event_id`
  只应发一次。

---

## 2. 前置条件（用户首次使用必须做）

让用户做以下两件事，缺一不可：

1. 部署或拿到一个 agent-notifier-qq 服务地址（参考主仓库 README）。
2. 在 QQ 里给那个机器人发 `/bind <一个英文别名>`，机器人会回复一个
   `ant_xxxxx` 形式的 token。

然后让用户**复制 `.env.example` 为 `.env` 并填入两个变量**：

```bash
cp skills/agent-notifier-qq/.env.example skills/agent-notifier-qq/.env
# 然后用编辑器填入 AGENT_NOTIFY_URL 和 AGENT_TOKEN
```

`.env` 已被 `.gitignore` 忽略，不会被提交。

---

## 3. 调用方式（推荐，最稳）

**直接调用仓库根目录已有的脚本**，不要让 agent 现场拼 curl。脚本会自动生成
`event_id`、填好 hostname/cwd、做错误处理。

```bash
set -a
source skills/agent-notifier-qq/.env
set +a
bash scripts/notify-agent-done.sh <project> <status> "<summary>"
```

参数说明：

| 位置参数 | 必填 | 说明 |
|---|---|---|
| `<project>` | ✅ | 当前项目/任务名，建议用仓库名或一个稳定短名（英文/拼音皆可），如 `voltron`、`my-blog-build` |
| `<status>` | ✅ | 必须是 `success` / `failed` / `cancelled` / `timeout` / `unknown` 之一 |
| `<summary>` | ❌ | 一句话结果，最长 1200 字。不传则默认 "任务结束" |

**强烈建议**：在自己的命令前后包裹，根据真实退出码决定 status：

```bash
set -a
source skills/agent-notifier-qq/.env
set +a

# ↓↓↓ 你的实际任务命令 ↓↓↓
your-real-command --foo --bar
EXIT_CODE=$?
# ↑↑↑ 你的实际任务命令 ↑↑↑

if [ "$EXIT_CODE" -eq 0 ]; then
  bash scripts/notify-agent-done.sh "my-project" success "任务完成 ✅"
else
  bash scripts/notify-agent-done.sh "my-project" failed "任务失败，退出码 $EXIT_CODE"
fi
```

如果想"跑命令 + 自动通知"一行搞定，仓库里还有 `scripts/run-with-notify.sh`：

```bash
set -a; source skills/agent-notifier-qq/.env; set +a
bash scripts/run-with-notify.sh my-project -- pytest -x
# 它会按 pytest 的真实退出码自动决定 success/failed
```

---

## 4. 直接 curl（脚本不可用时的兜底）

只在没有 `bash`/`jq`/`curl` 任一工具的极端环境下才需要。**优先用第 3 步的脚本**。

```bash
curl -fsS -X POST "$AGENT_NOTIFY_URL" \
  -H "Authorization: Bearer $AGENT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "event_id": "voltron-myhost-20260616233000-12345",
    "project":  "voltron",
    "agent":    "claude",
    "status":   "success",
    "summary":  "本轮任务完成 ✅"
  }'
```

`event_id` **必须每次唯一**（服务端用它去重）。建议格式：
`<project>-<host>-<UTC时间戳>-<pid或随机串>`。

---

## 5. 字段速查（对应 `app/models.py::AgentDoneReq`）

| 字段 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `event_id` | ✅ | str ≤200 | 唯一事件 ID，重复发同一个会被去重 |
| `project` | ✅ | str ≤100 | 项目/任务名 |
| `agent` | ❌ | str ≤100 | agent 名字，默认 `"unknown"`，建议传 `"claude"`/`"cursor"` 等 |
| `status` | ✅ | enum | `success` / `failed` / `cancelled` / `timeout` / `unknown` |
| `exit_code` | ❌ | int | 命令退出码 |
| `duration_sec` | ❌ | int | 总耗时（秒） |
| `cwd` | ❌ | str ≤500 | 工作目录 |
| `host` | ❌ | str ≤100 | 主机名 |
| `branch` | ❌ | str ≤100 | git 分支 |
| `commit` | ❌ | str ≤100 | git 短 commit |
| `summary` | ❌ | str ≤1200 | 一句话结果（出现在 QQ 消息正文显眼处） |
| `log_tail` | ❌ | str ≤3000 | 末尾日志，方便用户在 QQ 直接看到错误 |

`status=failed` 时**强烈建议**带上 `exit_code` 和 `log_tail`，用户在手机上就能定位问题。

---

## 6. 错误处理

| 现象 | 含义 | 怎么办 |
|---|---|---|
| HTTP `401 invalid token` | `.env` 里 token 错了或机器人没绑定 | 让用户重新 `/bind` 拿新 token |
| HTTP `429` | 触发限流 | 不要重试，本次跳过，下次少发 |
| `curl: (7) Failed to connect` | 服务不可达 | 检查 `AGENT_NOTIFY_URL` 是否带了 `/v1/notify/agent-done` 完整路径 |
| 脚本报 `missing AGENT_NOTIFY_URL` | `.env` 没 source 进来 | 用 `set -a; source .env; set +a` 形式 |
| 同一个 `event_id` 发了两次 | 服务端会去重，QQ 只收到一条 | 这是正确行为，别为了"补发"硬改 ID 绕过 |

通知失败**不应该让主任务失败**。`run-with-notify.sh` 已经做了这个处理；如果你
是手写 if/else，请在 `notify-agent-done.sh` 后面加 `|| true`：

```bash
bash scripts/notify-agent-done.sh ... || true
```

---

## 7. 安全注意

- `AGENT_TOKEN` 是**用户私人**的 QQ 推送凭证，泄漏等于别人能给该用户 QQ 发消息。
  不要把 token 打印到日志、不要写进 git、不要 echo 出来。
- `.env` 已被 `.gitignore` 忽略；提交前用 `git status` 确认。
- `summary` / `log_tail` 会原样进入 QQ 消息，**不要**把数据库密码、私钥等放进去。
