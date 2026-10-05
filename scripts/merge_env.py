"""Append-only merge of a local .env into a remote one.

The local .env arrives on stdin; argv[1] is the remote file to merge into.

Nothing that already exists on the remote is ever rewritten or removed. A key
the remote already defines is left exactly as it is, because the remote copy is
the only record of anything deliberately set on that host, and a deploy must
not quietly revert it. Only keys the remote is missing are appended, verbatim.
"""

from __future__ import annotations

import io
import os
import re
import shutil
import sys

LEADING = re.compile(r"^\s*(?:export\s+)?(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=")


def parse(text: str) -> tuple[list[str], dict[str, str]]:
    """Return the lines and a mapping of key -> first defining line."""
    lines: list[str] = []
    defined: dict[str, str] = {}
    for line in text.splitlines():
        lines.append(line.rstrip("\r"))
        match = LEADING.match(line)
        if match and match.group("key") not in defined:
            defined[match.group("key")] = line.rstrip("\r")
    return lines, defined


def merge(remote: str, local: str) -> tuple[list[str], list[str], list[str]]:
    """Decide which local keys to append. Returns (appended, preserved, duplicates)."""
    _, remote_defined = parse(remote)
    local_lines, local_defined = parse(local)
    appended: list[str] = []
    preserved: list[str] = []
    duplicates: list[str] = []
    seen: set[str] = set()

    for line in local_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = LEADING.match(line)
        if not match:
            continue
        key = match.group("key")
        if key in seen:
            duplicates.append(key)
            continue
        seen.add(key)
        if key in remote_defined:
            preserved.append(key)
        else:
            appended.append(local_defined[key])
    return appended, preserved, duplicates


def apply(remote_path: str, local_text: str) -> int:
    remote_text = ""
    if os.path.exists(remote_path):
        with open(remote_path, "r", encoding="utf-8", errors="replace") as handle:
            remote_text = handle.read()

    appended, preserved, duplicates = merge(remote_text, local_text)

    for key in duplicates:
        print(f"[WARN] .env defines {key} more than once locally; used the first.")

    if not appended:
        print(
            f"[OK] .env already defines all {len(preserved)} key(s) present locally. "
            "Nothing on the host was changed."
        )
        return 0

    existed = os.path.exists(remote_path)
    if existed:
        shutil.copy2(remote_path, f"{remote_path}.bak")
    with open(remote_path, "a", encoding="utf-8") as handle:
        if remote_text and not remote_text.endswith("\n"):
            handle.write("\n")
        if not remote_text.strip():
            handle.write("\n")
        for line in appended:
            handle.write(line + "\n")

    print(f"[OK] Added {len(appended)} new key(s) to .env: {' '.join(sorted(line.split('=', 1)[0] for line in appended))}")
    if preserved:
        print(f"[OK] Preserved {len(preserved)} key(s) already set on the host: {' '.join(sorted(preserved))}")
    if existed:
        print(f"[OK] The host's previous .env was copied to {remote_path}.bak")
    return 0


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write("usage: merge_env.py <remote .env path>  (local .env on stdin)\n")
        return 2
    if not sys.stdin.isatty() and hasattr(sys.stdin, "buffer"):
        text = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace").read()
    else:
        text = sys.stdin.read()
    if not text.strip():
        sys.stderr.write("merge_env.py: nothing arrived on stdin\n")
        return 2
    return apply(sys.argv[1], text)


if __name__ == "__main__":
    sys.exit(main())
