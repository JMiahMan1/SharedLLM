"""Live test: add an IP to a DNS record then remove it (idempotent, self-restoring).

Run on the host where the gateway is reachable, e.g.:
  ssh jeremiah@192.168.2.205 "python3 -" < scripts/test_dns_record_upsert.py

It targets the 'jeremiah-home-desktop.local' record, appends TEST_IP if
missing, verifies it appears, then removes it and verifies the record is
restored to its original value. Safe to run repeatedly.
"""
import json
import sys
import urllib.request
import urllib.error

BASE = "http://localhost:11435"
TARGET_DOMAIN = "jeremiah-home-desktop.local"
TEST_IP = "192.168.4.179"


def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def flatten(values):
    out = []
    for v in values or []:
        if isinstance(v, list):
            out.extend(flatten(v))
        elif str(v).strip():
            out.append(str(v).strip())
    return out


def main():
    status, raw = req("GET", "/api/dns")
    if status != 200:
        print(f"FAIL: GET /api/dns -> {status} {raw[:200]}")
        sys.exit(1)
    records = json.loads(raw)
    rec = next((r for r in records if r["domain"] == TARGET_DOMAIN), None)
    if not rec:
        print(f"FAIL: no record for {TARGET_DOMAIN}. Records: {[r['domain'] for r in records]}")
        sys.exit(1)

    original = flatten(rec["values"])
    print(f"Initial {TARGET_DOMAIN} values: {original}")
    rid = rec["id"]

    # --- ADD ---
    if TEST_IP in original:
        print(f"(already present, skipping add) {TEST_IP}")
        added_values = original
    else:
        added_values = original + [TEST_IP]
        s, d = req("PUT", f"/api/dns/{rid}", {
            "domain": rec["domain"], "record_type": rec["record_type"],
            "values": added_values, "ttl": rec.get("ttl", 300),
        })
        if s != 200:
            print(f"FAIL: PUT add -> {s} {d[:200]}"); sys.exit(1)

    status, raw = req("GET", "/api/dns")
    after_add = next(r for r in json.loads(raw) if r["domain"] == TARGET_DOMAIN)
    after_add_vals = flatten(after_add["values"])
    assert TEST_IP in after_add_vals, f"IP not present after add: {after_add_vals}"
    print(f"ADD OK -> {after_add_vals}")

    # --- REMOVE ---
    if TEST_IP in original:
        print(f"(was originally present, skipping remove) {TEST_IP}")
    else:
        removed_values = [v for v in after_add_vals if v != TEST_IP]
        s, d = req("PUT", f"/api/dns/{rid}", {
            "domain": rec["domain"], "record_type": rec["record_type"],
            "values": removed_values, "ttl": rec.get("ttl", 300),
        })
        if s != 200:
            print(f"FAIL: PUT remove -> {s} {d[:200]}"); sys.exit(1)

    status, raw = req("GET", "/api/dns")
    after_remove = next(r for r in json.loads(raw) if r["domain"] == TARGET_DOMAIN)
    after_remove_vals = flatten(after_remove["values"])
    assert after_remove_vals == original, f"Restore mismatch: got {after_remove_vals}, want {original}"
    print(f"REMOVE OK -> {after_remove_vals}")
    print("PASS: add + remove round-trip successful; record restored to original.")


if __name__ == "__main__":
    main()
