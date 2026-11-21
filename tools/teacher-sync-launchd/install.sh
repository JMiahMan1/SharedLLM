#!/bin/bash
# Install (or update) the launchd agent that syncs teacher traces every 30 minutes.
#
#   tools/teacher-sync-launchd/install.sh --gateway https://<jarvis> --key-file ~/.config/sharedllm/trainer-key
#
# Options: --python <path> (default: python3 on PATH), --interval <seconds> (1800),
#          --names-file <path> (optional extra names; Identity supplies the rest).
#
# Idempotent: running it again rewrites the same files, reloads the agent, and
# says what changed. No secret is written anywhere: the plist and the config
# name the key FILE, which must exist and be readable only by you (chmod 600).
set -euo pipefail

LABEL="com.sharedllm.teacher-sync"
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
LAUNCHCTL="${LAUNCHCTL:-launchctl}"
AGENTS_DIR="${LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
CONFIG_DIR="${TEACHER_CONFIG_DIR:-$HOME/.config/sharedllm}"
LOG_DIR="${TEACHER_LOG_DIR:-$HOME/Library/Logs/sharedllm-teacher}"
STATE_DIR="${TEACHER_STATE_DIR:-$HOME/.local/state/sharedllm-teacher}"

GATEWAY=""
KEY_FILE=""
PYTHON_BIN="$(command -v python3 || true)"
INTERVAL=1800
NAMES_FILE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --gateway) GATEWAY="${2%/}"; shift 2 ;;
    --key-file) KEY_FILE="$2"; shift 2 ;;
    --python) PYTHON_BIN="$2"; shift 2 ;;
    --interval) INTERVAL="$2"; shift 2 ;;
    --names-file) NAMES_FILE="$2"; shift 2 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "install.sh: unknown option $1" >&2; exit 2 ;;
  esac
done

fail() { echo "install.sh: $*" >&2; exit 2; }
[ -n "$GATEWAY" ] || fail "--gateway is required (the Jarvis gateway URL)"
case "$GATEWAY" in https://*|http://*) ;; *) fail "--gateway must be an http(s) URL" ;; esac
[ -n "$KEY_FILE" ] || fail "--key-file is required (a file holding an admin or uploader API key)"
KEY_FILE="$(cd "$(dirname "$KEY_FILE")" && pwd)/$(basename "$KEY_FILE")"
[ -s "$KEY_FILE" ] || fail "the key file $KEY_FILE does not exist or is empty"
[ -n "$PYTHON_BIN" ] && [ -x "$PYTHON_BIN" ] || fail "python3 was not found; pass --python <path>"
"$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || fail "$PYTHON_BIN is older than Python 3.11"
perms="$("$PYTHON_BIN" -c 'import os, sys; print(format(os.stat(sys.argv[1]).st_mode & 0o777, "o"))' "$KEY_FILE")"
case "$perms" in 600|400) ;; *) fail "the key file $KEY_FILE is mode $perms; run: chmod 600 '$KEY_FILE'" ;; esac
case "$INTERVAL" in ''|*[!0-9]*) fail "--interval must be a number of seconds" ;; esac

mkdir -p "$AGENTS_DIR" "$CONFIG_DIR" "$LOG_DIR" "$STATE_DIR"
chmod 700 "$CONFIG_DIR" "$STATE_DIR"

CONFIG="$CONFIG_DIR/teacher-sync.env"
PLIST="$AGENTS_DIR/$LABEL.plist"

new_config="$(cat <<EOF
# Written by tools/teacher-sync-launchd/install.sh. No secrets: the key stays in its file.
TEACHER_REPO='$REPO'
TEACHER_GATEWAY='$GATEWAY'
TEACHER_KEY_FILE='$KEY_FILE'
TEACHER_PYTHON='$PYTHON_BIN'
TEACHER_LOG_DIR='$LOG_DIR'
TEACHER_STATE_DIR='$STATE_DIR'
TEACHER_NAMES_FILE='$NAMES_FILE'
EOF
)"

new_plist="$(sed \
  -e "s#@LABEL@#$LABEL#g" \
  -e "s#@RUN@#$HERE/run.sh#g" \
  -e "s#@CONFIG@#$CONFIG#g" \
  -e "s#@INTERVAL@#$INTERVAL#g" \
  -e "s#@LOG_DIR@#$LOG_DIR#g" \
  "$HERE/$LABEL.plist.in")"

write_if_changed() {  # path, content, mode
  if [ -f "$1" ] && [ "$(cat "$1")" = "$2" ]; then
    echo "unchanged  $1"
  else
    printf '%s\n' "$2" > "$1"
    echo "wrote      $1"
  fi
  chmod "$3" "$1"
}

write_if_changed "$CONFIG" "$new_config" 600
write_if_changed "$PLIST" "$new_plist" 644
chmod 755 "$HERE/run.sh"

if command -v plutil >/dev/null 2>&1; then
  plutil -lint "$PLIST" >/dev/null || fail "the generated plist does not parse: $PLIST"
fi

DOMAIN="gui/$(id -u)"
"$LAUNCHCTL" bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
"$LAUNCHCTL" bootstrap "$DOMAIN" "$PLIST"
echo "loaded     $LABEL every ${INTERVAL}s (and once now)"
echo
echo "Installed the teacher-trace sync:"
echo "  agent    $PLIST"
echo "  config   $CONFIG (no secrets)"
echo "  runs     $HERE/run.sh -> scripts/collect_teacher_traces.py sync --source all, then flush"
echo "  gateway  $GATEWAY"
echo "  key file $KEY_FILE"
echo "  log      $LOG_DIR/sync.log (rotated at 1 MiB, 5 kept)"
echo "  state    $STATE_DIR (names cache, queue, sync marks)"
echo "Status: Lab, Training, Teacher traces. Remove: tools/teacher-sync-launchd/uninstall.sh"
