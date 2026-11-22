"""``scripts/collect_teacher_traces.py``: read-only, scrub before write, counts-only dry run.

The opencode database and the Cline sessions are synthetic and carry secrets
from the shared fixture. Nothing the script writes, prints or posts may contain
them, the source database is never modified, and a failed upload is queued
(scrubbed) and retried instead of being lost.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib
import time

import pytest
import teacher_fixture
import teacher_samples as samples

ROOT = pathlib.Path(__file__).resolve().parents[3]
FIX = teacher_fixture.load()
SECRET = FIX["secrets"]["anthropic_key"]["value"]
NAME = FIX["names"][0]


def _load():
    spec = importlib.util.spec_from_file_location("collect_teacher_traces", ROOT / "scripts" / "collect_teacher_traces.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


collect = _load()


@pytest.fixture
def world(tmp_path):
    db = samples.make_opencode_db(tmp_path / "opencode.db", extra_output=f"\nwarning: key {SECRET} for {NAME}")
    cline = tmp_path / "cline"
    samples.cline_session(cline)
    names = tmp_path / "names.txt"
    names.write_text("\n".join(FIX["names"]), encoding="utf-8")
    key = tmp_path / "key"
    key.write_text("synthetic-key-value\n", encoding="utf-8")
    return {"tmp": tmp_path, "db": db, "cline": cline, "names": names, "key": key}


def _base(world, *extra: str) -> list[str]:
    return [
        "--opencode-db", str(world["db"]), "--cline-dir", str(world["cline"]),
        "--names-file", str(world["names"]), "--work-dir", str(world["tmp"] / "work"),
        "--exclude-file", str(world["tmp"] / "no-exclude.txt"), *extra,
    ]


def _digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_a_dry_run_prints_counts_and_nothing_else(world, capsys):
    before = _digest(world["db"])

    code = collect.main(["collect", *_base(world), "--dry-run"])

    out = capsys.readouterr().out
    assert code == 0
    summary = json.loads(out)
    assert summary["tasks"] == 4 and summary["sessions"] == 2
    assert summary["by_source"] == {"opencode": 3, "cline": 1}
    assert SECRET not in out and NAME not in out and "off-by-one" not in out, "a dry run prints no content"
    assert _digest(world["db"]) == before, "the source database must not change"
    assert not list((world["tmp"] / "work").glob("snapshot-*")), "the snapshot is removed"


def test_a_trace_with_no_goal_is_counted_and_not_written(world, capsys):
    messages = [
        *samples.opencode_messages(),
        ("user", 9, 1_790_000_250_000, json.dumps({"text": "", "files": []})),
    ]
    samples.make_opencode_db(world["db"], messages=messages)
    out_file = world["tmp"] / "out.jsonl"

    assert collect.main(["collect", *_base(world), "--out", str(out_file)]) == 0

    summary = json.loads(capsys.readouterr().out)
    assert summary["skipped_no_goal"] == 1
    assert summary["tasks"] == 4, "the empty-goal task is not counted as a task to send"
    rows = [json.loads(line) for line in out_file.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 4 and all(row["goal"].strip() for row in rows)


def test_written_traces_are_scrubbed(world):
    out_file = world["tmp"] / "out.jsonl"

    assert collect.main(["collect", *_base(world), "--out", str(out_file)]) == 0

    text = out_file.read_text(encoding="utf-8")
    assert SECRET not in text and NAME not in text
    rows = [json.loads(line) for line in text.splitlines()]
    assert {row["scrub"]["status"] for row in rows} <= {"clean", "masked", "dropped"}
    assert all(row["scrub"]["names_loaded"] for row in rows)


def test_a_missing_names_file_stops_before_reading(world, capsys):
    args = _base(world)
    args[args.index("--names-file") + 1] = str(world["tmp"] / "missing.txt")

    assert collect.main(["collect", *args, "--dry-run"]) == 2
    assert "names file" in capsys.readouterr().err


def test_selecting_one_session_and_a_source(world, capsys):
    collect.main(["collect", *_base(world), "--source", "cline", "--dry-run"])
    assert json.loads(capsys.readouterr().out)["by_source"] == {"cline": 1}

    collect.main(["collect", *_base(world), "--session", "ses_synthetic01", "--source", "opencode", "--dry-run"])
    assert json.loads(capsys.readouterr().out)["tasks"] == 3


def test_an_excluded_session_is_never_read(world, capsys):
    collect.main(["collect", *_base(world), "--exclude-session", "ses_synthetic01", "--dry-run"])

    assert json.loads(capsys.readouterr().out)["by_source"] == {"cline": 1}


@pytest.fixture
def gateway(monkeypatch):
    """The gateway's names and heartbeat routes, faked: names from the fixture."""
    seen: dict[str, list] = {"heartbeats": [], "name_calls": []}
    state = {"names_up": True}

    def fake_get(url, key, timeout=30.0):
        seen["name_calls"].append(url)
        if not state["names_up"]:
            raise collect.urllib.error.URLError("connection refused")
        return {"names": FIX["names"]}

    def fake_post(url, key, body, timeout=30.0):
        assert url.endswith(collect.HEARTBEAT_PATH)
        seen["heartbeats"].append(body)
        return {}

    monkeypatch.setattr(collect, "get_json", fake_get)
    monkeypatch.setattr(collect, "post_json", fake_post)
    seen["state"] = state
    return seen


