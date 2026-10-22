#!/usr/bin/env python3
"""Probe the external service URLs this app depends on.

Run it after a deploy, when media misbehaves, or from a cron/monitoring job:

    .venv/bin/python scripts/check_service_urls.py
    .venv/bin/python scripts/check_service_urls.py --json

Each target gets a classified answer (OK / UNAVAILABLE / UNREACHABLE /
ERROR). Exit code is 1 when any target is not OK, so a scheduler can alert on
it. Audiobookshelf, Music Assistant and Nextcloud come from ``.env`` (the same
values the gateway uses); the gateway itself defaults to the production host.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.gateway.service_probes import DEFAULT_PROBE_TIMEOUT, probe_many  # noqa: E402


def load_env() -> None:
    """Load .env into the environment without overriding real variables."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def targets() -> dict[str, str]:
    abs_url = (os.getenv("AUDIOBOOKSHELF_URL") or "").strip().rstrip("/")
    ma_url = (os.getenv("MA_URL") or os.getenv("MASS_URL") or "").strip().rstrip("/")
    nextcloud_url = (os.getenv("NEXTCLOUD_URL") or "").strip().rstrip("/")
    gateway_url = (os.getenv("GATEWAY_URL") or "https://jarvis.sumemail.com").strip().rstrip("/")
    result: dict[str, str] = {}
    if abs_url:
        result["audiobookshelf"] = f"{abs_url}/ping"
    if ma_url:
        result["music_assistant"] = ma_url
    if nextcloud_url:
        result["nextcloud"] = f"{nextcloud_url}/status.php"
    result["gateway"] = f"{gateway_url}/health"
    return result


async def run(timeout: float) -> list[dict]:
    async with aiohttp.ClientSession() as client:
        results = await probe_many(
            client,
            targets(),
            timeout=timeout,
            # Music Assistant has no health route; any HTTP answer proves it.
            expectations={"music_assistant": None},
        )
    return [result.as_dict() for result in results]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument("--timeout", type=float, default=DEFAULT_PROBE_TIMEOUT, help="per-probe seconds")
    args = parser.parse_args()

    load_env()
    results = asyncio.run(run(args.timeout))

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        width = max(len(r["name"]) for r in results)
        for r in results:
            mark = "PASS" if r["ok"] else "FAIL"
            timing = f"{r['milliseconds']}ms" if r["milliseconds"] is not None else "-"
            detail = r["detail"] or r["status"]
            print(f"{mark}  {r['name']:<{width}}  {r['status']:<11}  {timing:>7}  {r['url']}  ({detail})")

    failed = [r for r in results if not r["ok"]]
    if failed:
        print(f"\n{len(failed)} service(s) not OK: {', '.join(r['name'] for r in failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
