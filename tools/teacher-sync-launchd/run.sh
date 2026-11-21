#!/bin/bash
# One catch-up pass of the teacher-trace sync, run by launchd every 30 minutes.
#
#   run.sh <config file>
#
# The config file (written by install.sh, no secrets in it) names the SharedLLM
# checkout, the gateway, the key FILE and the python to use. This script syncs
# every opencode and Cline session touched since the last run, flushes the
# upload queue, and appends both results to a log it rotates itself.
set -u

CONFIG="${1:?usage: run.sh <config file>}"
if [ ! -r "$CONFIG" ]; then
  echo "teacher-sync: config $CONFIG is missing; run tools/teacher-sync-launchd/install.sh" >&2
  exit 2
fi
# shellcheck disable=SC1090
. "$CONFIG"

: "${TEACHER_REPO:?TEACHER_REPO is not set in $CONFIG}"
: "${TEACHER_GATEWAY:?TEACHER_GATEWAY is not set in $CONFIG}"
: "${TEACHER_KEY_FILE:?TEACHER_KEY_FILE is not set in $CONFIG}"
PYTHON="${TEACHER_PYTHON:-python3}"
LOG_DIR="${TEACHER_LOG_DIR:-$HOME/Library/Logs/sharedllm-teacher}"
STATE_DIR="${TEACHER_STATE_DIR:-$HOME/.local/state/sharedllm-teacher}"
MAX_LOG_BYTES="${TEACHER_MAX_LOG_BYTES:-1048576}"
KEEP_LOGS="${TEACHER_KEEP_LOGS:-5}"

mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/sync.log"

# Rotate: sync.log -> sync.log.1 -> ... -> sync.log.$KEEP_LOGS (the oldest is dropped).
if [ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt "$MAX_LOG_BYTES" ]; then
  i=$KEEP_LOGS
  while [ "$i" -gt 1 ]; do
    prev=$((i - 1))
    [ -f "$LOG.$prev" ] && mv -f "$LOG.$prev" "$LOG.$i"
    i=$prev
  done
  mv -f "$LOG" "$LOG.1"
fi

stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }
COLLECT="$TEACHER_REPO/scripts/collect_teacher_traces.py"
EXTRA=()
[ -n "${TEACHER_NAMES_FILE:-}" ] && EXTRA+=(--names-file "$TEACHER_NAMES_FILE")

{
  echo "$(stamp) sync start"
  "$PYTHON" "$COLLECT" sync --source all --gateway "$TEACHER_GATEWAY" --key-file "$TEACHER_KEY_FILE" \
    --state-dir "$STATE_DIR" ${EXTRA[@]+"${EXTRA[@]}"} 2>&1
  sync_code=$?
  echo "$(stamp) sync exit $sync_code"
  "$PYTHON" "$COLLECT" flush --gateway "$TEACHER_GATEWAY" --key-file "$TEACHER_KEY_FILE" \
    --state-dir "$STATE_DIR" 2>&1
  flush_code=$?
  echo "$(stamp) flush exit $flush_code"
  echo "$sync_code" > "$LOG_DIR/.last_exit"
} >> "$LOG"

code=$(cat "$LOG_DIR/.last_exit")
# 3 means "queued, will retry": expected while the gateway is away, not an error for launchd.
[ "$code" = "3" ] && exit 0
exit "$code"
