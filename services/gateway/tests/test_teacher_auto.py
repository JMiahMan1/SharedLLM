"""The hands-off teacher pass: lessons, gated evaluation, snapshots, sync health.

Nothing here reaches a model, RAG or telemetry: the RAG writer, the model and
the admin notifier are fakes. The tests pin what the owner asked for: lessons
are written without anyone pressing a button, the evaluation only runs on an
idle card, a snapshot is exported (and announced, counts only) every N new
verified traces, and a failure notifies the admins once, not on every retry.
"""
from __future__ import annotations

import asyncio
import json

import pytest
import teacher_samples as samples
from fastapi.testclient import TestClient

from services.gateway.main import app
from services.gateway.trainer import dream as dream_mod
from services.gateway.trainer import runner, teacher, teacher_auto
from services.gateway.trainer import teacher_scrub as scrub
from services.gateway.trainer import teacher_traces as traces

SETTINGS = {
    "trainer_teacher_auto": "true",
    "trainer_teacher_eval_interval_hours": "24",
    "trainer_teacher_snapshot_every": "1",
    "trainer_teacher_sync_stale_minutes": "180",
}
IDLE = {"reachable": True, "ready": True, "reasons": [], "models": {"chosen": "local-moe"}}
BUSY = {"reachable": True, "ready": False, "reasons": ["2 requests running"]}
T0 = 1_800_000_000.0


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "DATA_DIR", tmp_path)
    return tmp_path


class _Written(list):
    """System-learnings writes; SIM ledger writes are kept on ``.sim``."""

    def __init__(self) -> None:
        super().__init__()
        self.sim: list[dict] = []


@pytest.fixture
def rag(monkeypatch):
    written = _Written()

    async def fake_request(method, path, **kwargs):
        if path == "/rag/sim/history":
            return {"problems": [], "count": 0}
        if path == "/rag/sim/record":
            written.sim.append(kwargs.get("body") or {})
            return {}
        written.append(kwargs.get("body") or {})
        return {}

    monkeypatch.setattr(dream_mod, "request", fake_request)
    return written


def _import():
    session = {"id": "ses_synthetic01", "directory": samples.ROOT_DIR, "model": json.dumps(samples.MODEL)}
    config = scrub.ScrubConfig(names=("Placeholderperson",), names_loaded=True)
    rows = [scrub.scrub_trace(t, config) for t in traces.opencode_tasks(session, samples.opencode_messages())]
    teacher.import_traces(rows, uploader="admin")


class Notices:
    def __init__(self, ok: bool = True):
        self.sent: list[dict] = []
        self.ok = ok

    async def __call__(self, notice: dict) -> bool:
        self.sent.append(notice)
        return self.ok


async def _answer(case):
    return "Read services/gateway/pager.py first, then run pytest services/gateway/tests/test_pager.py."


async def _prompt():
    return "You are Raven."


def _pass(settings=None, **kwargs):
    kwargs.setdefault("failures_fetch", False)
    kwargs.setdefault("hardware", IDLE)
    kwargs.setdefault("complete_fn", _answer)
    kwargs.setdefault("system_prompt", _prompt)
    kwargs.setdefault("notify", Notices())
    return asyncio.run(teacher_auto.run_pass(settings or SETTINGS, **kwargs))


def test_a_pass_writes_lessons_evaluates_and_snapshots_without_a_button(data_dir, rag):
    _import()
    notices = Notices()

    done = _pass(now=T0, notify=notices)

    assert done["lessons_written"] == 1 and len(rag) == 1
    (sim,) = rag.sim
    assert sim["harness"] == "raven" and sim["category"] == "teacher_lesson"
    assert sim["outcome_status"] == "improved"
    assert done["eval"] == "1/1 passed"
    assert done["snapshot"] in ("1 train / 0 eval rows", "0 train / 1 eval rows")
    health = teacher_auto.health()
    assert health["eval"]["pass_rate"] == 1.0 and health["lessons"]["written"] == 1
    assert health["snapshot"]["verified_total"] == 1
    (ready,) = notices.sent
    assert ready["title"] == "Teacher dataset snapshot ready"
    assert "Fix the off-by-one" not in json.dumps(ready), "a notice carries counts, never content"
    assert health["failures"] == []


def test_the_evaluation_waits_for_an_idle_card_and_is_not_repeated_until_due(data_dir, rag):
    _import()
    assert _pass(now=T0, hardware=BUSY)["eval"] == "host busy; waiting for an idle card"
    assert teacher_auto.health()["failures"] == [], "a busy card is not a failure"
    assert _pass(now=T0 + 60)["eval"] == "1/1 passed"
    assert "eval" not in _pass(now=T0 + 3600), "not due again for 24 hours"
    assert _pass(now=T0 + 25 * 3600)["eval"] == "1/1 passed"
    assert len(teacher.score_history()) == 2


