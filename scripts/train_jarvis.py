#!/usr/bin/env python3
"""Run the Jarvis Trainer from a shell: evaluate, dream, repair, export, draft.

The trainer's own cycle lives in the gateway (``services/gateway/trainer/``) and
can run on a timer (``trainer_cycle_interval_minutes``). This script drives the
same endpoints from outside, which is what a cron job, a CI job or a person at a
terminal wants: one bounded pass, and the evidence printed.

Every call authenticates with ``INTERNAL_SECRET`` (the gateway treats an
internal caller as an admin), so nothing here ever stores or echoes a secret.

Usage::

    export GATEWAY_URL=https://jarvis.example.com     # or --gateway
    export INTERNAL_SECRET=<from .env on the server>   # or DEPLOY_HOST=user@host

    python scripts/train_jarvis.py catalog
    python scripts/train_jarvis.py curriculum --buckets writing,repair --limit 10
    python scripts/train_jarvis.py eval --buckets repair --limit 8
    python scripts/train_jarvis.py cycle --limit 6 --no-lessons
    python scripts/train_jarvis.py repair --workspace sermons --path tools/parse.py \
        --targets tools/test_parse.py
    python scripts/train_jarvis.py sermon --workspace sermons --passage "Romans 8:28"
    python scripts/train_jarvis.py dataset --report eval-20261120T101500Z-ab12
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

import httpx

DEFAULT_GATEWAY_URL = os.getenv("GATEWAY_URL", "").rstrip("/")
DEPLOY_HOST = os.getenv("DEPLOY_HOST", "")
DEPLOY_DIR = os.getenv("DEPLOY_DIR", "SharedLLM")


def internal_secret() -> str:
    """The secret from the environment, or read from the server over ssh."""
    secret = os.getenv("INTERNAL_SECRET", "")
    if secret:
        return secret
    if not DEPLOY_HOST:
        sys.exit("Set INTERNAL_SECRET, or DEPLOY_HOST=user@host to read it from the server.")
    try:
        out = subprocess.run(
            ["ssh", DEPLOY_HOST, f"grep -m1 '^INTERNAL_SECRET=' ~/{DEPLOY_DIR}/.env"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout
    except (subprocess.SubprocessError, OSError) as exc:
        sys.exit(f"Could not read INTERNAL_SECRET from {DEPLOY_HOST}: {exc}")
    return out.split("=", 1)[1].strip().strip('"')


def call(gateway: str, secret: str, method: str, path: str, body: dict | None = None,
         timeout: float = 900.0) -> dict:
    """One trainer call. A non-2xx answer prints the server's own words."""
    url = f"{gateway}{path}"
    headers = {"X-Internal-Secret": secret}
    with httpx.Client(timeout=timeout) as client:
        resp = client.request(method, url, json=body, headers=headers)
    try:
        data = resp.json()
    except ValueError:
        data = {"status": "ERROR", "message": resp.text[:500]}
    if resp.status_code >= 400:
        detail = data.get("detail") if isinstance(data, dict) else None
        sys.exit(f"{method} {path} failed ({resp.status_code}): {detail or data}")
    return data


