"""Tests for the execution container toolchain probe."""

from typing import ClassVar

import pytest

from services.execution import toolchain


def _fake_run(stdout_lines):
    class _Proc:
        stdout = "\n".join(stdout_lines)

    def run(cmd, **kwargs):
        assert kwargs.get("timeout") == 5
        return _Proc()

    return run


def test_discover_toolchain_reports_only_installed_binaries():
    installed = {"python3": "/usr/bin/python3", "pandoc": "/usr/bin/pandoc"}

    def which(binary):
        return installed.get(binary)

    def run(cmd, **kwargs):
        class _Proc:
            stdout = "3.11.2\n"

        return _Proc()

    caps = toolchain.discover_toolchain(which=which, run=run)

    names = {c["name"] for c in caps}
    assert names == {"python3", "pandoc"}
    for cap in caps:
        assert cap["type"] == "binary"
        assert cap["version"] == "3.11.2"
        assert cap["tags"]
        assert cap["description"]


def test_discover_toolchain_parses_first_stdout_line():
    def which(binary):
        return "/usr/bin/pandoc" if binary == "pandoc" else None

    caps = toolchain.discover_toolchain(
        which=which, run=_fake_run(["3.1.11", "Copyright (C) 2024"])
    )

    assert len(caps) == 1
    assert caps[0]["name"] == "pandoc"
    assert caps[0]["version"] == "3.1.11"


def test_probe_version_returns_empty_on_failure():
    def run(cmd, **kwargs):
        raise FileNotFoundError("no such binary")

    assert toolchain._probe_version("nope", ["--version"], run=run) == ""
    assert toolchain._probe_version("nope", ["--version"], run=run) == ""


def test_probe_version_falls_through_flags():
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd[1])
        if cmd[1] == "-v":
            class _Proc:
                stdout = "1.18.2\n"

            return _Proc()
        class _Proc:
            stdout = ""

        return _Proc()

    assert toolchain._probe_version("pdftotext", ["--version", "-v"], run=run) == "1.18.2"
    assert calls == ["--version", "-v"]


def test_probe_version_truncates_to_60_chars():
    def run(cmd, **kwargs):
        class _Proc:
            stdout = "x" * 200 + "\n"

        return _Proc()

    version = toolchain._probe_version("git", ["--version"], run=run)
    assert len(version) == 60
    assert version == "x" * 60


def test_probe_version_empty_output_gives_unknown():
    def which(binary):
        return "/usr/bin/python3"

    caps = toolchain.discover_toolchain(which=which, run=_fake_run([]))

    assert caps[0]["version"] == ""
    assert "Version: unknown." in caps[0]["description"]


class _FakeSession:
    """aiohttp.ClientSession stand-in: each post() runs the next scripted step."""

    calls = 0
    steps: ClassVar[list] = []
    timeout = None

    def __init__(self, timeout=None):
        type(self).timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def post(self, url, **kwargs):
        type(self).calls += 1
        step = type(self).steps.pop(0)
        class _Resp:
            status = 200

            async def json(self):
                return {"synced": 1}

            async def __aenter__(self):
                if isinstance(step, BaseException):
                    raise step
                return self

            async def __aexit__(self, *exc):
                return False

        return _Resp()


@pytest.fixture
def fake_rag(monkeypatch):
    import aiohttp

    import services.config as cfg

    _FakeSession.calls = 0
    monkeypatch.setattr(aiohttp, "ClientSession", _FakeSession)
    monkeypatch.setattr(cfg, "RAG_SVC_URL", "http://rag:8004", raising=False)
    monkeypatch.setattr(toolchain, "discover_toolchain", lambda: [{"name": "git", "type": "binary"}])

    async def _no_sleep(_s):
        return None

    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    return _FakeSession


@pytest.mark.asyncio
async def test_sync_waits_for_rag_to_finish_embedding(fake_rag):
    """RAG answers only after embedding; a short client timeout gave up every time."""
    fake_rag.steps = ["ok"]
    result = await toolchain.sync_toolchain_to_rag()
    assert result["status"] == "SUCCESS"
    assert fake_rag.timeout.total >= 120


@pytest.mark.asyncio
async def test_a_timeout_is_not_resent(fake_rag):
    """Resending after a timeout only queued another copy of the same embed."""
    fake_rag.steps = [TimeoutError(), "ok"]
    result = await toolchain.sync_toolchain_to_rag()
    assert result["status"] == "ERROR"
    assert fake_rag.calls == 1
    assert "no answer" in result["error"]


@pytest.mark.asyncio
async def test_an_error_with_no_message_still_says_what_failed(fake_rag):
    class Silent(Exception):
        def __str__(self):
            return ""

    fake_rag.steps = [Silent()] * 6
    result = await toolchain.sync_toolchain_to_rag()
    assert result["status"] == "ERROR"
    assert result["error"].startswith("Silent")
    assert fake_rag.calls == 6
