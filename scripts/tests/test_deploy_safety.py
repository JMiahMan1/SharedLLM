#!/usr/bin/env python3
"""The deploy scripts must not destroy host state or the whole stack.

Both defects here were found after the fact, in production, and neither is
visible from reading the happy path:

1. `.env` was synced with `cat >`, which truncates. It is untracked and
   gitignored, so the host's copy is the only record of anything set on the
   host, and every deploy silently replaced it with the developer's.

2. `docker compose up -d --force-recreate` was called with no service names,
   which recreates every container in the project -- including unrelated
   wsbox-* ones -- so each deploy briefly took the whole stack down.

These assert the *text* of the scripts. That is the right level here: the bug
was in what the script says to a remote shell, and there is no unit under test
to call. Parsing shell by hand would test our parser, not the deploy.
"""
import re
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
SCRIPTS = [
    SCRIPTS_DIR / "deploy_local_build.sh",
    SCRIPTS_DIR / "deploy_remote.sh",
]

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}{' -- ' + detail if detail else ''}")
        failures.append(name)


def code_only(src: str) -> str:
    """Strip comments.

    Necessary, not cosmetic: the comments I added explaining these bugs quote
    the exact command they are warning about ("docker compose up
    --force-recreate recreates every container..."), so matching the raw text
    matched my own explanation and reported a false failure. A check that
    cannot distinguish a warning from the behaviour it warns about is not
    measuring the behaviour.
    """
    out = []
    for line in src.splitlines():
        stripped = line.lstrip()
        # Inside a quoted heredoc a '#' is literal text, not a comment.
        if stripped.startswith("#"):
            continue
        out.append(line.split(" #", 1)[0] if " #" in line and '"' not in line.split(" #", 1)[0][-40:] else line)
    return "\n".join(out)


print("deploy script safety")
for path in SCRIPTS:
    label = path.name
    src = path.read_text()
    code = code_only(src)

    # 1. .env must not be in the sync list.
    sync_lists = re.findall(r'for NON_GIT_FILE in ([^\n]+); do', code)
    env_listed = any('".env"' in lst for lst in sync_lists)
    check(f"{label}: .env is not in the file-sync list", not env_listed,
          f"found .env in {sync_lists}")

    # 2. No bare `cat >` of .env anywhere.
    cat_env = re.search(r'cat > \'?\$DIR/\.env', code)
    check(f"{label}: never truncates the host .env", cat_env is None)

    # 3. compose up must be scoped to services.
    up = re.search(r'docker compose up[^\n]*', code)
    has_services = up is not None and "$SERVICES" in up.group(0)
    check(f"{label}: docker compose up is scoped to $SERVICES", has_services,
          up.group(0).strip() if up else "no compose up found")

    # 4. compose pull (remote script only) also scoped, so images still update.
    pull = re.search(r'docker compose pull[^\n]*', code)
    if pull:
        check(f"{label}: docker compose pull is scoped too", "$SERVICES" in pull.group(0),
              pull.group(0).strip())

    # 5. The service list must never fall back to "everything".
    fallback_all = re.search(r'^\s*SERVICES=""\s*$', code, re.M)
    check(f"{label}: no empty SERVICES fallback that would recreate everything",
          fallback_all is None, fallback_all.group(0).strip() if fallback_all else "")

    check(f"{label}: syntax is valid", subprocess.run(["bash", "-n", str(path)]).returncode == 0)

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {', '.join(failures)}")
    sys.exit(1)
print("all deploy-script safety checks passed")
