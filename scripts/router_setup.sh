#!/usr/bin/env bash
# Give Jarvis read-only access to an OpenWrt router for Admin > Network
# (docs/NETWORK.md). Run from a machine that can ssh to both the router and
# the Jarvis server:
#
#   scripts/router_setup.sh [router ssh target] [router ssh port] [server ssh target] [server dir]
#   scripts/router_setup.sh root@192.168.2.1 32100 jeremiah@192.168.2.205 /home/jeremiah/SharedLLM
#
# What it changes on the router, after saving a backup to ~/Desktop:
#   - /usr/share/rpcd/acl.d/jarvis-monitor.json: an ACL that can only read
#     status (system, interfaces, Wi-Fi, DHCP leases and config, the log);
#   - an rpcd login "jarvis" using that ACL, with a new random password;
#   - rpcd is reloaded so the login works.
# The password goes from here straight into Jarvis's Identity (encrypted) and
# is never printed or written to disk. Running it again replaces the password.
# Undo: remove the ACL file and the rpcd login, then `/etc/init.d/rpcd reload`.
set -euo pipefail

ROUTER="${1:-root@192.168.2.1}"
PORT="${2:-22}"
SERVER="${3:-}"
SERVER_DIR="${4:-/home/jeremiah/SharedLLM}"
ROUTER_URL="${ROUTER_URL:-https://${ROUTER#*@}}"
LOGIN="jarvis"

rsh() { ssh -p "$PORT" -o ConnectTimeout=20 "$ROUTER" "$@"; }

echo "== 1. Backing up the router's configuration"
backup="$HOME/Desktop/openwrt-backup-$(date +%Y%m%d-%H%M%S).tar.gz"
rsh 'sysupgrade -b /tmp/cfg-backup.tar.gz >/dev/null 2>&1 && cat /tmp/cfg-backup.tar.gz && rm -f /tmp/cfg-backup.tar.gz' > "$backup"
chmod 600 "$backup"
tar -tzf "$backup" | grep -q '^etc/config/rpcd$' || { echo "Backup looks wrong; stopping." >&2; exit 1; }
echo "   saved $backup"

echo "== 2. Installing the read-only ACL"
rsh 'cat > /usr/share/rpcd/acl.d/jarvis-monitor.json' <<'JSON'
{
  "jarvis-monitor": {
    "description": "Jarvis: read-only network monitoring",
    "read": {
      "ubus": {
        "system": ["info", "board"],
        "network.interface": ["dump"],
        "luci-rpc": ["getDHCPLeases"],
        "iwinfo": ["devices", "info", "assoclist", "survey"],
        "log": ["read"],
        "file": ["read"],
        "uci": ["get"]
      },
      "uci": ["dhcp"],
      "file": {
        "/proc/sys/net/netfilter/nf_conntrack_count": ["read"],
        "/proc/sys/net/netfilter/nf_conntrack_max": ["read"]
      }
    }
  }
}
JSON

echo "== 3. Creating the '$LOGIN' login"
password="$(openssl rand -base64 30 | tr -d '/+=\n' | cut -c1-32)"
# The password arrives on stdin, so it is not on any command line here; on
# the router it is hashed with uhttpd's crypt and only the hash is stored.
printf '%s\n' "$password" | rsh "LOGIN=$LOGIN sh -s" <<'SH'
read -r pw
hash="$(uhttpd -m "$pw")"
for s in $(uci -q show rpcd | sed -n "s/^rpcd\.\(@login\[[0-9]*\]\)\.username='$LOGIN'$/\1/p"); do uci delete "rpcd.$s"; done
s="$(uci add rpcd login)"
uci set "rpcd.$s.username=$LOGIN"
uci set "rpcd.$s.password=$hash"
uci add_list "rpcd.$s.read=jarvis-monitor"
uci commit rpcd
/etc/init.d/rpcd reload
SH
echo "   done"

if [ -z "$SERVER" ]; then
  echo "No Jarvis server given: enter the login in Admin > Network (URL $ROUTER_URL, user $LOGIN)."
  echo "The password was not saved anywhere; run this again with the server to set one."
  exit 0
fi

echo "== 4. Saving the login in Jarvis and checking it"
printf '%s\n%s\n%s\n' "$ROUTER_URL" "$LOGIN" "$password" | ssh -o ConnectTimeout=20 "$SERVER" \
  "cd '$SERVER_DIR' && docker compose exec -T gateway python -c \"\$(cat <<'PY'
import json, os, sys, urllib.request
from services.config import IDENTITY_SVC_URL, CONTROL_PLANE_URL
url, user, password = (sys.stdin.readline().rstrip('\n') for _ in range(3))
h = {'Content-Type': 'application/json', 'X-Internal-Secret': os.environ['INTERNAL_SECRET']}
def post(u, body):
    req = urllib.request.Request(u, data=json.dumps(body).encode(), method='POST', headers=h)
    return json.load(urllib.request.urlopen(req, timeout=60))
for field, value in (('router_url', url), ('router_user', user), ('router_pass', password)):
    post(IDENTITY_SVC_URL.rstrip('/') + '/api/admin/seed-credential', {'field': field, 'value': value})
s = post(CONTROL_PLANE_URL.rstrip('/') + '/api/network/check', {})
radios = s.get('radios') or []
print('   connected:', s.get('connected'), '| error:', s.get('error'))
print('   router:', (s.get('router') or {}).get('model'), '| internet up:', s.get('internet'))
print('   access points:', len(radios), '| Wi-Fi clients:', sum(len(r['clients']) for r in radios))
PY
)\"" 2>&1 | grep -v 'level=warning'
unset password
