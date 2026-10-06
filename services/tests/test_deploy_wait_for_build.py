"""Pin the image-build gate in ``scripts/deploy_remote.sh``.

The gate exists so a deploy never pulls an image that CI has not finished
building. It failed in production the moment it was written: ``gh run list``
reports ``status`` and ``conclusion`` as separate fields, GitHub flips
``status`` to ``completed`` before it populates ``conclusion``, and the
script read the two from two different API calls. It therefore paired a
completed status with an empty conclusion and aborted a deploy whose build
was still finalising.

The gate also accepted whichever run happened to be newest on the branch,
so two pushes in quick succession could deploy an image built from the
wrong commit. Both are regressions worth a test rather than a memory.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "deploy_remote.sh"

SHA = "abcdef1234567890abcdef1234567890abcdef12"
OTHER_SHA = "9999999999999999999999999999999999999999"


def _function_source() -> str:
    """Extract the ``wait_for_build`` shell function from the deploy script.

    The function is sourced rather than the whole script run, because the
    script's top level pushes to a real host. Reading it out of the file also
    means this test fails if the function is renamed or deleted.
    """
    text = SCRIPT.read_text()
    start = text.index("wait_for_build() {")
    end = text.index("\n}\n", start) + len("\n}\n")
    return text[start:end]


def _run(poll_payloads: list[list[dict]], *, branch: str = "microservices", sha: str = SHA) -> subprocess.CompletedProcess:
    """Drive the gate against a stubbed ``gh`` that replays ``poll_payloads``.

    Each element of ``poll_payloads`` is what one poll of ``gh run list``
    returns. Polls past the end repeat the final entry, so a test can leave
    the last payload in place and let the loop run out its attempts.
    """
    stub_dir = Path(subprocess.run(
        ["mktemp", "-d"], capture_output=True, text=True, check=True,
    ).stdout.strip())
    for index, payload in enumerate(poll_payloads):
        (stub_dir / f"{index}.json").write_text(json.dumps(payload))

    counter = stub_dir / "poll"
    counter.write_text("0")

    harness = f"""
set -u
_poll() {{
  local n
  n=$(cat {counter})
  local last=$(($(ls {stub_dir} | grep -c '^[0-9]*\\.json$') - 1))
  if [ "$n" -gt "$last" ]; then n=$last; fi
  echo $((n + 1)) > {counter}
  cat {stub_dir}/${{n}}.json
}}
gh() {{ _poll; }}
sleep() {{ :; }}
{_function_source()}
wait_for_build "{branch}" "{sha}"
"""

    script = stub_dir / "harness.sh"
    script.write_text(harness)
    return subprocess.run(
        ["bash", str(script)], capture_output=True, text=True, timeout=120,
    )


def _run_record(*, status: str, conclusion: str | None, sha: str = SHA) -> list[dict]:
    """Build a one-poll payload list for a single run."""
    record: dict = {"headSha": sha, "status": status}
    if conclusion is not None:
        record["conclusion"] = conclusion
    return [[record]]


def test_a_successful_build_passes():
    result = _run(_run_record(status="completed", conclusion="success"))
    assert result.returncode == 0
    assert "[OK] Build & Push Images" in result.stdout


def test_a_completed_run_with_no_conclusion_is_still_settling_not_a_failure():
    """The regression: an empty conclusion must be waited out, not failed."""
    result = _run([
        _run_record(status="completed", conclusion=None)[0],
        _run_record(status="completed", conclusion="success")[0],
    ])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "[FAIL]" not in result.stdout
    assert "completed successfully" in result.stdout


def test_a_genuinely_failed_build_still_fails():
    result = _run(_run_record(status="completed", conclusion="failure"))
    assert result.returncode == 1
    assert "[FAIL] Build & Push Images" in result.stdout
    assert "failure" in result.stdout


def test_a_build_for_a_different_commit_is_not_accepted():
    """Two pushes in quick succession must not cross-contaminate."""
    result = _run(_run_record(sha=OTHER_SHA, status="completed", conclusion="success"))
    assert result.returncode == 1
    assert "[OK]" not in result.stdout
    assert "Timeout" in result.stdout


def test_the_expected_run_appearing_late_is_waited_for():
    result = _run([
        [],
        _run_record(status="in_progress", conclusion=None)[0],
        _run_record(status="completed", conclusion="success")[0],
    ])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "completed successfully" in result.stdout


def test_the_gate_is_asked_for_a_branch_and_a_commit():
    """Guards the call site: the gate must receive what it needs to be safe.

    It used to be invoked bare, before the branch was resolved, so
    ``DEPLOY_BRANCH`` and a detached HEAD never reached it and it had no way
    to know which commit it was gating.
    """
    text = SCRIPT.read_text()
    calls = re.findall(r"^\s*wait_for_build\b(.*)$", text, re.MULTILINE)
    invoked = [c for c in calls if not c.strip().startswith("(")]
    assert invoked, "wait_for_build is never called with arguments"
    assert all('"$BRANCH"' in c and '"$EXPECT_SHA"' in c for c in invoked), invoked


def test_the_expected_commit_is_resolved_before_the_gate_runs():
    text = SCRIPT.read_text()
    sha_at = text.index("EXPECT_SHA=$(git rev-parse HEAD")
    call_at = text.index('wait_for_build "$BRANCH" "$EXPECT_SHA"')
    assert sha_at < call_at, "the gate runs before it knows which commit to wait for"


def test_the_branch_is_resolved_before_the_gate_runs():
    text = SCRIPT.read_text()
    branch_at = text.index('BRANCH="${DEPLOY_BRANCH:-microservices}"')
    call_at = text.index('wait_for_build "$BRANCH" "$EXPECT_SHA"')
    assert branch_at < call_at, "the gate runs before the branch is known"