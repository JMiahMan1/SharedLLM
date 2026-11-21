#!/bin/bash
# Remove the teacher-trace sync agent. Keeps the config, the state and the logs.
set -euo pipefail
LABEL="com.sharedllm.teacher-sync"
LAUNCHCTL="${LAUNCHCTL:-launchctl}"
AGENTS_DIR="${LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
PLIST="$AGENTS_DIR/$LABEL.plist"
"$LAUNCHCTL" bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
if [ -f "$PLIST" ]; then
  rm -f "$PLIST"
  echo "removed    $PLIST"
else
  echo "not installed ($PLIST does not exist)"
fi
