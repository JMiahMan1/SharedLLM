#!/usr/bin/env python3
"""Refresh the bundled stalkerware indicator snapshot (docs/APP_VETTING.md).

Downloads ``ioc.yaml`` from the open-source Stalkerware Indicators project
(https://github.com/AssoEchap/stalkerware-indicators, CC BY 4.0) at one commit
and writes the parts app vetting uses (each app's name, Android package names
and signing certificate SHA-1 fingerprints) to
``services/common/data/stalkerware_indicators.json``, with the source, its
license and the commit and date it was taken from.

Run it on a developer machine and commit the result; the services never fetch
the list themselves. Nothing about the household is sent: it is two public
GitHub requests.

    python scripts/refresh_stalkerware_indicators.py            # newest commit
    python scripts/refresh_stalkerware_indicators.py --commit <sha>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

import yaml

REPO = "AssoEchap/stalkerware-indicators"
SOURCE_URL = f"https://github.com/{REPO}"
LICENSE = "CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)"
ATTRIBUTION = "Stalkerware Indicators by Echap and contributors, " + SOURCE_URL
OUT = Path(__file__).resolve().parents[1] / "services" / "common" / "data" / "stalkerware_indicators.json"
HEADERS = {"User-Agent": "stalkerware-indicator-refresh", "Accept": "application/vnd.github+json"}
_PACKAGE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$")
_SHA1 = re.compile(r"^[0-9A-Fa-f]{40}$")


def _get(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=30) as resp:
        return resp.read()


def latest_commit() -> tuple[str, str]:
    """The newest commit that touched ioc.yaml: (sha, ISO date)."""
    rows = json.loads(_get(f"https://api.github.com/repos/{REPO}/commits?path=ioc.yaml&per_page=1"))
    if not rows:
        raise SystemExit("GitHub listed no commit for ioc.yaml")
    return rows[0]["sha"], rows[0]["commit"]["committer"]["date"]


def commit_date(sha: str) -> str:
    row = json.loads(_get(f"https://api.github.com/repos/{REPO}/commits/{sha}"))
    return row["commit"]["committer"]["date"]


def convert(raw: list, sha: str, date: str) -> dict:
    """The snapshot: only Android packages and certificate fingerprints."""
    apps = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        packages = sorted({p for p in item.get("packages") or [] if isinstance(p, str) and _PACKAGE.match(p)})
        certs = sorted({c.upper() for c in item.get("certificates") or [] if isinstance(c, str) and _SHA1.match(c)})
        if not packages and not certs:
            continue
        apps.append({
            "name": str(item.get("name") or "").strip() or "Unnamed",
            "type": str(item.get("type") or "stalkerware"),
            "packages": packages,
            "certificates_sha1": certs,
        })
    if not apps:
        raise SystemExit("ioc.yaml had no Android indicators; refusing to write an empty list")
    return {
        "source": SOURCE_URL,
        "file": "ioc.yaml",
        "commit": sha,
        "commit_date": date,
        "license": LICENSE,
        "attribution": ATTRIBUTION,
        "apps": apps,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--commit", help="the commit to take ioc.yaml from (default: the newest)")
    args = parser.parse_args()
    if args.commit:
        sha, date = args.commit, commit_date(args.commit)
    else:
        sha, date = latest_commit()
    raw = yaml.safe_load(_get(f"https://raw.githubusercontent.com/{REPO}/{sha}/ioc.yaml"))
    snapshot = convert(raw, sha, date)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(snapshot, indent=1, sort_keys=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(snapshot['apps'])} apps, commit {sha[:12]} of {date})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
