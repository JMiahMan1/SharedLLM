"""Unit tests for the Raven tool-builder decision logic."""
import os

os.environ.setdefault("INTERNAL_SECRET", "test-secret")

from services.gateway.tool_builder import decide, scaffold_source, slugify


def test_use_existing_single_tool():
    d = decide("open a pull request on GitHub")
    assert d["decision"] == "use_existing"
    assert d["tool"] == "sharedllm_gh"


def test_chain_multiple_tools():
    d = decide("create a github repository and commit my code")
    assert d["decision"] == "chain"
    tools = [s["tool"] for s in d["steps"]]
    assert "sharedllm_gh" in tools
    assert "GitOperationRequest" in tools
    assert len(d["steps"]) <= 3


def test_build_when_no_existing_tool_fits():
    d = decide("send a Slack message when the nightly build finishes")
    assert d["decision"] == "build"
    assert d["tool_path"].startswith("tools/")
    assert d["tool_path"].endswith(".py")
    assert "Implement run()" in d["instruction"]


def test_build_empty_capability_still_builds():
    d = decide("")
    assert d["decision"] == "build"
    assert d["slug"]


def test_slugify_is_filesystem_safe():
    assert slugify("Send a Slack Message!!!") == "send-a-slack-message"
    assert "/" not in slugify("a/b/c")
    assert slugify("") == "tool"


def test_scaffold_source_runs_and_signals_not_implemented():
    import subprocess
    import sys
    import tempfile
    from pathlib import Path

    src = scaffold_source("send a slack message", "slack-ping")
    assert "def run(argv" in src
    assert 'raise NotImplementedError' in src
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "tools" / "slack-ping.py"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)
        # The scaffold must run and signal "not implemented" (exit 2) until
        # Raven fills in run().
        r = subprocess.run(
            [sys.executable, str(p), "arg"],
            capture_output=True,
            text=True,
        )
        assert r.returncode == 2
        assert "NOT IMPLEMENTED" in r.stderr
