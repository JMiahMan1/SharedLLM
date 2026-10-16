#!/usr/bin/env bash
# notify_admins.sh
# Usage: scripts/notify_admins.sh "<title>" "<body>" [priority 1-5] [tag]
#
# Sends one admin notice (category "admin") to every admin's phone, through
# telemetry (docs/PUSH.md). Run it on the server, from any directory: a cron
# job (backups, maintenance), deploy_remote.sh, or by hand.
#
# The request is made from inside the telemetry container, which already has
# INTERNAL_SECRET in its environment (compose passes it from the server's
# .env), so the secret never appears on a command line, in a log or here.
# Priority defaults to 3; the tag (default "admin:script") lets a later notice
# replace an earlier one on the phone. Exits non-zero if telemetry refused it.

set -euo pipefail

if [ $# -lt 2 ] || [ -z "$1" ]; then
    echo "Usage: $0 \"<title>\" \"<body>\" [priority 1-5] [tag]" >&2
    exit 2
fi

# cron runs with a minimal PATH. Compose commands need the checkout: use the
# current directory when it is one (deploy_remote.sh pipes this script in from
# there), else the checkout this script lives in.
export PATH="${PATH:-}:/usr/local/bin:/usr/bin:/bin"
[ -f docker-compose.yml ] || cd "$(dirname "$0")/.."

docker compose exec -T telemetry python - "$@" << 'PY'
import json
import os
import sys
import urllib.request

args = sys.argv[1:] + ["", ""]
title, body, priority, tag = args[0], args[1], args[2], args[3]
notice = {
    "user": "@admins",
    "category": "admin",
    "kind": "admin",
    "title": title[:120],
    "body": body,
    "priority": int(priority) if priority.isdigit() else 3,
    "tag": tag or "admin:script",
    "data": {"url": "/"},
}
request = urllib.request.Request(
    f"http://localhost:{os.environ.get('TELEMETRY_PORT', '11438')}/api/telemetry/notify",
    data=json.dumps(notice).encode(),
    headers={"Content-Type": "application/json", "X-Internal-Secret": os.environ["INTERNAL_SECRET"]},
    method="POST",
)
try:
    with urllib.request.urlopen(request, timeout=15) as resp:
        print(f"[OK] Admins notified: {title}")
except Exception as e:
    print(f"[FAIL] Could not notify admins: {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(1)
PY
