"""deploy_remote.sh must not report success for a service on the wrong image.

A deploy printed "[OK] Deployment Verification Successful" although homehub
never changed image: the script was edited while it ran (bash reads scripts
incrementally), hit a syntax error, and carried on to the OK line. Two guards
are pinned here: the script is one brace group parsed before it runs, and
``verify_deployed_revisions`` compares every deployed service's image revision
with the commit CI built, failing loudly on any mismatch.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "deploy_remote.sh"


def _function_source() -> str:
    text = SCRIPT.read_text()
    start = text.index("verify_deployed_revisions() {")
    end = text.index("\n}\n", start) + len("\n}\n")
    return text[start:end]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> dict[str, str]:
    """A repo with: c1 changes gateway, c2 changes homehub, c3 changes docs."""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    shas = {}
    for name, path in (("c1", "services/gateway/x.py"), ("c2", "services/homehub/x.py"), ("c3", "docs/x.md")):
        f = tmp_path / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(name)
        _git(tmp_path, "add", ".")
        _git(tmp_path, "commit", "-q", "-m", name)
        shas[name] = _git(tmp_path, "rev-parse", "HEAD")
    shas["dir"] = str(tmp_path)
    return shas


def _verify(repo: dict[str, str], expect: str, report: str, *services: str) -> subprocess.CompletedProcess:
    log = Path(repo["dir"]) / "remote.log"
    log.write_text(report)
    harness = f"set -euo pipefail\n{_function_source()}\nverify_deployed_revisions \"$@\"\n"
    return subprocess.run(
        ["bash", "-c", harness, "verify", expect, str(log), *services],
        cwd=repo["dir"], capture_output=True, text=True,
    )


def test_every_service_on_the_built_commit_passes(repo):
    out = _verify(repo, repo["c3"], f"REVISION gateway {repo['c3']}\nREVISION homehub {repo['c3']}\n", "gateway", "homehub")
    assert out.returncode == 0, out.stdout + out.stderr


def test_a_service_left_on_an_older_image_it_has_changed_since_fails_and_is_named(repo):
    # homehub changed in c2, but the server still runs the c1 image.
    out = _verify(repo, repo["c3"], f"REVISION gateway {repo['c3']}\nREVISION homehub {repo['c1']}\n", "gateway", "homehub")
    assert out.returncode != 0
    assert f"homehub is running revision {repo['c1'][:12]}, expected {repo['c3'][:12]}" in out.stdout


def test_an_unchanged_service_may_keep_the_image_of_its_last_change(repo):
    # gateway has not changed since c1, so CI never rebuilt it: c1 is right.
    out = _verify(repo, repo["c3"], f"REVISION gateway {repo['c1']}\n", "gateway")
    assert out.returncode == 0, out.stdout


def test_a_service_the_server_never_reported_fails(repo):
    out = _verify(repo, repo["c3"], f"REVISION gateway {repo['c3']}\n", "gateway", "homehub")
    assert out.returncode != 0
    assert "homehub: the server reported no image revision" in out.stdout


def test_an_image_without_a_revision_label_fails(repo):
    out = _verify(repo, repo["c3"], "REVISION homehub none\n", "homehub")
    assert out.returncode != 0
    assert "no org.opencontainers.image.revision label" in out.stdout


def test_upstream_images_are_skipped(repo):
    out = _verify(repo, repo["c3"], "REVISION ntfy upstream\n", "ntfy")
    assert out.returncode == 0


def test_no_built_commit_means_no_verification_and_a_failure(repo):
    out = _verify(repo, "", f"REVISION gateway {repo['c3']}\n", "gateway")
    assert out.returncode != 0


def test_a_revision_unknown_to_git_fails(repo):
    out = _verify(repo, repo["c3"], "REVISION gateway " + "f" * 40 + "\n", "gateway")
    assert out.returncode != 0


def test_the_script_is_parsed_whole_before_it_runs():
    lines = SCRIPT.read_text().rstrip("\n").splitlines()
    assert lines[-2:] == ["}", "exit"], "deploy_remote.sh must end with the closing brace group and exit"
    assert "\n{\n" in SCRIPT.read_text(), "deploy_remote.sh must open a top-level brace group"
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_the_verdict_runs_the_revision_check_before_ok():
    text = SCRIPT.read_text()
    verdict = text[text.rindex('DEPLOY_STEP="verifying image revisions"'):]
    assert verdict.index("verify_deployed_revisions") < verdict.index("[OK] Deployment Verification Successful.")
    assert "|| true" not in verdict.split("[OK] Deployment Verification Successful.")[0]
