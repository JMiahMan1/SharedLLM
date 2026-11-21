#!/usr/bin/env bash
#
# Sign everyone out and reset their keys, from the server's console. The same operation as
# Admin > Security > "Sign everyone out and reset keys" (services/identity/sign_in_reset.py),
# for when no admin can sign in, or to script it. Docs: docs/KEY_ROTATION.md.
#
#   scripts/reset_sign_ins.sh --preview            # what would be reset (counts, no secrets)
#   scripts/reset_sign_ins.sh --everyone           # asks you to type RESET, then resets everyone
#   scripts/reset_sign_ins.sh --user NAME          # one person only
#   scripts/reset_sign_ins.sh --hub-only           # only revoke every person's home hub login
#
# Runs inside the identity container (it has the database), and for the hub inside the
# homehub container (the stack's secret never leaves the container it is in). Prints the
# report: what was revoked and which devices need re-enrolling by hand. No key is printed.
#
# Environment: COMPOSE (default "docker compose").

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE="${COMPOSE:-docker compose}"

compose() { (cd "$ROOT" && $COMPOSE "$@"); }
die() { echo "reset_sign_ins: $*" >&2; exit 1; }

hub_only() {
  compose exec -T homehub python -c '
import json, os, urllib.request
port = os.getenv("HOMEHUB_PORT", "8123")
req = urllib.request.Request(f"http://127.0.0.1:{port}/hub/auth/sign-out-everyone", data=b"{}", method="POST",
                             headers={"Content-Type": "application/json", "X-Internal-Secret": os.environ["INTERNAL_SECRET"]})
print(json.dumps(json.loads(urllib.request.urlopen(req, timeout=15).read()), indent=2))
' || die "the hub did not sign everyone out (is homehub running?)"
}

confirm() {
  local word
  [ -r /dev/tty ] || die "run this from a terminal: it asks you to confirm"
  echo "This signs $1 out of every phone, browser and the Android app, and revokes their extra keys"
  echo "and PC agent keys. Devices listed afterwards must be re-enrolled by hand."
  read -r -p "Type RESET to continue: " word </dev/tty
  [ "$word" = "RESET" ] || die "not confirmed; nothing changed"
}

case "${1:-}" in
  --preview)
    compose exec -T identity python -m services.identity.sign_in_reset --preview ;;
  --everyone)
    confirm "everyone"
    status=0
    compose exec -T identity python -m services.identity.sign_in_reset --confirm RESET || status=$?
    if [ "$status" -eq 3 ]; then
      echo "identity could not reach the hub; signing everyone out of the hub from its own container" >&2
      hub_only
    elif [ "$status" -ne 0 ]; then
      die "the reset failed (exit $status)"
    fi ;;
  --user)
    [ -n "${2:-}" ] || die "--user needs a username"
    confirm "$2"
    compose exec -T identity python -m services.identity.sign_in_reset --confirm RESET --user "$2" ;;
  --hub-only)
    confirm "every person in the home hub"
    hub_only ;;
  *)
    sed -n '3,14p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