def test_a_snapshot_waits_for_n_new_verified_traces(data_dir, rag):
    _import()
    settings = {**SETTINGS, "trainer_teacher_snapshot_every": "100"}

    assert "snapshot" not in _pass(settings, now=T0)


def test_a_failure_notifies_once_until_it_recovers(data_dir, rag):
    _import()
    notices = Notices()
    teacher_auto.record_heartbeat({"command": "sync", "ok": True}, uploader="agent", now=T0)

    _pass(now=T0 + 4 * 3600, notify=notices)
    _pass(now=T0 + 5 * 3600, notify=notices)

    stale = [n for n in notices.sent if n["tag"].endswith(":sync")]
    assert len(stale) == 1
    assert "no sync from the Mac for 240 minutes" in stale[0]["body"]
    assert teacher_auto.health()["failures"][0]["part"] == "sync"

    teacher_auto.record_heartbeat({"command": "sync", "ok": True}, uploader="agent", now=T0 + 5 * 3600 + 60)
    _pass(now=T0 + 5 * 3600 + 120, notify=notices)
    assert teacher_auto.health()["failures"] == []


def test_an_undelivered_notice_is_retried(data_dir, rag):
    teacher_auto.record_heartbeat({"command": "sync", "ok": False, "error": "the gateway answered 502"},
                                  uploader="agent", now=T0)
    down = Notices(ok=False)
    _pass(now=T0 + 60, notify=down)
    up = Notices()
    _pass(now=T0 + 120, notify=up)
    _pass(now=T0 + 180, notify=up)

    assert len(down.sent) == 1 and len(up.sent) == 1
    assert "the gateway answered 502" in up.sent[0]["body"]


def test_a_heartbeat_error_is_scrubbed_before_it_is_stored(data_dir):
    secret = "sk-or-v1-" + "0a" * 32
    teacher_auto.record_heartbeat({"command": "sync", "ok": False, "error": f"key {secret} refused"},
                                  uploader="agent", now=T0)

    assert secret not in json.dumps(teacher_auto.health())


def test_missing_settings_are_a_failure_that_names_them(data_dir):
    notices = Notices()

    done = _pass({"trainer_teacher_auto": "true"}, notify=notices, now=T0)

    assert "trainer_teacher_snapshot_every" in done["error"]
    assert notices.sent[0]["tag"] == "admin:trainer:teacher:settings"


def test_the_pass_can_be_turned_off(data_dir, rag):
    _import()

    done = _pass({**SETTINGS, "trainer_teacher_auto": "false"}, now=T0)

    assert done["skipped"] == "trainer_teacher_auto is off"
    assert rag == []


def test_an_unreachable_host_is_a_failure(data_dir, rag):
    _import()
    notices = Notices()

    _pass(now=T0, hardware={"reachable": False, "reasons": ["connection refused"]}, notify=notices)

    assert any(n["tag"].endswith(":eval") for n in notices.sent)


def test_names_are_built_from_the_family_directory():
    directory = [
        {"username": "default", "display_name": "Default"},
        {"username": "pat", "display_name": "Pat Placeholder", "nextcloud_user": "pat.p"},
        {"username": "jo", "display_name": "Jo"},
    ]

    words = teacher.household_name_words(directory)

    assert "Pat Placeholder" in words and "Placeholder" in words and "pat" in words
    assert "Default" not in words and "jo" not in words, "service accounts and short words are left out"


# --- routes ----------------------------------------------------------------


def test_heartbeat_and_status_routes(client, data_dir):
    resp = client.post("/api/trainer/teacher/heartbeat", json={"command": "sync", "ok": True, "queue_length": 2})
    assert resp.status_code == 200, resp.text

    status = client.get("/api/trainer/teacher/status").json()
    assert status["sync"]["queue_length"] == 2
    assert client.get("/api/trainer/teacher").json()["health"]["sync"]["ok"] is True
    assert TestClient(app).get("/api/trainer/teacher/status").status_code in (401, 403)


def test_the_names_route_reads_the_family_directory_with_the_callers_key(client, monkeypatch):
    seen: dict = {}

    class _Resp:
        status = 200

        async def json(self):
            return [{"username": "pat", "display_name": "Pat Placeholder"}]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class _Client:
        def get(self, url, headers=None, timeout=None):
            seen.update(url=url, headers=headers)
            return _Resp()

    from contextlib import asynccontextmanager

    from services.gateway import http_client

    @asynccontextmanager
    async def fake_shared():
        yield _Client()

    monkeypatch.setattr(http_client, "shared_http_client", fake_shared)
    resp = client.get("/api/trainer/teacher/scrub-names", headers={"Authorization": "Bearer uploader-key"})

    assert resp.status_code == 200, resp.text
    assert "Pat Placeholder" in resp.json()["names"]
    assert seen["url"].endswith("/api/users/family-directory")
    assert seen["headers"] == {"Authorization": "Bearer uploader-key"}
