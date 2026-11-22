#!/usr/bin/env python3
"""Rebuild .test_durations (pytest-split) from a green SOA CI run.

The SOA Microservices CI shards balance on .test_durations. Running the whole
suite locally to refresh it needs every service's dependencies, so this reads
the per-shard junit reports the CI run already uploaded instead:

    scripts/ci_test_durations.py <run-id>      # downloads pytest-results-*
    scripts/ci_test_durations.py --dir <path>  # junit XML already on disk

A junit classname is dotted (services.gateway.tests.test_x.TestY); the node id
pytest-split wants is a path (services/gateway/tests/test_x.py::TestY::name),
so the longest dotted prefix that is a file in the repo becomes the path.
"""

import argparse
import glob
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def node_id(classname: str, name: str) -> str | None:
    parts = classname.split(".")
    for i in range(len(parts), 0, -1):
        path = os.path.join(*parts[:i]) + ".py"
        if os.path.isfile(os.path.join(ROOT, path)):
            return "::".join([path.replace(os.sep, "/"), *parts[i:], name])
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run_id", nargs="?", help="GitHub Actions run id of a green SOA CI run")
    ap.add_argument("--dir", help="directory holding the junit XML reports")
    args = ap.parse_args()
    if not args.run_id and not args.dir:
        ap.error("give a run id or --dir")

    src = args.dir
    if not src:
        src = tempfile.mkdtemp(prefix="junit-")
        subprocess.run(
            ["gh", "run", "download", args.run_id, "-p", "pytest-results-*", "-D", src],
            check=True,
        )

    durations: dict[str, float] = {}
    unmapped = 0
    for report in glob.glob(os.path.join(src, "**", "*.xml"), recursive=True):
        for case in ET.parse(report).iter("testcase"):
            classname = case.get("classname")
            if not classname:  # a module skipped at collection
                continue
            nid = node_id(classname, case.get("name", ""))
            if nid is None:
                unmapped += 1
                continue
            durations[nid] = round(durations.get(nid, 0.0) + float(case.get("time") or 0), 4)

    if not durations:
        print("no test cases found", file=sys.stderr)
        return 1
    with open(os.path.join(ROOT, ".test_durations"), "w") as fh:
        json.dump(dict(sorted(durations.items())), fh, indent=4)
        fh.write("\n")
    print(f"{len(durations)} tests, {sum(durations.values()):.0f} s, {unmapped} unmapped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
