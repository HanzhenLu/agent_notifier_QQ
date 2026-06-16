# agent-notifier-qq · Skill

让支持 skills 的 agent（Claude / Cursor / 其它）在长耗时任务结束后，
**自动把结果通知到你的 QQ**。

> 服务端部署、机器人申请、`/bind` 流程见 [仓库根 README](../../README.md)。

## 安装（一次配好）

1. 拷一份配置模板并填入你自己的值：
   ```bash
   cp skills/agent-notifier-qq/.env.example skills/agent-notifier-qq/.env
   ${EDITOR:-vi} skills/agent-notifier-qq/.env
   ```
   需要填两个变量：
   - `AGENT_NOTIFY_URL` —— 你部署的服务地址，**必须包含 `/v1/notify/agent-done` 路径**
   - `AGENT_TOKEN` —— 在 QQ 里对机器人发 `/bind <别名>` 后机器人回复的 `ant_xxx` token

2. 确认 `.env` 不会被提交（仓库 `.gitignore` 已经覆盖）：
   ```bash
   git check-ignore -v skills/agent-notifier-qq/.env
   ```

## 自检

不依赖 agent，手动跑一遍，QQ 应当收到一条消息：

```bash
set -a; source skills/agent-notifier-qq/.env; set +a
bash scripts/notify-agent-done.sh selftest success "skill 自检 ✅"
```

QQ 没收到？常见原因：
- `AGENT_NOTIFY_URL` 没带完整路径（少了 `/v1/notify/agent-done`）
- token 输错了，或者还没 `/bind` 过
- 服务端没起来 / 网络不通

## agent 怎么用

把这个仓库（或单独 copy 出来的 `skills/agent-notifier-qq/` 目录 + `scripts/`
两个 sh）放到 agent 的 skills 搜索路径里。agent 会在你说出"结束后通知我"
之类的话时自动加载 [SKILL.md](./SKILL.md)。

## 文件清单

```
skills/agent-notifier-qq/
├── SKILL.md         # 给 agent 看的指令（触发词、调用方式、字段说明）
├── README.md        # 给人看的（你正在读）
└── .env.example     # 配置模板，复制成 .env 后自己填
```

实际发请求的脚本在仓库根的 [`scripts/`](../../scripts/) 下：
- `notify-agent-done.sh` —— 手动发一条结束通知
- `run-with-notify.sh` —— 包裹任意命令，结束后按真实退出码自动通知