def _print(value: object) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def _buckets(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Drive the Jarvis Trainer.")
    parser.add_argument("--gateway", default=DEFAULT_GATEWAY_URL or "http://localhost:11435")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("catalog", help="print the derived feature counts")

    curriculum = sub.add_parser("curriculum", help="list the derived cases")
    curriculum.add_argument("--buckets", default="")
    curriculum.add_argument("--limit", type=int, default=20)

    evaluate = sub.add_parser("eval", help="run a bounded evaluation")
    evaluate.add_argument("--buckets", default="")
    evaluate.add_argument("--limit", type=int, default=12)
    evaluate.add_argument("--budget", type=float, default=600.0)

    cycle = sub.add_parser("cycle", help="dream, evaluate, propose in one pass")
    cycle.add_argument("--buckets", default="")
    cycle.add_argument("--limit", type=int, default=12)
    cycle.add_argument("--budget", type=float, default=600.0)
    cycle.add_argument("--no-lessons", action="store_true", help="do not write proposals into RAG")

    repair = sub.add_parser("repair", help="repair a workspace file, proven by its tests")
    repair.add_argument("--workspace", required=True)
    repair.add_argument("--path", required=True)
    repair.add_argument("--error", default="")
    repair.add_argument("--targets", default="", help="comma-separated test paths")
    repair.add_argument("--attempts", type=int, default=2)

    sermon = sub.add_parser("sermon", help="draft a sermon into a workspace")
    sermon.add_argument("--workspace", required=True)
    sermon.add_argument("--passage", required=True)
    sermon.add_argument("--title", default="")
    sermon.add_argument("--theme", default="")
    sermon.add_argument("--translation", default="kjv")
    sermon.add_argument("--points", type=int, default=3)
    sermon.add_argument("--series", default="")

    dataset = sub.add_parser("dataset", help="export a report as JSONL fine-tuning rows")
    dataset.add_argument("--report", required=True)
    dataset.add_argument("--all", action="store_true", help="include failing rows too")

    args = parser.parse_args()
    gateway = args.gateway.rstrip("/")
    secret = internal_secret()

    if args.command == "catalog":
        data = call(gateway, secret, "GET", "/api/trainer/catalog")
        _print({"counts": data.get("counts"), "fingerprint": data.get("fingerprint")})
        return

    if args.command == "curriculum":
        query = f"?buckets={args.buckets}&limit={args.limit}" if args.buckets else f"?limit={args.limit}"
        data = call(gateway, secret, "GET", f"/api/trainer/curriculum{query}")
        _print(
            {
                "summary": data.get("summary"),
                "cases": [
                    {"id": case["id"], "bucket": case["bucket"], "title": case["title"]}
                    for case in data.get("cases", [])
                ],
            }
        )
        return

    if args.command == "eval":
        body: dict = {"limit": args.limit, "budget_s": args.budget}
        if _buckets(args.buckets):
            body["buckets"] = _buckets(args.buckets)
        report = call(gateway, secret, "POST", "/api/trainer/eval", body)
        _print(
            {
                "id": report.get("id"),
                "model": report.get("model"),
                "totals": report.get("totals"),
                "by_bucket": report.get("by_bucket"),
                "stopped_reason": report.get("stopped_reason"),
            }
        )
        return

    if args.command == "cycle":
        body = {"limit": args.limit, "budget_s": args.budget, "apply_lessons": not args.no_lessons}
        if _buckets(args.buckets):
            body["buckets"] = _buckets(args.buckets)
        summary = call(gateway, secret, "POST", "/api/trainer/cycle", body)
        _print(
            {
                "eval": summary.get("eval"),
                "dream": summary.get("dream"),
                "errors": summary.get("errors"),
                "lessons": summary.get("lessons"),
                "proposals": [
                    {"kind": item["kind"], "title": item["title"], "priority": item["priority"]}
                    for item in summary.get("proposals", [])
                ],
            }
        )
        return

    if args.command == "repair":
        body = {
            "workspace_id": args.workspace,
            "path": args.path,
            "attempts": args.attempts,
            "test_targets": _buckets(args.targets),
        }
        if args.error:
            body["error_text"] = args.error
        result = call(gateway, secret, "POST", "/api/trainer/repair", body, timeout=1200.0)
        _print(
            {
                key: result.get(key)
                for key in ("status", "attempts", "tests_passed", "model", "guards", "message")
            }
        )
        return

    if args.command == "sermon":
        body = {
            "workspace_id": args.workspace,
            "passage": args.passage,
            "title": args.title,
            "theme": args.theme,
            "translation": args.translation,
            "points": args.points,
            "series": args.series,
        }
        drafted = call(gateway, secret, "POST", "/api/trainer/sermon", body)
        _print(
            {
                "title": drafted.get("title"),
                "path": drafted.get("path"),
                "words": drafted.get("words"),
                "notes": drafted.get("notes"),
            }
        )
        return

    if args.command == "dataset":
        exported = call(
            gateway,
            secret,
            "POST",
            "/api/trainer/reports/dataset",
            {"report_id": args.report, "accepted_only": not args.all},
        )
        _print(exported)
        return

    parser.error(f"unknown command {args.command}")


if __name__ == "__main__":
    main()
