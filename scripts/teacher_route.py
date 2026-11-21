#!/usr/bin/env python3
"""May this task go to a cloud teacher model, or must it stay on alpaca-local?

    python scripts/teacher_route.py "Refactor the pager tests" --path services/gateway/pager.py
    # cloud: public code reasoning: ...           (exit 0)

    python scripts/teacher_route.py "Why does the family calendar sync fail on the live server"
    # local: it involves household or personal data; it touches production systems ...  (exit 1)

Exit 0 means a cloud teacher may do it (and its trace may be collected); exit 1
means use the local model. ``--json`` prints the decision as JSON. Household
names are read from the same local file the scrubber uses (``--names-file``),
when it exists. See docs/TEACHER_TRACES.md, "Routing".
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.gateway.trainer import teacher_routing, teacher_scrub  # noqa: E402

DEFAULT_NAMES_FILE = os.getenv("TEACHER_SCRUB_NAMES_FILE") or "~/.config/sharedllm/teacher-names.txt"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("task", help="the task, as you would ask it")
    parser.add_argument("--path", action="append", default=[], help="a file the task will touch (repeatable)")
    parser.add_argument("--names-file", default=DEFAULT_NAMES_FILE)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    names: list[str] = []
    names_file = pathlib.Path(args.names_file).expanduser()
    if names_file.is_file():
        names = teacher_scrub.load_names(names_file)
    decision = teacher_routing.route_task(args.task, args.path, names=names)
    if args.json:
        print(json.dumps(decision))
    else:
        print(f"{decision['route']}: {'; '.join(decision['reasons'])}")
    return 0 if decision["route"] == "cloud" else 1


if __name__ == "__main__":
    sys.exit(main())
