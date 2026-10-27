#!/bin/bash
# Remove certificate authorities that the root programs no longer trust from
# this host's system trust store.
#
# Why: on 2026-10-17 eight eMudhra (emSign) intermediate CAs were shown to have
# recoverable private keys - anyone can forge a certificate under them. Mozilla
# removed emSign ECC Root CA - G3 on 2026-10-20 and emSign Root CA - C1 and
# emSign ECC Root CA - C3 on 2026-10-21 (bug 2083709), and eMudhra announced on
# 2026-10-22 that it is leaving the TLS business. This host's package still ships
# them (checked 2026-10-26: the newest available RPM contains all three), and
# curl, Python, dnf and Docker's registry TLS all read this store rather than a
# browser's, so a browser update does not reach them.
#
# Also blocked: roots the root programs have distrusted for other reasons
# (COMODO Certification Authority, GLOBALTRUST 2020, Entrust, Trustwave,
# SecureTrust, TeliaSonera, XRamp, Go Daddy Class 2, Starfield Class 2, ePKI).
# emSign Root CA - G1 is deliberately KEPT: the root programs kept it.
#
# Usage:
#   sudo bash fix_host_trust.sh          # apply
#   bash fix_host_trust.sh --dry-run     # just report what it would do
set -uo pipefail

BUNDLE=/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem
BLOCKDIR=/etc/pki/ca-trust/source/blocklist
[ -d /etc/pki/ca-trust/source/blacklist ] && [ ! -d "$BLOCKDIR" ] && BLOCKDIR=/etc/pki/ca-trust/source/blacklist
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

# The subject CNs to remove. Only those actually present are touched.
TARGETS=(
  "emSign Root CA - C1"
  "emSign ECC Root CA - C3"
  "emSign ECC Root CA - G3"
  "COMODO Certification Authority"
  "GLOBALTRUST 2020"
  "Entrust Root Certification Authority"
  "Entrust Root Certification Authority - G2"
  "Entrust Root Certification Authority - EC1"
  "Entrust.net Certification Authority (2048)"
  "Trustwave Global Certification Authority"
  "Trustwave Global ECC P256 Certification Authority"
  "Trustwave Global ECC P384 Certification Authority"
  "SecureTrust CA"
  "Secure Global CA"
  "TeliaSonera Root CA v1"
  "XRamp Global Certification Authority"
  "Go Daddy Class 2 Certification Authority"
  "Starfield Class 2 Certification Authority"
  "ePKI Root Certification Authority"
)

if [ ! -r "$BUNDLE" ]; then
  echo "ERROR: $BUNDLE is not readable" >&2
  exit 1
fi

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# Split the bundle into one file per certificate, then index each by its CN.
awk -v dir="$WORK" '
  /-----BEGIN CERTIFICATE-----/ { n++; f = sprintf("%s/cert-%03d.pem", dir, n) }
  f { print > f }
  /-----END CERTIFICATE-----/ { f = "" }
' "$BUNDLE"

declare -A WANTED
for t in "${TARGETS[@]}"; do WANTED["$t"]=1; done

found=0
for cert in "$WORK"/cert-*.pem; do
  cn=$(openssl x509 -in "$cert" -noout -subject 2>/dev/null | sed -n 's/.*CN *= *//p')
  [ -z "$cn" ] && continue
  [ -z "${WANTED[$cn]:-}" ] && continue
  found=$((found + 1))
  echo "found: $cn"
  if [ "$DRY" -eq 0 ]; then
    mkdir -p "$BLOCKDIR"
    cp "$cert" "$BLOCKDIR/$(echo "$cn" | tr ' /' '__').pem"
  fi
done

echo "matched $found of ${#TARGETS[@]} named roots"

if [ "$DRY" -eq 1 ]; then
  echo "(dry run: nothing written; re-run with sudo to apply)"
  exit 0
fi

if [ ! -w "$BLOCKDIR" ]; then
  echo "ERROR: $BLOCKDIR is not writable - run this with sudo" >&2
  exit 1
fi

command -v update-ca-trust >/dev/null || { echo "ERROR: update-ca-trust not found" >&2; exit 1; }
update-ca-trust extract
echo "trust store rebuilt"

echo "--- remaining matches in the extracted bundle (only emSign Root CA - G1 should appear) ---"
openssl crl2pkcs7 -nocrl -certfile "$BUNDLE" 2>/dev/null \
  | openssl pkcs7 -print_certs -noout 2>/dev/null \
  | grep -E "^subject=.*(emSign|COMODO Certification|GLOBALTRUST|Entrust Root|Trustwave|SecureTrust|Secure Global|TeliaSonera|XRamp|Go Daddy Class 2|Starfield Class 2|ePKI)" \
  | sort -u
echo "--- bundle size ---"
grep -c "BEGIN CERTIFICATE" "$BUNDLE"
