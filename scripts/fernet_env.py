#!/usr/bin/env python3
"""Change FERNET_KEY in the server's .env without any key ever being printed.

Used by scripts/rotate_fernet_key.sh; standard library only, so it runs with the
host's python3. Every write is atomic (a temporary file in the same directory, then a
rename) and keeps the file's mode; nothing else in the file changes.

    fernet_env.py status       --env .env   # which keys are set, as one-way fingerprints
    fernet_env.py stage-new    --env .env   # FERNET_KEY -> FERNET_KEY_PREVIOUS, a new FERNET_KEY
    fernet_env.py drop-previous --env .env  # remove FERNET_KEY_PREVIOUS

``stage-new`` refuses to run while FERNET_KEY_PREVIOUS is already set (a rotation is in
progress: finish it, or the key that rotation needs would be lost). The new key is made
here from os.urandom and goes straight into the file; it is never printed, never in an
argument, never in the environment of another process.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import json
import os
import re
import stat
import sys
import tempfile

_LINE = re.compile(r"^(?P<export>export\s+)?(?P<key>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>.*)$")


class EnvError(SystemExit):
    def __init__(self, message: str) -> None:
        super().__init__(f"fernet_env: {message}")


def _value(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"":
        return raw[1:-1]
    return raw


def read_env(path: str) -> tuple[list[str], dict[str, str]]:
    if not os.path.exists(path):
        raise EnvError(f"{path} does not exist")
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    values: dict[str, str] = {}
    for line in lines:
        m = _LINE.match(line.strip())
        if m:
            values[m.group("key")] = _value(m.group("value"))
    return lines, values


def fingerprint(key: str | None) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:12] if key else ""


def valid_fernet_key(key: str) -> bool:
    try:
        return len(base64.urlsafe_b64decode(key.encode())) == 32
    except (ValueError, TypeError):
        return False


def new_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


def write_env(path: str, lines: list[str]) -> None:
    st = os.stat(path)
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, stat.S_IMODE(st.st_mode))
        # Not root: the file stays ours, which it already was to be writable.
        with contextlib.suppress(PermissionError):
            os.chown(tmp, st.st_uid, st.st_gid)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _set(lines: list[str], key: str, value: str | None, after: str | None = None) -> list[str]:
    """Replace ``key``'s line (or drop it when value is None); add it after ``after`` when missing."""
    out: list[str] = []
    found = False
    for line in lines:
        m = _LINE.match(line.strip())
        if m and m.group("key") == key:
            found = True
            if value is not None:
                out.append(f"{key}={value}")
            continue
        out.append(line)
    if not found and value is not None:
        index = len(out)
        if after:
            for i, line in enumerate(out):
                m = _LINE.match(line.strip())
                if m and m.group("key") == after:
                    index = i + 1
                    break
        out.insert(index, f"{key}={value}")
    return out


def status(path: str) -> dict[str, object]:
    _, values = read_env(path)
    current = values.get("FERNET_KEY") or ""
    previous = [p.strip() for p in (values.get("FERNET_KEY_PREVIOUS") or "").split(",") if p.strip()]
    mode = stat.S_IMODE(os.stat(path).st_mode)
    return {
        "env": os.path.abspath(path),
        "fernet_key": fingerprint(current),
        "fernet_key_valid": valid_fernet_key(current) if current else False,
        "previous": [fingerprint(p) for p in previous],
        "rotating": bool(previous),
        "mode": oct(mode),
        "readable_by_others": bool(mode & 0o077),
    }


def stage_new(path: str) -> dict[str, object]:
    lines, values = read_env(path)
    current = values.get("FERNET_KEY") or ""
    if not current:
        raise EnvError("FERNET_KEY is not set in the .env; there is nothing to rotate from")
    if not valid_fernet_key(current):
        raise EnvError("FERNET_KEY in the .env is not a valid Fernet key; refusing to rotate from it")
    if (values.get("FERNET_KEY_PREVIOUS") or "").strip():
        raise EnvError("FERNET_KEY_PREVIOUS is already set: a rotation is in progress. Finish it "
                       "(rotate_fernet_key.sh rotate, then finalize) before starting another")
    fresh = new_key()
    lines = _set(lines, "FERNET_KEY", fresh)
    lines = _set(lines, "FERNET_KEY_PREVIOUS", current, after="FERNET_KEY")
    write_env(path, lines)
    return status(path)


def drop_previous(path: str) -> dict[str, object]:
    lines, values = read_env(path)
    if not (values.get("FERNET_KEY_PREVIOUS") or "").strip():
        return status(path)
    write_env(path, _set(lines, "FERNET_KEY_PREVIOUS", None))
    return status(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Change FERNET_KEY in a .env without printing any key.")
    parser.add_argument("command", choices=("status", "stage-new", "drop-previous"))
    parser.add_argument("--env", default=".env", help="the .env to read and change (default: ./.env)")
    args = parser.parse_args(argv)
    if args.command == "status":
        result = status(args.env)
    elif args.command == "stage-new":
        result = stage_new(args.env)
    else:
        result = drop_previous(args.env)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
