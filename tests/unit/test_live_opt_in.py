"""The live-test guard in the root conftest.py.

tests/integration (and the other live suites) read SERVER_IP, INTERNAL_SECRET
and GH_TOKEN from the environment, and conftest.py loads .env into it. Before
this guard a bare `pytest` on a developer machine posted real Raven missions to
the deployed stack. These tests pin the two halves of the fix: live tests are
skipped without an opt-in, and only the shell (or --run-live) can opt in, never
an env file.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def root_conftest(request):
    """The root conftest.py as pytest loaded it. Looked up through the plugin
    manager: sys.modules["conftest"] is whichever conftest was imported last."""
    for plugin in request.config.pluginmanager.get_plugins():
        path = getattr(plugin, "__file__", None)
        if path and Path(path).resolve() == ROOT / "conftest.py":
            return plugin
    raise AssertionError("root conftest.py is not loaded")


class _Item:
    def __init__(self, path: Path, markers: tuple[str, ...] = ()):
        self.path = path
        self.keywords: dict[str, object] = {}
        self._markers = set(markers)
        self.added: list[object] = []

    def get_closest_marker(self, name):
        return SimpleNamespace(name=name) if name in self._markers else None

    def add_marker(self, marker):
        self.added.append(marker)


def _config():
    return SimpleNamespace(getoption=lambda name, default=False: False)


def _skipped(item: _Item) -> bool:
    return any("deployed stack" in str(getattr(m, "kwargs", {}).get("reason", "")) for m in item.added)


@pytest.mark.parametrize(
    "rel",
    [
        "tests/integration/test_regression.py",
        "test/local/test_rag_sync.py",
        "test/integration/test_rag.py",
        "scripts/test_live_api.py",
    ],
)
def test_live_directories_are_skipped_without_an_opt_in(monkeypatch, root_conftest, rel):
    monkeypatch.delenv("SHAREDLLM_RUN_LIVE", raising=False)
    item = _Item(ROOT / rel)
    root_conftest.pytest_collection_modifyitems(_config(), [item])
    assert _skipped(item)


def test_a_live_marker_anywhere_is_skipped_without_an_opt_in(monkeypatch, root_conftest):
    monkeypatch.delenv("SHAREDLLM_RUN_LIVE", raising=False)
    item = _Item(ROOT / "services/tests/test_gateway_coding.py", markers=("live",))
    root_conftest.pytest_collection_modifyitems(_config(), [item])
    assert _skipped(item)


def test_ordinary_tests_are_not_touched(monkeypatch, root_conftest):
    monkeypatch.delenv("SHAREDLLM_RUN_LIVE", raising=False)
    item = _Item(ROOT / "tests/unit/test_live_opt_in.py")
    root_conftest.pytest_collection_modifyitems(_config(), [item])
    assert not _skipped(item)


def test_the_opt_in_lets_live_tests_through(monkeypatch, root_conftest):
    monkeypatch.setenv("SHAREDLLM_RUN_LIVE", "1")
    item = _Item(ROOT / "tests/integration/test_regression.py")
    root_conftest.pytest_collection_modifyitems(_config(), [item])
    assert not _skipped(item)


_PROBE = (
    "import json, os, runpy, sys; sys.path.insert(0, sys.argv[1]); "
    "runpy.run_path(os.path.join(sys.argv[1], 'conftest.py')); "
    "print(json.dumps({k: os.environ.get(k) for k in ('SHAREDLLM_RUN_LIVE', 'SERVER_IP')}))"
)


def _load_conftest_with_env_file(tmp_path: Path, env_file: str, runner_env: dict[str, str]) -> dict:
    shutil.copy(ROOT / "conftest.py", tmp_path / "conftest.py")
    (tmp_path / ".env").write_text(env_file, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in ("SHAREDLLM_RUN_LIVE", "SERVER_IP")}
    env.update(runner_env)
    out = subprocess.run(
        [sys.executable, "-c", _PROBE, str(tmp_path)],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        cwd=tmp_path,
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_an_env_file_cannot_opt_in(tmp_path):
    seen = _load_conftest_with_env_file(tmp_path, "SHAREDLLM_RUN_LIVE=1\nSERVER_IP=192.0.2.10\n", {})
    # The .env still loads everything else, but not the opt-in.
    assert seen["SERVER_IP"] == "192.0.2.10"
    assert seen["SHAREDLLM_RUN_LIVE"] is None


def test_the_shell_can_opt_in(tmp_path):
    seen = _load_conftest_with_env_file(tmp_path, "", {"SHAREDLLM_RUN_LIVE": "true"})
    assert seen["SHAREDLLM_RUN_LIVE"] == "1"
