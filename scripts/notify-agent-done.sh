#!/usr/bin/env bash
# 用途：手动发送一条 agent 完成通知。
set -euo pipefail

: "${AGENT_NOTIFY_URL:?missing AGENT_NOTIFY_URL}"
# 兼容旧变量名 AGENT_NOTIFY_TOKEN
: "${AGENT_TOKEN:=${AGENT_NOTIFY_TOKEN:-}}"
: "${AGENT_TOKEN:?missing AGENT_TOKEN (the per-user token from /bind reply)}"

PROJECT="${1:?usage: notify-agent-done.sh <project> <status> [summary]}"
STATUS="${2:?usage: notify-agent-done.sh <project> <status> [summary]}"
SUMMARY="${3:-任务结束}"

HOSTNAME_VALUE="$(hostname)"
EVENT_ID="${PROJECT}-${HOSTNAME_VALUE}-$(date +%Y%m%d%H%M%S)-$$"

curl -fsS -X POST "$AGENT_NOTIFY_URL" \
  -H "Authorization: Bearer $AGENT_TOKEN" \
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
