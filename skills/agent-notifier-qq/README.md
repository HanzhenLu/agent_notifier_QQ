# agent-notifier-qq · Skill

让支持 skills 的 agent（Claude / Cursor / 其它）在长耗时任务结束后，
**自动把结果通知到你的 QQ**。

> 服务端部署、机器人申请、`/bind` 流程见 [配套服务仓库 README](https://github.com/HanzhenLu/agent-notifier-qq)。

## 安装

skill 以 zip 形式分发。本目录就是 skill 的根，把整个目录打包上传给 agent 即可：

```bash
# 在仓库根目录执行
cd skills && zip -r agent-notifier-qq.zip agent-notifier-qq && cd ..
# 然后在 agent 的 skills 设置里上传 skills/agent-notifier-qq.zip
```

## 配置（一次配好）

skill 通过**环境变量**读取你的服务地址和 token，**不依赖任何配置文件**。
任选下面一种方式：

- **A. agent / capsule 的 secrets 设置**（推荐，token 不落盘）：
  在 agent 的设置面板里添加：
  - `AGENT_NOTIFY_URL` = `http://<your-host>:8000/v1/notify/agent-done`
  - `AGENT_TOKEN` = `ant_xxxxxxxxxxxxxxxx`（在 QQ 给机器人发 `/bind <别名>` 拿到）
  - `AGENT_NAME` = `claude` _(可选)_
- **B. 终端 `export`**（仅本次会话有效）：
  ```bash
  export AGENT_NOTIFY_URL='http://your-host:8000/v1/notify/agent-done'
  export AGENT_TOKEN='ant_xxxxxxxxxxxxxxxx'
  ```
- **C. 写进 `~/.bashrc` / `~/.zshrc`**（持久化，单机适用）。

> ⚠️ **不要**把 token 写进任何会被 git 跟踪的文件。

## 自检

不依赖 agent，手动跑一遍，QQ 应当收到一条消息：

```bash
# 假设 skill 解压在 ~/.claude/skills/agent-notifier-qq
bash ~/.claude/skills/agent-notifier-qq/notify-agent-done.sh selftest success "skill 自检 ✅"
```

QQ 没收到？常见原因：
- `AGENT_NOTIFY_URL` 没带完整路径（少了 `/v1/notify/agent-done`）
- token 输错了，或者还没 `/bind` 过
- 服务端没起来 / 网络不通

## agent 怎么用

把这个 zip 上传给 agent 后，agent 会在你说出"结束后通知我"之类的话时
自动加载 [SKILL.md](./SKILL.md)，按里面的指令调用 `notify-agent-done.sh`。

## 文件清单

```
agent-notifier-qq/                  ← zip 解压后顶层目录
├── SKILL.md                        # 给 agent 看的指令（触发词、调用方式、字段说明）
├── README.md                       # 给人看的（你正在读）
└── notify-agent-done.sh            # 实际发请求的脚本，从环境变量读配置
```
