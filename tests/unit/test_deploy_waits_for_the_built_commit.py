"""deploy_remote.sh waits for the commit the images were built from.

A docs-only HEAD gets no "Build & Push Images" run (the workflow has a
`paths` filter), so waiting for HEAD hung every deploy for 15 minutes and then
failed. The paths the script looks at must stay the workflow's.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _workflow_paths() -> set[str]:
    text = (ROOT / ".github/workflows/build-images.yml").read_text()
    block = text.split("push:", 1)[1].split("pull_request:", 1)[0]
    return {p.rstrip("/*") for p in re.findall(r"-\s*'([^']+)'", block.split("paths:", 1)[1])}


def test_the_deploy_script_follows_the_build_workflows_paths():
    script = (ROOT / "scripts/deploy_remote.sh").read_text()
    m = re.search(r"EXPECT_SHA=\$\(git log -1 --format=%H HEAD -- (.+?) 2>/dev/null", script, re.S)
    assert m, "deploy_remote.sh no longer picks the built commit"
    script_paths = set(m.group(1).replace("\\\n", " ").split())
    assert script_paths == _workflow_paths()