def test_sync_sends_only_new_tasks_and_remembers(world, gateway, monkeypatch, capsys):
    posted: list[list[dict]] = []

    def fake_post(url, key, batch, timeout=60.0):
        assert key == "synthetic-key-value"
        posted.append(batch)
        return {"accepted": [t["id"] for t in batch], "updated": [], "refused": []}

    monkeypatch.setattr(collect, "post_batch", fake_post)
    args = ["sync", *_base(world), "--source", "opencode", "--session", "ses_synthetic01",
            "--gateway", "https://gateway.test", "--key-file", str(world["key"]),
            "--state-dir", str(world["tmp"] / "state")]

    assert collect.main(args) == 0
    assert [t["task_index"] for t in posted[0]] == [0, 1, 2]
    assert SECRET not in json.dumps(posted) and NAME not in json.dumps(posted)
    out = capsys.readouterr().out
    assert "synthetic-key-value" not in out

    assert collect.main(args) == 0
    assert len(posted) == 1, "nothing new: nothing sent"

    # A new task arrives: it is sent with the previous last one (whose outcome may change).
    messages = [
        *samples.opencode_messages(),
        ("user", 9, 1_790_000_300_000, json.dumps({"text": "Thanks, now document it", "files": []})),
    ]
    samples.make_opencode_db(world["db"], messages=messages)
    assert collect.main(args) == 0
    assert [t["task_index"] for t in posted[1]] == [2, 3]


def test_sync_never_sends_a_trace_with_no_goal_and_moves_past_it(world, gateway, monkeypatch, capsys):
    messages = [
        *samples.opencode_messages(),
        ("user", 9, 1_790_000_250_000, json.dumps({"text": "", "files": []})),
    ]
    samples.make_opencode_db(world["db"], messages=messages)
    posted: list[list[dict]] = []

    def fake_post(url, key, batch, timeout=60.0):
        posted.append(batch)
        return {"accepted": [t["id"] for t in batch], "updated": [], "refused": []}

    monkeypatch.setattr(collect, "post_batch", fake_post)
    args = ["sync", *_base(world), "--source", "opencode", "--session", "ses_synthetic01",
            "--gateway", "https://gateway.test", "--key-file", str(world["key"]),
            "--state-dir", str(world["tmp"] / "state")]

    assert collect.main(args) == 0
    assert [t["task_index"] for t in posted[0]] == [0, 1, 2]
    assert json.loads(capsys.readouterr().out)["skipped_no_goal"] == 1

    # A real task after the empty one is sent; the empty one is not resent.
    samples.make_opencode_db(world["db"], messages=[
        *messages,
        ("user", 10, 1_790_000_300_000, json.dumps({"text": "Add a health check", "files": []})),
    ])
    assert collect.main(args) == 0
    assert [t["task_index"] for t in posted[1]] == [4]
    assert json.loads(capsys.readouterr().out)["skipped_no_goal"] == 1


def test_a_failed_upload_is_queued_scrubbed_and_flushed_later(world, gateway, monkeypatch, capsys):
    def down(url, key, batch, timeout=60.0):
        raise collect.CollectError("the gateway did not answer: connection refused")

    monkeypatch.setattr(collect, "post_batch", down)
    state = world["tmp"] / "state"
    args = ["sync", *_base(world), "--source", "opencode", "--session", "ses_synthetic01",
            "--gateway", "https://gateway.test", "--key-file", str(world["key"]), "--state-dir", str(state)]

    assert collect.main(args) == collect.EXIT_QUEUED
    queued = list((state / "queue").glob("*.json"))
    assert len(queued) == 1
    assert SECRET not in queued[0].read_text() and NAME not in queued[0].read_text()
    assert "connection refused" in capsys.readouterr().out

    sent: list[int] = []
    monkeypatch.setattr(collect, "post_batch", lambda url, key, batch, timeout=60.0: sent.append(len(batch)) or {})
    flush = ["flush", "--gateway", "https://gateway.test", "--key-file", str(world["key"]), "--state-dir", str(state)]
    assert collect.main(flush) == 0
    assert sent == [3]
    assert not list((state / "queue").glob("*.json"))


