#!/usr/bin/env python3
"""Create (or re-key) the test account automated agents use, and save its key to a file.

Run on the machine the agent runs on. It signs in as an admin (the password is asked for
and never stored), asks identity for the test account's key
(``POST /api/users/security/test-account``), and writes the key straight to a file with
mode 600 in a mode-700 directory. The key is never printed, logged or put in an argument.

    scripts/create_test_account.py --server https://jarvis.example.com
    scripts/create_test_account.py --server https://jarvis.example.com --username ci-agent \\
        --out ~/.config/jarvis/ci-agent.key

Running it again replaces the key (the old one is revoked). The account is a test account:
no admin, no parent role, no roles, no password sign-in, and left out of the household's
roster, family counts and notifications (docs/KEY_ROTATION.md, "The test account").

Standard library only. Exit status: 0 written; 1 refused or failed (the message says why).
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import stat
import sys
import tempfile
import urllib.error
import urllib.request

DEFAULT_OUT = os.path.join("~", ".config", "jarvis", "test-agent.key")
TIMEOUT_S = 20


class Failed(Exception):
    pass


def _post(url: str, body: dict, bearer: str | None = None) -> dict:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read() or b"{}").get("detail")
        except ValueError:
            detail = None
        raise Failed(f"{url.split('/api/', 1)[-1]}: HTTP {exc.code}{': ' + str(detail) if detail else ''}") from None
    except (urllib.error.URLError, OSError) as exc:
        raise Failed(f"could not reach {url}: {exc}") from None


def write_secret(path: str, value: str) -> str:
    """Write ``value`` to ``path`` with mode 600 (directory 700), atomically. Returns the path."""
    path = os.path.abspath(os.path.expanduser(path))
    directory = os.path.dirname(path)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    if stat.S_IMODE(os.stat(directory).st_mode) & 0o077:
        os.chmod(directory, 0o700)
    old_umask = os.umask(0o077)
    try:
        fd, tmp = tempfile.mkstemp(prefix=".key.", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(value + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
    finally:
        os.umask(old_umask)
    return path


def admin_key(server: str, args: argparse.Namespace) -> str:
    if args.admin_key_file:
        path = os.path.expanduser(args.admin_key_file)
        if stat.S_IMODE(os.stat(path).st_mode) & 0o077:
            raise Failed(f"{path} is readable by others; chmod 600 it first")
        with open(path, encoding="utf-8") as f:
            key = f.read().strip()
        if not key:
            raise Failed(f"{path} is empty")
        return key
    username = args.admin or input("Admin username: ").strip()
    password = getpass.getpass(f"Password for {username}: ")
    if not username or not password:
        raise Failed("an admin username and password are needed")
    data = _post(f"{server}/api/auth/login", {"username": username, "password": password})
    key = str(data.get("api_key") or "")
    if not key:
        raise Failed("the server signed you in but returned no key")
    if not data.get("is_admin"):
        raise Failed(f"{username} is not an admin")
    return key


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create the automated agents' test account and save its key.")
    parser.add_argument("--server", default=os.getenv("JARVIS_SERVER_URL", ""),
                        help="the Jarvis address, e.g. https://jarvis.example.com (or JARVIS_SERVER_URL)")
    parser.add_argument("--username", default="test-agent", help="the test account's username (default test-agent)")
    parser.add_argument("--display-name", default="Test agent")
    parser.add_argument("--out", default=DEFAULT_OUT, help=f"where the key goes (default {DEFAULT_OUT})")
    parser.add_argument("--admin", help="the admin to sign in as (asked when missing)")
    parser.add_argument("--admin-key-file", help="read an admin key from this mode-600 file instead of signing in")
    args = parser.parse_args(argv)

    server = (args.server or "").strip().rstrip("/")
    if not server:
        print("create_test_account: --server (or JARVIS_SERVER_URL) is required", file=sys.stderr)
        return 1
    if not server.startswith("https://") and not server.startswith("http://localhost"):
        print("create_test_account: refusing a non-https server: the key would cross the network in the clear",
              file=sys.stderr)
        return 1
    try:
        key = admin_key(server, args)
        data = _post(f"{server}/api/users/security/test-account",
                     {"username": args.username, "display_name": args.display_name}, bearer=key)
        key = ""
        secret = str(data.get("api_key") or "")
        if not secret:
            raise Failed("the server returned no key for the test account")
        path = write_secret(args.out, secret)
        secret = ""
    except Failed as exc:
        print(f"create_test_account: {exc}", file=sys.stderr)
        return 1
    verb = "created" if data.get("created") else "re-keyed (the old key no longer works)"
    print(f"{data.get('username')} {verb}; its key is in {path} (mode 600). Never print it; send it as "
          "'Authorization: Bearer' read from that file.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
