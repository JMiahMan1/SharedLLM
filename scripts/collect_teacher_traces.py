#!/usr/bin/env python3
"""Collect teacher traces from opencode and Cline, scrub them, and (optionally) post them.

Runs on the machine that holds the transcripts. It reads them read-only (the
opencode database is cloned first, never opened in place), turns each user turn
into a normalised trace (``services/gateway/trainer/teacher_traces.py``), and
scrubs every trace (``teacher_scrub.py``) before anything is written to disk or
sent. A raw transcript never leaves this process.

Subcommands::

    # What would be collected: counts only, never content.
    python scripts/collect_teacher_traces.py collect --since 2026-11-01 --dry-run

    # Scrubbed traces to a local JSONL file (under .tmp/, gitignored).
    python scripts/collect_teacher_traces.py collect --session ses_abc --out .tmp/teacher/t.jsonl

    # ...and post them to the Jarvis Trainer.
    python scripts/collect_teacher_traces.py collect --since 2026-11-20 --upload \
        --gateway https://jarvis.example --key-file ~/.config/sharedllm/trainer-key

    # Incremental: only the tasks of one session that were not sent yet (the
    # opencode plugin calls this when a session goes idle). Failed uploads are
    # queued under --state-dir and retried by the next sync or by `flush`.
    python scripts/collect_teacher_traces.py sync --session ses_abc --gateway ... --key-file ...
    python scripts/collect_teacher_traces.py flush --gateway ... --key-file ...

Household names come from a local file that is never committed
(``--names-file``, default ``~/.config/sharedllm/teacher-names.txt``); the run
stops if it is missing unless ``--no-names`` says to go without. Values in the
local ``.env`` files (``--env-file``, default: this checkout's ``.env``) are
masked wherever they appear.

The key is read from ``--key-file`` (or the file named by
``TEACHER_TRACE_KEY_FILE``) or from the variable named by ``--key-env``. It is
sent as a bearer token and never printed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Iterator
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.gateway.trainer import teacher_scrub as scrub  # noqa: E402
from services.gateway.trainer import teacher_traces as traces  # noqa: E402

DEFAULT_OPENCODE_DB = pathlib.Path("~/.local/share/opencode/opencode.db")
DEFAULT_CLINE_DIR = pathlib.Path("~/.cline/data/sessions")
DEFAULT_NAMES_FILE = pathlib.Path(os.getenv("TEACHER_SCRUB_NAMES_FILE") or "~/.config/sharedllm/teacher-names.txt")
DEFAULT_EXCLUDE_FILE = pathlib.Path("~/.config/sharedllm/teacher-exclude.txt")
DEFAULT_STATE_DIR = pathlib.Path(os.getenv("TEACHER_TRACE_STATE_DIR") or "~/.local/state/sharedllm-teacher")
UPLOAD_PATH = "/api/trainer/teacher/traces"
NAMES_PATH = "/api/trainer/teacher/scrub-names"
HEARTBEAT_PATH = "/api/trainer/teacher/heartbeat"
#: A cached names list older than this is not trusted for an upload.
NAMES_CACHE_MAX_AGE_S = 24 * 3600
UPLOAD_BATCH = 50

EXIT_QUEUED = 3


class CollectError(RuntimeError):
    """A clear, user-facing reason the run cannot continue."""


# ---------------------------------------------------------------------------
# Reading the transcripts (read-only)
# ---------------------------------------------------------------------------


def snapshot_sqlite(db_path: pathlib.Path, work_dir: pathlib.Path) -> pathlib.Path:
    """Copy the database (and its WAL) into ``work_dir`` and return the copy.

    On macOS the copy is an APFS clone (``cp -c``), so a multi-gigabyte
    database costs no space or time. Elsewhere it is a plain copy, refused when
    the disk cannot hold it. The original is never opened.
    """
    if not db_path.is_file():
        raise CollectError(f"No opencode database at {db_path}")
    work_dir.mkdir(parents=True, exist_ok=True)
    target = work_dir / db_path.name
    for suffix in ("", "-wal"):
        src = pathlib.Path(str(db_path) + suffix)
        if not src.exists():
            continue
        dst = pathlib.Path(str(target) + suffix)
        if sys.platform == "darwin":
            result = subprocess.run(["cp", "-c", str(src), str(dst)], capture_output=True, text=True, check=False)
            if result.returncode == 0:
                continue
        free = shutil.disk_usage(work_dir).free
        if src.stat().st_size * 1.1 > free:
            raise CollectError(
                f"Copying {src.name} needs {src.stat().st_size // 2**20} MiB and {work_dir} has "
                f"{free // 2**20} MiB free; pass --work-dir on a larger disk"
            )
        shutil.copy2(src, dst)
    return target


def _epoch_ms(day: str | None, *, end: bool = False) -> int | None:
    if not day:
        return None
    try:
        parsed = dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.UTC)
    except ValueError as exc:
        raise CollectError(f"Dates are YYYY-MM-DD, not {day!r}") from exc
    if end:
        parsed += dt.timedelta(days=1)
    return int(parsed.timestamp() * 1000)


def opencode_sessions(
    conn: sqlite3.Connection,
    *,
    session_ids: list[str],
    since: str | None,
    until: str | None,
    include_children: bool,
    updated_after_ms: int | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if session_ids:
        clauses.append(f"id IN ({','.join('?' * len(session_ids))})")
        params += session_ids
    elif not include_children:
        clauses.append("parent_id IS NULL")
    start, stop = _epoch_ms(since), _epoch_ms(until, end=True)
    if start is not None:
        clauses.append("time_updated >= ?")
        params.append(start)
    if stop is not None:
        clauses.append("time_created < ?")
        params.append(stop)
    if updated_after_ms is not None:
        clauses.append("time_updated >= ?")
        params.append(updated_after_ms)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT id, directory, model, time_created FROM session_v2 {where} ORDER BY time_created", params
    ).fetchall()
    return [{"id": r[0], "directory": r[1], "model": r[2], "time_created": r[3]} for r in rows]


def opencode_traces(conn: sqlite3.Connection, session: dict[str, Any], local_providers: list[str]) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT type, seq, time_created, data FROM session_message WHERE session_id = ? ORDER BY seq",
        (session["id"],),
    )
    return traces.opencode_tasks(session, rows, local_providers=local_providers)


def cline_sessions(
    cline_dir: pathlib.Path,
    *,
    session_ids: list[str],
    since: str | None,
    until: str | None,
    updated_after_ms: int | None = None,
) -> Iterator[pathlib.Path]:
    if not cline_dir.is_dir():
        return
    start, stop = _epoch_ms(since), _epoch_ms(until, end=True)
    for folder in sorted(p for p in cline_dir.iterdir() if p.is_dir()):
        if session_ids and folder.name not in session_ids:
            continue
        meta_path = folder / f"{folder.name}.json"
        messages_path = folder / f"{folder.name}.messages.json"
        if not meta_path.is_file() or not messages_path.is_file():
            continue
        if updated_after_ms is not None and messages_path.stat().st_mtime * 1000 < updated_after_ms:
            continue
        if start is not None or stop is not None:
            started = _cline_started_ms(meta_path)
            if started is None or (start is not None and started < start) or (stop is not None and started >= stop):
                continue
        yield folder


def _cline_started_ms(meta_path: pathlib.Path) -> int | None:
    try:
        value = json.loads(meta_path.read_text(encoding="utf-8")).get("started_at")
        return int(dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp() * 1000)
    except (OSError, ValueError, TypeError):
        return None


def cline_traces(folder: pathlib.Path, local_providers: list[str]) -> list[dict[str, Any]]:
    meta = json.loads((folder / f"{folder.name}.json").read_text(encoding="utf-8"))
    data = json.loads((folder / f"{folder.name}.messages.json").read_text(encoding="utf-8"))
    messages = data.get("messages") if isinstance(data, dict) else data
    meta.setdefault("session_id", folder.name)
    return traces.cline_tasks(meta, list(messages or []), local_providers=local_providers)


# ---------------------------------------------------------------------------
# Scrubbing config
# ---------------------------------------------------------------------------


def get_json(url: str, key: str, timeout: float = 30.0) -> dict[str, Any]:
    """GET with the bearer key (the one seam tests replace for reads)."""
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8") or "{}")
    return data if isinstance(data, dict) else {}


def post_json(url: str, key: str, body: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
    """POST with the bearer key (the one seam tests replace for small writes)."""
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8") or "{}")
    return data if isinstance(data, dict) else {}


class NamesUnavailable(CollectError):
    """The household names could not be fetched (and no fresh cache exists)."""


def names_cache(state_dir: pathlib.Path) -> pathlib.Path:
    return state_dir / "names.json"


def fetch_names(args: argparse.Namespace, state_dir: pathlib.Path, *, now: float | None = None) -> list[str]:
    """The household's names from the gateway (Identity's family directory).

    Cached in the state directory (mode 600). When the gateway cannot be asked,
    a cache younger than ``NAMES_CACHE_MAX_AGE_S`` is used; otherwise the run
    refuses to scrub for upload at all (fail closed).
    """
    now = time.time() if now is None else now
    url, key = gateway_url(args), read_key(args)
    try:
        names = [str(n) for n in get_json(url + NAMES_PATH, key).get("names") or [] if str(n).strip()]
        if not names:
            raise CollectError("the gateway returned no household names")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, CollectError) as exc:
        cache = names_cache(state_dir)
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cached = {}
        if cached.get("names") and now - float(cached.get("fetched_at") or 0) <= NAMES_CACHE_MAX_AGE_S:
            return list(cached["names"])
        raise NamesUnavailable(
            f"household names could not be fetched ({_reason(exc)}) and there is no fresh cache; "
            "nothing was scrubbed or sent, the next run retries"
        ) from exc
    state_dir.mkdir(parents=True, exist_ok=True)
    cache = names_cache(state_dir)
    tmp = cache.with_suffix(".tmp")
    tmp.write_text(json.dumps({"names": names, "fetched_at": now}), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, cache)
    return names


def _reason(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"the gateway answered {exc.code}"
    return f"{type(exc).__name__}: {exc}"[:200]


def scrub_config(args: argparse.Namespace, *, remote_names: list[str] | None = None) -> scrub.ScrubConfig:
    """Names from the gateway (when given) plus the optional local names file."""
    names: list[str] = list(remote_names or [])
    names_file = pathlib.Path(args.names_file or DEFAULT_NAMES_FILE).expanduser()
    if not args.no_names:
        if args.names_file or names_file.is_file():
            try:
                names += scrub.load_names(names_file)
            except (FileNotFoundError, ValueError) as exc:
                raise CollectError(str(exc)) from exc
        if not names:
            raise CollectError(
                "No household names: pass --gateway and a key so they are fetched from Identity, "
                f"add {names_file}, or pass --no-names (counts-only dry runs)."
            )
    env_files = args.env_file or [p for p in (ROOT / ".env",) if p.is_file()]
    return scrub.ScrubConfig(
        names=tuple(names),
        literals=tuple(scrub.load_env_literals(env_files)),
        mask_ips=not args.keep_ips,
        names_loaded=bool(names),
    )


def excluded_sessions(args: argparse.Namespace) -> set[str]:
    excluded = set(args.exclude_session or [])
    path = pathlib.Path(args.exclude_file).expanduser()
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                excluded.add(line)
    return excluded


# ---------------------------------------------------------------------------
# Uploading
# ---------------------------------------------------------------------------


def read_key(args: argparse.Namespace) -> str:
    """The bearer key, from a file or a named variable. Never printed."""
    key_file = args.key_file or os.getenv("TEACHER_TRACE_KEY_FILE")
    if key_file:
        path = pathlib.Path(key_file).expanduser()
        try:
            key = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise CollectError(f"Could not read the key file {path}: {exc.strerror}") from exc
        if not key:
            raise CollectError(f"The key file {path} is empty")
        return key
    if args.key_env:
        key = os.getenv(args.key_env, "").strip()
        if not key:
            raise CollectError(f"The variable {args.key_env} is not set")
        return key
    raise CollectError("Uploading needs --key-file, TEACHER_TRACE_KEY_FILE, or --key-env")


def gateway_url(args: argparse.Namespace) -> str:
    url = (args.gateway or os.getenv("GATEWAY_URL") or "").rstrip("/")
    if not url:
        raise CollectError("Uploading needs --gateway or GATEWAY_URL")
    return url


def post_batch(url: str, key: str, batch: list[dict[str, Any]], timeout: float = 60.0) -> dict[str, Any]:
    request = urllib.request.Request(
        url + UPLOAD_PATH,
        data=json.dumps({"traces": batch}).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise CollectError(f"the gateway answered {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CollectError(f"the gateway did not answer: {exc}") from exc


def queue_dir(state_dir: pathlib.Path) -> pathlib.Path:
    return state_dir / "queue"


def enqueue(state_dir: pathlib.Path, batch: list[dict[str, Any]]) -> pathlib.Path:
    """Keep a scrubbed batch for a later retry (``flush``)."""
    directory = queue_dir(state_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{time.strftime('%Y%m%dT%H%M%S')}-{os.getpid()}-{len(batch)}.json"
    path.write_text(json.dumps(batch, ensure_ascii=False), encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def upload(traces_out: list[dict[str, Any]], args: argparse.Namespace, *, state_dir: pathlib.Path | None) -> dict[str, Any]:
    """Post in batches. With a state dir, a batch that fails is queued, not lost."""
    url, key = gateway_url(args), read_key(args)
    totals: Counter[str] = Counter()
    errors: list[str] = []
    for start in range(0, len(traces_out), UPLOAD_BATCH):
        batch = traces_out[start : start + UPLOAD_BATCH]
        try:
            answer = post_batch(url, key, batch)
        except CollectError as exc:
            if state_dir is None:
                raise
            enqueue(state_dir, batch)
            totals["queued"] += len(batch)
            errors.append(str(exc))
            continue
        totals["accepted"] += len(answer.get("accepted") or [])
        totals["updated"] += len(answer.get("updated") or [])
        totals["refused"] += len(answer.get("refused") or [])
        errors += [f"{item.get('id')}: {item.get('reason')}" for item in answer.get("refused") or []]
    return {**totals, "errors": errors}


def flush_queue(args: argparse.Namespace, state_dir: pathlib.Path) -> dict[str, Any]:
    directory = queue_dir(state_dir)
    totals: Counter[str] = Counter()
    errors: list[str] = []
    if not directory.is_dir():
        return {"sent": 0, "left": 0, "errors": []}
    files = sorted(directory.glob("*.json"))
    if not files:
        return {"sent": 0, "left": 0, "errors": []}
    url, key = gateway_url(args), read_key(args)
    for path in files:
        batch = json.loads(path.read_text(encoding="utf-8"))
        try:
            answer = post_batch(url, key, batch)
        except CollectError as exc:
            errors.append(str(exc))
            break
        path.unlink()
        totals["sent"] += len(batch)
        totals["refused"] += len(answer.get("refused") or [])
    totals["left"] = len(list(directory.glob("*.json")))
    return {**totals, "errors": errors}


# ---------------------------------------------------------------------------
# Collect
# ---------------------------------------------------------------------------


def collect_raw(
    args: argparse.Namespace, work_dir: pathlib.Path, *, updated_after_ms: int | None = None
) -> list[dict[str, Any]]:
    """Every selected task as a raw (unscrubbed) trace. Never written or sent."""
    found: list[dict[str, Any]] = []
    local = [p.strip() for p in args.local_providers.split(",") if p.strip()]
    excluded = excluded_sessions(args)
    if args.source in ("opencode", "all"):
        db = pathlib.Path(args.opencode_db).expanduser()
        if db.is_file():
            copy = snapshot_sqlite(db, work_dir)
            conn = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
            try:
                for session in opencode_sessions(
                    conn, session_ids=args.session or [], since=args.since, until=args.until,
                    include_children=args.include_subagents, updated_after_ms=updated_after_ms,
                ):
                    if session["id"] in excluded:
                        continue
                    found += opencode_traces(conn, session, local)
            finally:
                conn.close()
        elif args.source == "opencode":
            raise CollectError(f"No opencode database at {db}")
    if args.source in ("cline", "all"):
        cline_dir = pathlib.Path(args.cline_dir).expanduser()
        if not cline_dir.is_dir() and args.source == "cline":
            raise CollectError(f"No Cline sessions at {cline_dir}")
        for folder in cline_sessions(
            cline_dir, session_ids=args.session or [], since=args.since, until=args.until,
            updated_after_ms=updated_after_ms,
        ):
            if folder.name in excluded:
                continue
            found += cline_traces(folder, local)
    if args.limit:
        found = found[: args.limit]
    return found


def counts(scrubbed: list[dict[str, Any]]) -> dict[str, Any]:
    """What a dry run prints: numbers and categories, never content."""
    masked: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    drops: Counter[str] = Counter()
    for item in scrubbed:
        info = item.get("scrub") or {}
        masked.update(info.get("masked") or {})
        reasons.update(info.get("dropped_reasons") or {})
        if info.get("status") == "dropped":
            drops[str(info.get("drop_reason")).split(":")[0]] += 1
    return {
        "sessions": len({(t.get("source"), t.get("session_id")) for t in scrubbed}),
        "tasks": len(scrubbed),
        "by_source": dict(Counter(str(t.get("source")) for t in scrubbed)),
        "by_location": dict(Counter(str(t.get("model_location")) for t in scrubbed)),
        "by_outcome": dict(Counter(str(t.get("outcome")) for t in scrubbed)),
        "by_scrub_status": dict(Counter(str((t.get("scrub") or {}).get("status")) for t in scrubbed)),
        "dropped_traces_by_reason": dict(drops),
        "dropped_steps_by_reason": dict(reasons),
        "masked_by_kind": dict(masked),
        "steps_kept": sum(int(t.get("step_count") or 0) for t in scrubbed if (t.get("scrub") or {}).get("status") != "dropped"),
    }


def scrub_all(raw: list[dict[str, Any]], config: scrub.ScrubConfig, *, per_task: bool = False) -> list[dict[str, Any]]:
    """Scrub every task, then drop whole cloud sessions that touched secrets.

    ``per_task`` keeps the other tasks of such a session (each still scrubbed,
    its own secret steps still dropped); it is an explicit choice, never a default.
    """
    scrubbed = [scrub.scrub_trace(trace, config) for trace in raw]
    return scrubbed if per_task else scrub.apply_session_policy(scrubbed)


def write_jsonl(path: pathlib.Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.chmod(path, 0o600)


def cmd_collect(args: argparse.Namespace) -> int:
    remote = None
    if args.upload:
        # An upload is scrubbed with the household's names from Identity, always.
        remote = fetch_names(args, pathlib.Path(args.state_dir).expanduser())
    config = scrub_config(args, remote_names=remote)
    with tempfile.TemporaryDirectory(dir=_work_root(args), prefix="snapshot-") as work:
        raw = collect_raw(args, pathlib.Path(work))
    scrubbed = scrub_all(raw, config, per_task=args.per_task_policy)
    del raw
    summary = counts(scrubbed)
    if args.dry_run:
        print(json.dumps({"dry_run": True, **summary}, indent=2))
        return 0
    if args.out:
        write_jsonl(pathlib.Path(args.out), scrubbed)
        summary["written_to"] = str(args.out)
    if args.upload:
        summary["upload"] = upload(scrubbed, args, state_dir=None)
    if not args.out and not args.upload:
        raise CollectError("Nothing to do: pass --dry-run, --out PATH, or --upload")
    print(json.dumps(summary, indent=2))
    return 0


def _work_root(args: argparse.Namespace) -> pathlib.Path:
    root = pathlib.Path(args.work_dir).expanduser() if args.work_dir else ROOT / ".tmp" / "teacher"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _load_state(state_dir: pathlib.Path) -> dict[str, Any]:
    path = state_dir / "state.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"sessions": {}}
    except ValueError as exc:
        raise CollectError(f"The sync state {path} is corrupt ({exc}); move it aside to resend everything") from exc


def _save_state(state_dir: pathlib.Path, state: dict[str, Any]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    tmp = state_dir / "state.json.tmp"
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, state_dir / "state.json")


def post_heartbeat(args: argparse.Namespace, report: dict[str, Any]) -> str | None:
    """Tell the gateway how this run went (counts and an error line only)."""
    try:
        url, key = gateway_url(args), read_key(args)
    except CollectError as exc:
        return str(exc)
    try:
        post_json(url + HEARTBEAT_PATH, key, report)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return _reason(exc)
    return None


#: Sessions touched this long before the last full sync are looked at again.
FULL_SYNC_OVERLAP_MS = 3600 * 1000


def cmd_sync(args: argparse.Namespace) -> int:
    """Send new tasks (one session, or every session touched since the last run).

    Fails closed: without the household's names nothing is scrubbed or sent,
    the state is not advanced, and the next run tries again. A failed upload
    is queued (scrubbed) and flushed by the next run. Either way the gateway
    gets a heartbeat saying what happened.
    """
    state_dir = pathlib.Path(args.state_dir).expanduser()
    gateway_url(args)
    read_key(args)  # fail before reading anything when the key is missing
    flushed = flush_queue(args, state_dir)
    state = _load_state(state_dir)
    started_ms = int(time.time() * 1000)
    heartbeat: dict[str, Any] = {"command": "sync", "ok": True, "pending_sessions": 0}
    try:
        names = fetch_names(args, state_dir)
    except NamesUnavailable as exc:
        heartbeat.update(ok=False, error=str(exc), queue_length=_queue_length(state_dir))
        _report(args, heartbeat, {"flushed": flushed, "error": str(exc)})
        return EXIT_QUEUED
    config = scrub_config(args, remote_names=names)
    full = not args.session
    after = None
    if full and state.get("last_full_sync_ms"):
        after = int(state["last_full_sync_ms"]) - FULL_SYNC_OVERLAP_MS
    with tempfile.TemporaryDirectory(dir=_work_root(args), prefix="snapshot-") as work:
        raw = collect_raw(args, pathlib.Path(work), updated_after_ms=after)
    # Whole sessions are scrubbed, so the session policy sees every task.
    everything = scrub_all(raw, config, per_task=args.per_task_policy)
    del raw
    scrubbed: list[dict[str, Any]] = []
    marks: dict[str, int] = {}
    by_session: dict[str, list[dict[str, Any]]] = {}
    for trace in everything:
        by_session.setdefault(f"{trace['source']}:{trace['session_id']}", []).append(trace)
    for key, items in by_session.items():
        last = int((state["sessions"].get(key) or {}).get("last_task", -1))
        newest = max(int(t["task_index"]) for t in items)
        if newest <= last:
            continue
        marks[key] = newest
        if any((t.get("scrub") or {}).get("drop_reason") == "session_touched_secrets" for t in items):
            # Tasks sent before this one touched secrets are replaced by stubs too.
            scrubbed += items
            continue
        # The previous last task is sent again: a correction typed since then
        # changes its outcome, and the gateway upserts by id.
        scrubbed += [t for t in items if int(t["task_index"]) >= max(0, last)]
    del everything
    result: dict[str, Any] = {"flushed": flushed, **counts(scrubbed)}
    if scrubbed:
        result["upload"] = upload(scrubbed, args, state_dir=state_dir)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for key, newest in marks.items():
        state["sessions"][key] = {"last_task": newest, "synced_at": now}
    if full:
        state["last_full_sync_ms"] = started_ms
    _save_state(state_dir, state)
    upload_result = result.get("upload") or {}
    queue_length = _queue_length(state_dir)
    heartbeat.update(
        tasks=result["tasks"],
        sent=int(upload_result.get("accepted") or 0) + int(upload_result.get("updated") or 0),
        queue_length=queue_length,
    )
    errors = list(upload_result.get("errors") or []) + list(flushed.get("errors") or [])
    if queue_length or errors:
        heartbeat.update(ok=not queue_length, error="; ".join(errors)[:300] or None)
    _report(args, heartbeat, result)
    return EXIT_QUEUED if queue_length else 0


def _queue_length(state_dir: pathlib.Path) -> int:
    directory = queue_dir(state_dir)
    return len(list(directory.glob("*.json"))) if directory.is_dir() else 0


def _report(args: argparse.Namespace, heartbeat: dict[str, Any], result: dict[str, Any]) -> None:
    failed = post_heartbeat(args, heartbeat)
    if failed:
        result["heartbeat_error"] = failed
    print(json.dumps(result))


def cmd_flush(args: argparse.Namespace) -> int:
    state_dir = pathlib.Path(args.state_dir).expanduser()
    result = flush_queue(args, state_dir)
    left = int(result.get("left") or 0)
    _report(
        args,
        {"command": "flush", "ok": not left, "queue_length": left, "sent": int(result.get("sent") or 0),
         "error": "; ".join(result.get("errors") or [])[:300] or None},
        result,
    )
    return EXIT_QUEUED if left else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--source", choices=("opencode", "cline", "all"), default="all")
        p.add_argument("--session", action="append", help="a session id (repeatable)")
        p.add_argument("--since", help="sessions active on or after YYYY-MM-DD (UTC)")
        p.add_argument("--until", help="sessions started on or before YYYY-MM-DD (UTC)")
        p.add_argument("--limit", type=int, default=0, help="at most this many tasks")
        p.add_argument("--include-subagents", action="store_true", help="opencode child sessions too")
        p.add_argument("--opencode-db", default=str(DEFAULT_OPENCODE_DB))
        p.add_argument("--cline-dir", default=str(DEFAULT_CLINE_DIR))
        p.add_argument("--names-file", help=f"extra household names, one per line (default {DEFAULT_NAMES_FILE} when it exists)")
        p.add_argument("--no-names", action="store_true", help="scrub without a household names file")
        p.add_argument("--env-file", action="append", help="mask every value in this .env file (repeatable)")
        p.add_argument("--keep-ips", action="store_true", help="do not mask IP addresses")
        p.add_argument("--exclude-session", action="append", help="never collect this session (repeatable)")
        p.add_argument("--exclude-file", default=str(DEFAULT_EXCLUDE_FILE))
        p.add_argument("--local-providers", default=",".join(traces.DEFAULT_LOCAL_PROVIDERS))
        p.add_argument("--work-dir", help="where the database snapshot goes (default: .tmp/teacher)")
        p.add_argument(
            "--per-task-policy",
            action="store_true",
            help="keep the other tasks of a cloud session that touched secrets (default: drop the whole session)",
        )

    def remote(p: argparse.ArgumentParser) -> None:
        p.add_argument("--gateway", help="the Jarvis gateway URL (or GATEWAY_URL)")
        p.add_argument("--key-file", help="file holding an admin or uploader API key (or TEACHER_TRACE_KEY_FILE)")
        p.add_argument("--key-env", help="name of a variable holding the key")

    collect = sub.add_parser("collect", help="collect, scrub, and write or post")
    common(collect)
    remote(collect)
    collect.add_argument("--dry-run", action="store_true", help="print counts only")
    collect.add_argument("--out", help="write the scrubbed traces as JSONL")
    collect.add_argument("--upload", action="store_true", help="post the scrubbed traces")
    collect.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR), help="where the names cache is kept")
    collect.set_defaults(func=cmd_collect)

    sync = sub.add_parser("sync", help="post a session's new tasks (incremental, queued on failure)")
    common(sync)
    remote(sync)
    sync.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    sync.set_defaults(func=cmd_sync)

    flush = sub.add_parser("flush", help="retry queued uploads")
    remote(flush)
    flush.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    flush.set_defaults(func=cmd_flush)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except CollectError as exc:
        print(f"collect_teacher_traces: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
