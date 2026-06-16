#!/usr/bin/env bash
# 用途：包裹任意 agent 命令，命令结束后自动通知。
# 退出码必须等于被包裹命令的退出码。
set -u

if [ "$#" -lt 2 ]; then
  echo "usage: run-with-notify.sh <project> <command> [args...]"
  exit 2
fi

: "${AGENT_NOTIFY_URL:?missing AGENT_NOTIFY_URL}"
# 兼容旧变量名 AGENT_NOTIFY_TOKEN
: "${AGENT_TOKEN:=${AGENT_NOTIFY_TOKEN:-}}"
: "${AGENT_TOKEN:?missing AGENT_TOKEN (the per-user token from /bind reply)}"

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
  -H "Authorization: Bearer $AGENT_TOKEN" \
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
