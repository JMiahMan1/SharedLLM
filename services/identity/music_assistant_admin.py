"""Music Assistant housekeeping, run inside the identity container. Prints names only, never a token.

Dry run first (the default), then ``--apply`` once the list looks right::

    # People whose saved Music Assistant URL points at a retired host. Clearing it makes them follow
    # the household Music Assistant (HomeHub's integration); their token is kept.
    docker exec -w /app sharedllm_identity python -m services.identity.music_assistant_admin \\
        stale-urls --retired-host old-ha.example.com
    docker exec -w /app sharedllm_identity python -m services.identity.music_assistant_admin \\
        stale-urls --retired-host old-ha.example.com --apply

    # Give every person without a Music Assistant login their own account on the household server.
    docker exec -w /app sharedllm_identity python -m services.identity.music_assistant_admin accounts
    docker exec -w /app sharedllm_identity python -m services.identity.music_assistant_admin accounts --apply

With no ``--retired-host`` the hosts HA_URL_REDIRECTS redirects from are used (they are retired by
definition); the dry run prints which.
"""
from __future__ import annotations

import argparse
import logging
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="music_assistant_admin", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    stale = sub.add_parser("stale-urls", help="clear saved Music Assistant URLs on retired hosts")
    stale.add_argument("--retired-host", action="append", default=[], help="a retired host name (repeatable)")
    stale.add_argument("--apply", action="store_true", help="clear them (default: only list)")
    acc = sub.add_parser("accounts", help="give each person their own household Music Assistant account")
    acc.add_argument("--user", action="append", default=[], help="only these usernames (repeatable)")
    acc.add_argument("--apply", action="store_true", help="make the accounts (default: only list)")
    args = parser.parse_args(argv)

    logging.disable(logging.INFO)  # the service's own startup logging is noise here
    from sqlmodel import Session, select

    from services.identity import main as identity
    from services.identity import music_assistant
    from services.identity.models import User

    with Session(identity.engine) as session:
        if args.cmd == "stale-urls":
            hosts = identity._ma_retired_hosts(args.retired_host)
            if not hosts:
                print("No retired host given (and HA_URL_REDIRECTS names none). Use --retired-host.")
                return 2
            print(f"Retired host(s): {', '.join(hosts)}")
            plan = music_assistant.stale_url_plan(list(session.exec(select(User)).all()), hosts)
            if not plan:
                print("Nobody has a Music Assistant URL on those hosts.")
                return 0
            for row in plan:
                print(f"  {row['username']} ({row['host']})")
            if not args.apply:
                print(f"{len(plan)} account(s) would follow the household Music Assistant. Re-run with --apply.")
                return 0
            cleared = identity._ma_clear_stale(session, hosts)
            print(f"Cleared {len(cleared)} URL(s); those people now use the household Music Assistant.")
            return 0

        if not args.apply:
            server = music_assistant.household_server(fresh=True)
            print(f"Household Music Assistant: {server.get('url') or 'none (' + str(server.get('error')) + ')'}")
            wanted = {u.lower() for u in args.user}
            for row in identity._ma_accounts_plan(session):
                if not wanted or row["username"] in wanted:
                    print(f"  {row['username']}: {row['action']}")
            print("needs_account = would get their own account. Re-run with --apply.")
            return 0
        for row in identity._ma_provision(session, args.user or None):
            if row["result"] != "unchanged" or row["action"] == "needs_account":
                extra = f" ({row['message']})" if row.get("message") else ""
                print(f"  {row['username']}: {row['result']}{extra}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
