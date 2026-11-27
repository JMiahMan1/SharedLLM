#!/usr/bin/env bash
# Flash an Echo Dot 3 (donut) or Echo Show 8 1st gen (crown) into a HomeHub voice satellite.
#
#   scripts/echo_flash.sh donut|crown [--next] [--dry-run] [--from STEP]
#     --next       the next Echo of the same model (forgets the previous one's progress)
#     --dry-run    walk the steps without running them
#     --from STEP  start at STEP, counting the earlier ones as done. An Echo you already unlocked, flashed
#                  and rooted by hand: --from build (its first check stops if the firmware is not the one needed)
#
# Wraps the upstream echo-dot-assist guided setup at a pinned, tested commit. It never runs the unlock itself:
# the unlock's author asks that it is only run by hand from the XDA thread, so the upstream setup stops there and
# waits. Guide, solder points and parts: docs/ECHO_FLASHING.md.
#
# Env: ECHO_ASSIST_DIR (default ~/echo-dot-assist), ECHO_ASSIST_REF (default the pinned commit below).
set -euo pipefail

PINNED_REF=1d7d957139f24788f06acc7761854fe2c80d19a0
REPO_URL=https://github.com/Gamer92000/echo-dot-assist.git
DIR=${ECHO_ASSIST_DIR:-$HOME/echo-dot-assist}
REF=${ECHO_ASSIST_REF:-$PINNED_REF}

die() { printf 'echo_flash: %s\n' "$*" >&2; exit 1; }
say() { printf '\n== %s\n' "$*"; }

device=${1:-}
shift || true
case $device in
donut | crown) ;;
*) die "usage: scripts/echo_flash.sh donut|crown [--next] [--dry-run] [--from STEP]   (donut = Echo Dot 3 D9N29T, crown = Echo Show 8 1st gen C7H6N3)" ;;
esac
args=("$device")
while [ $# -gt 0 ]; do
    case $1 in
    --next) args+=(--restart) ;;
    --dry-run) args+=(--dry-run) ;;
    --from) [ $# -ge 2 ] || die "--from needs a step (e.g. --from build)"; args+=(--from "$2"); shift ;;
    --from=*) args+=(--from "${1#--from=}") ;;
    *) die "unknown option: $1" ;;
    esac
    shift
done

[ "$(uname -s)" = Linux ] || die "run this on a Linux PC booted on the hardware (a live USB stick works), not macOS: the unlock needs direct USB"
if grep -qiE 'microsoft|wsl' /proc/version 2>/dev/null; then die "WSL cannot reach the Echo's bootrom; boot Linux on the PC instead"; fi
if command -v systemd-detect-virt >/dev/null && [ "$(systemd-detect-virt 2>/dev/null || true)" != none ]; then
    die "this looks like a virtual machine; the unlock needs direct USB, boot Linux on the PC instead"
fi
command -v git >/dev/null || die "git is missing"

say "Before you start"
if [ "$device" = donut ]; then
    cat <<'EOF'
Echo Dot 3, model D9N29T only (look under the base). Not C78MP8 or 36EBT3.

WARNING: upstream issue #112 is open. On some recently updated Dots the unlock ends without an error and the
Echo stays locked for good. Unlock ONE Dot you can afford to lose first.
https://github.com/Gamer92000/echo-dot-assist/issues/112

Wires: GND to TM3, D- to TM4, D+ to TM5, nothing on 5 V. The Dot runs from its own 12 V adapter.
EOF
else
    cat <<'EOF'
Echo Show 8 1st gen (2019), model C7H6N3 only. Not the 2nd gen (A8H3N2).
Use a USB-A to micro-USB DATA cable and the Echo's own power supply. Nothing has to be opened.
When TWRP asks "Keep System Read only?", tap Keep Read Only.
EOF
fi
cat <<'EOF'

Never let the Echo reach Amazon: no Alexa app, and block its internet at the router until it is installed.
The unlock is done by hand from the XDA thread the setup names; it waits for you there.
EOF
read -r -p "Continue? [y/N] " ok </dev/tty
[ "${ok,,}" = y ] || die "stopped"

say "echo-dot-assist at ${REF:0:12}"
if [ -d "$DIR/.git" ]; then
    git -C "$DIR" fetch --quiet origin
else
    git clone --quiet "$REPO_URL" "$DIR"
fi
if [ -n "$(git -C "$DIR" status --porcelain --untracked-files=no)" ]; then
    die "$DIR has local changes to tracked files; commit or stash them first"
fi
git -C "$DIR" -c advice.detachedHead=false checkout --quiet "$REF"

say "Guided setup (upstream scripts/setup.sh ${args[*]})"
(cd "$DIR" && scripts/setup.sh "${args[@]}")

say "Adopt it in HomeHub"
cat <<EOF
1. Give the Echo a DHCP reservation (its IP is in the setup output above and in $DIR/build/$device/setup.log).
2. Jarvis > Devices > Add device > ESPHome: the Echo's IP, port 26053, no encryption key (HomeHub sets one).
3. Back up $DIR/secrets/update.key (encrypted, never in git): it signs updates and opens adb over Wi-Fi.
Updates later: cd $DIR && DEVICE=$device scripts/ota-push.sh <echo-ip>
EOF