def test_sync_without_a_key_fails_before_reading(world, capsys):
    args = ["sync", *_base(world), "--session", "ses_synthetic01", "--gateway", "https://gateway.test",
            "--state-dir", str(world["tmp"] / "state")]

    assert collect.main(args) == 2
    assert "key" in capsys.readouterr().err


def _sync_args(world, *extra: str) -> list[str]:
    base = _base(world)
    names_at = base.index("--names-file")
    del base[names_at : names_at + 2]  # names come from the gateway, not a file
    return ["sync", *base, "--names-file", str(world["tmp"] / "no-extra-names.txt"),
            "--gateway", "https://gateway.test", "--key-file", str(world["key"]),
            "--state-dir", str(world["tmp"] / "state"), *extra]


def _no_extra_names(world):
    (world["tmp"] / "no-extra-names.txt").write_text("# none\nPlaceholderperson\n", encoding="utf-8")


def test_names_come_from_the_gateway_and_are_cached_privately(world, gateway, monkeypatch):
    _no_extra_names(world)
    posted: list[dict] = []
    monkeypatch.setattr(collect, "post_batch", lambda url, key, batch, timeout=60.0: posted.extend(batch) or {})

    assert collect.main(_sync_args(world, "--source", "opencode")) == 0

    assert NAME not in json.dumps(posted)
    cache = world["tmp"] / "state" / "names.json"
    assert cache.stat().st_mode & 0o777 == 0o600
    assert gateway["heartbeats"][-1]["ok"] is True and gateway["heartbeats"][-1]["tasks"] == 3


def test_without_names_nothing_is_scrubbed_or_sent_and_the_next_run_retries(world, gateway, monkeypatch):
    _no_extra_names(world)
    gateway["state"]["names_up"] = False
    posted: list[dict] = []
    monkeypatch.setattr(collect, "post_batch", lambda url, key, batch, timeout=60.0: posted.extend(batch) or {})

    assert collect.main(_sync_args(world, "--source", "opencode")) == collect.EXIT_QUEUED

    assert posted == []
    beat = gateway["heartbeats"][-1]
    assert beat["ok"] is False and "household names could not be fetched" in beat["error"]
    assert not (world["tmp"] / "state" / "state.json").exists(), "the state must not advance"

    gateway["state"]["names_up"] = True
    assert collect.main(_sync_args(world, "--source", "opencode")) == 0
    assert len(posted) == 3


def test_a_fresh_names_cache_covers_a_gateway_blip_but_an_old_one_does_not(world, gateway):
    state = world["tmp"] / "state"
    assert collect.fetch_names(collect.build_parser().parse_args(_sync_args(world)), state) == FIX["names"]
    gateway["state"]["names_up"] = False
    args = collect.build_parser().parse_args(_sync_args(world))
    assert collect.fetch_names(args, state) == FIX["names"]
    with pytest.raises(collect.NamesUnavailable):
        collect.fetch_names(args, state, now=time.time() + collect.NAMES_CACHE_MAX_AGE_S + 60)


def test_a_full_sync_covers_every_source_once(world, gateway, monkeypatch):
    _no_extra_names(world)
    posted: list[dict] = []
    monkeypatch.setattr(collect, "post_batch", lambda url, key, batch, timeout=60.0: posted.extend(batch) or {})

    assert collect.main(_sync_args(world)) == 0
    assert {t["source"] for t in posted} == {"opencode", "cline"}
    count = len(posted)

    assert collect.main(_sync_args(world)) == 0
    assert len(posted) == count, "nothing changed: nothing sent again"
    assert len(gateway["heartbeats"]) == 2


def test_flush_reports_its_queue_in_a_heartbeat(world, gateway):
    flush = ["flush", "--gateway", "https://gateway.test", "--key-file", str(world["key"]),
             "--state-dir", str(world["tmp"] / "state")]

    assert collect.main(flush) == 0
    assert gateway["heartbeats"][-1] == {"command": "flush", "ok": True, "queue_length": 0, "sent": 0, "error": None}
