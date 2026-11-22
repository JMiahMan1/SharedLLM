#!/usr/bin/env python3
"""Run the on-demand context eval against a live model (inside the gateway container).

    docker exec sharedllm_gateway python scripts/eval_knowledge_tools.py --model <name>

Prints one line per case and a JSON summary. Exit status 1 when any case fails.
See services/gateway/knowledge_eval.py.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--no-hub", action="store_true", help="skip the cases that need a home hub")
    args = parser.parse_args()

    from services import config
    from services.gateway import knowledge_eval
    from services.gateway.http_client import close_all_clients
    from services.gateway.main import select_system_instruction_for_query

    try:
        hub = config.any_home_hub() and not args.no_hub
        problems = knowledge_eval.check_routing(hub=hub)
        for p in problems:
            print(f"ROUTING {p}")
        report = await knowledge_eval.run(
            args.model, system_prompt=select_system_instruction_for_query("", args.model), hub=hub,
            limit=args.limit,
        )
        for r in report["results"]:
            print(f"{'PASS' if r['ok'] else 'FAIL'} {r['id']}: expected tool {r['want']!r}, "
                  f"got {r['got']!r} ({r['why']})")
        print(json.dumps({k: v for k, v in report.items() if k != "results"}, indent=2))
        return 0 if not problems and report["passed"] == report["cases"] else 1
    finally:
        # aiohttp keeps a session and connector per event loop; without this the process exits with
        # "Unclosed client session" noise after the report.
        await close_all_clients()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
