"""The recorder: what goes into history, and the thread that writes it.

HA's recorder, and a little more:

* **Unrecorded attributes.** Each domain names attributes that churn without meaning anything later (a camera's
  rotating ``access_token``, the sun's ``azimuth``, a player's ``media_position``); history leaves them out, as
  HA's ``_unrecorded_attributes`` do. A driver adds its own (``Driver.unrecorded_attributes``). The live state and
  the state restored at startup keep every attribute: only history rows are trimmed.
* **No row for nothing.** A change that touched only unrecorded attributes (the state and every recorded
  attribute as they were last written) writes no history row.
* **Shared attributes.** Equal attribute sets are stored once in ``state_attributes`` (store.py) and history rows
  point at them. Older rows keep their own column and read the same.
* **Exclusions.** Entity ids, globs and domains an admin leaves out (``GET``/``PUT /hub/recorder``), the hub
  setting ``recorder_settings``; no household names live in code. Retention (``keep_days``) is a setting too.
* **Purges.** ``recorder.purge`` (``keep_days``, ``repack``, ``apply_filter``) and ``recorder.purge_entities``,
  admin only over the API, as in HA.
* **Off the event loop.** Rows go to a writer thread on its own SQLite connection through a bounded queue and are
  committed in batches (``COMMIT_INTERVAL_S``); a history read first waits for what is queued, and shutdown
  flushes.

| Route | Who | What |
|---|---|---|
| ``GET /hub/recorder`` | admins | The settings, the per-domain unrecorded attributes, the writer's counters |
| ``PUT /hub/recorder`` | admins | Change ``keep_days`` and ``exclude`` (``domains``, ``entity_globs``, ``entities``) |
| ``GET /hub/recorder/size`` | admins | Database size, row counts, rows per day, the busiest entities of the last 24 h |
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import json
import logging
import os
import queue
import re
import sqlite3
import threading
import time
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from services.homehub.core import HubError, ServiceCall, State
from services.homehub.store import Store, attrs_hash, canonical_attrs, entity_matcher

log = logging.getLogger("homehub.recorder")

DOMAIN = "recorder"
SETTINGS_KEY = "recorder_settings"
DEFAULT_KEEP_DAYS = 30
MAX_KEEP_DAYS = 3650
#: How long rows wait in the writer before they are committed together.
COMMIT_INTERVAL_S = 1.0
#: Rows committed in one transaction at most (a burst commits early).
MAX_BATCH = 1000
#: Queue length before new rows are dropped (and counted): about a quarter hour of a busy hub's changes.
MAX_QUEUE = 50000
#: How long a reader waits for the writer to commit what it holds before reading anyway.
SYNC_TIMEOUT_S = 5.0
DROP_REPORT_EVERY_S = 60.0

#: HA's ``ALL_DOMAIN_EXCLUDE_ATTRS``: never recorded for any entity.
ALL_DOMAINS_UNRECORDED = frozenset({"attribution", "restored", "supported_features"})

#: HA's per-platform ``_entity_component_unrecorded_attributes`` / ``_unrecorded_attributes`` for the domains a
#: hub has. Conservative: only attributes HA itself leaves out, which are lists of choices, limits and churn.
UNRECORDED_ATTRIBUTES: dict[str, frozenset[str]] = {
    "automation": frozenset({"current", "last_triggered", "max", "mode"}),
    "camera": frozenset({"access_token", "entity_picture"}),
    "climate": frozenset({"fan_modes", "hvac_modes", "max_humidity", "max_temp", "min_humidity", "min_temp",
                          "preset_modes", "swing_horizontal_modes", "swing_modes", "target_temp_step"}),
    "event": frozenset({"event_types"}),
    "fan": frozenset({"preset_modes"}),
    "humidifier": frozenset({"available_modes", "max_humidity", "min_humidity"}),
    "image": frozenset({"access_token", "entity_picture"}),
    "input_boolean": frozenset({"editable"}),
    "input_button": frozenset({"editable"}),
    "input_datetime": frozenset({"editable"}),
    "input_number": frozenset({"editable", "initial", "max", "min", "step"}),
    "input_select": frozenset({"editable"}),
    "input_text": frozenset({"editable", "max", "min", "mode", "pattern"}),
    "light": frozenset({"effect_list", "max_color_temp_kelvin", "max_mireds", "min_color_temp_kelvin", "min_mireds",
                        "supported_color_modes"}),
    "media_player": frozenset({"entity_picture", "entity_picture_local", "media_position",
                               "media_position_updated_at", "sound_mode_list", "source_list"}),
    "number": frozenset({"max", "min", "mode", "step"}),
    "remote": frozenset({"activity_list"}),
    "schedule": frozenset({"editable", "next_event"}),
    "script": frozenset({"current", "last_action", "last_triggered", "max", "mode"}),
    "select": frozenset({"options"}),
    "siren": frozenset({"available_tones"}),
    "sun": frozenset({"azimuth", "elevation", "next_dawn", "next_dusk", "next_midnight", "next_noon", "next_rising",
                      "next_setting", "rising"}),
    "text": frozenset({"max", "min", "mode", "pattern"}),
    "update": frozenset({"entity_picture", "in_progress", "release_summary", "update_percentage"}),
    "vacuum": frozenset({"fan_speed_list"}),
    "water_heater": frozenset({"max_temp", "min_temp", "operation_list"}),
}

_DOMAIN_RE = re.compile(r"^[a-z0-9_]+$")
_ENTITY_RE = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
_GLOB_RE = re.compile(r"^[a-z0-9_.*?\[\]!-]+$")


def default_settings() -> dict[str, Any]:
    return {"keep_days": DEFAULT_KEEP_DAYS, "exclude": {"domains": [], "entity_globs": [], "entities": []}}


def validate_settings(raw: Any, current: dict[str, Any] | None = None) -> dict[str, Any]:
    """A full settings document from a (partial) change; raises ValueError naming what is wrong."""
    if not isinstance(raw, dict):
        raise ValueError("The settings must be a JSON object")
    unknown = set(raw) - {"keep_days", "exclude"}
    if unknown:
        raise ValueError(f"Unknown setting: {sorted(unknown)[0]}")
    out = json.loads(json.dumps(current or default_settings()))
    if "keep_days" in raw:
        days = raw["keep_days"]
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= MAX_KEEP_DAYS:
            raise ValueError(f"keep_days must be a whole number of days from 1 to {MAX_KEEP_DAYS}")
        out["keep_days"] = days
    if "exclude" in raw:
        ex = raw["exclude"]
        if not isinstance(ex, dict):
            raise ValueError("exclude must be an object with domains, entity_globs and entities")
        unknown = set(ex) - {"domains", "entity_globs", "entities"}
        if unknown:
            raise ValueError(f"Unknown exclude key: {sorted(unknown)[0]}")
        checks = {"domains": (_DOMAIN_RE, "a domain such as sensor"),
                  "entity_globs": (_GLOB_RE, "a glob such as sensor.*_signal"),
                  "entities": (_ENTITY_RE, "an entity id such as sensor.example")}
        for key, (pattern, what) in checks.items():
            if key not in ex:
                continue
            items = ex[key]
            if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
                raise ValueError(f"exclude.{key} must be a list of strings")
            cleaned = list(dict.fromkeys(i.strip().lower() for i in items if i.strip()))
            bad = [i for i in cleaned if not pattern.match(i)]
            if bad:
                raise ValueError(f"exclude.{key}: {bad[0]!r} is not {what}")
            out["exclude"][key] = cleaned
    return out


class HistoryWriter:
    """One thread, one SQLite connection: history rows and restore states, committed in batches.

    ``submit`` never blocks (the event loop calls it); a full queue drops the row and counts it. ``sync`` waits
    for everything queued before it to be committed. ``run_job`` runs a function on the writer's connection
    between batches (purges), so nothing else writes history while it runs.
    """

    def __init__(self, store: Store, *, commit_interval: float = COMMIT_INTERVAL_S, max_queue: int = MAX_QUEUE,
                 max_batch: int = MAX_BATCH) -> None:
        self.store = store
        self.commit_interval = commit_interval
        self.max_batch = max_batch
        self.q: queue.Queue = queue.Queue(maxsize=max_queue)
        self.written = 0
        self.dropped = 0
        self.failed = 0
        self.batches = 0
        self._last_drop_report = 0.0
        self._thread = threading.Thread(target=self._run, name="homehub-recorder", daemon=True)
        self._started = False

    def start(self) -> None:
        self._started = True
        self._thread.start()

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    def submit(self, kind: str, payload: Any) -> bool:
        try:
            self.q.put_nowait((kind, payload))
            return True
        except queue.Full:
            self.dropped += 1
            now = time.monotonic()
            if now - self._last_drop_report >= DROP_REPORT_EVERY_S:
                self._last_drop_report = now
                log.warning("recorder queue is full (%d waiting): %d rows dropped so far", self.q.qsize(), self.dropped)
            return False

    def sync(self, timeout: float = SYNC_TIMEOUT_S) -> bool:
        """Wait until what was queued before this call is committed. False if the writer did not answer."""
        if threading.current_thread() is self._thread or not self.alive:
            return False
        done = threading.Event()
        try:
            self.q.put(("sync", done), timeout=timeout)
        except queue.Full:
            return False
        return done.wait(timeout)

    def run_job(self, fn: Callable[[sqlite3.Connection], Any]) -> concurrent.futures.Future:
        fut: concurrent.futures.Future = concurrent.futures.Future()
        if not self.alive:
            fut.set_exception(RuntimeError("the recorder is not running"))
            return fut
        self.q.put(("job", (fn, fut)))
        return fut

    def stop(self, timeout: float = 30.0) -> None:
        """Commit what is queued and end the thread."""
        if not self._started or not self.alive:
            return
        try:
            self.q.put(("stop", None), timeout=timeout)
        except queue.Full:
            log.warning("recorder queue stayed full at shutdown; %d rows are lost", self.q.qsize())
            return
        self._thread.join(timeout)
        if self._thread.is_alive():
            log.warning("recorder writer did not finish within %.0f s of shutdown", timeout)

    def _run(self) -> None:
        db = self.store.connect()
        rows: list[tuple] = []
        lasts: dict[str, str] = {}
        due: float | None = None

        def flush() -> None:
            nonlocal rows, lasts, due
            if not rows and not lasts:
                due = None
                return
            batch, latest = rows, lasts
            rows, lasts, due = [], {}, None
            try:
                db.execute("BEGIN")
                if batch:
                    self.store.write_history(db, batch)
                if latest:
                    db.executemany("INSERT OR REPLACE INTO last_states(entity_id, doc) VALUES (?, ?)",
                                   list(latest.items()))
                db.execute("COMMIT")
                self.written += len(batch)
                self.batches += 1
            except Exception:
                self.failed += len(batch)
                log.exception("recorder could not commit %d rows", len(batch))
                with contextlib.suppress(sqlite3.Error):
                    db.execute("ROLLBACK")
                # A rolled-back insert may have handed out attribute ids that do not exist.
                self.store.forget_attribute_ids()

        try:
            while True:
                timeout = None if due is None else max(0.0, due - time.monotonic())
                try:
                    kind, payload = self.q.get(timeout=timeout)
                except queue.Empty:
                    flush()
                    continue
                if kind == "row":
                    rows.append(payload)
                elif kind == "last":
                    lasts[payload[0]] = payload[1]
                elif kind == "sync":
                    flush()
                    payload.set()
                    continue
                elif kind == "job":
                    flush()
                    fn, fut = payload
                    if fut.set_running_or_notify_cancel():
                        try:
                            fut.set_result(fn(db))
                        except BaseException as exc:  # handed to the caller
                            fut.set_exception(exc)
                    continue
                elif kind == "stop":
                    flush()
                    return
                if due is None:
                    due = time.monotonic() + self.commit_interval
                if len(rows) >= self.max_batch:
                    flush()
        except Exception:
            log.exception("recorder writer stopped")
        finally:
            db.close()


class Recorder:
    """Decides what history keeps and hands it to the writer (or writes it at once without a thread)."""

    def __init__(self, hub: Any, store: Store, *, threaded: bool | None = None,
                 commit_interval: float = COMMIT_INTERVAL_S, max_queue: int = MAX_QUEUE) -> None:
        self.hub = hub
        self.store = store
        if threaded is None:
            threaded = store.path != ":memory:"  # a second connection to :memory: is another database
        self.writer = HistoryWriter(store, commit_interval=commit_interval, max_queue=max_queue) if threaded else None
        self.settings = default_settings()
        self._excluded: Callable[[str], bool] = lambda _eid: False
        #: entity -> (state, attribute hash) last written, so a change of unrecorded attributes alone is skipped.
        self._last: dict[str, tuple[str, str]] = {}
        self.recorded = 0
        self.skipped = 0
        self.excluded = 0
        self._unsub: Callable[[], None] | None = None
        self.load_settings()

    # -- settings -------------------------------------------------------------
    def load_settings(self) -> None:
        raw = self.store.get_kv(SETTINGS_KEY)
        try:
            self.apply_settings(validate_settings(raw) if raw else default_settings())
        except ValueError as exc:
            log.warning("recorder settings are not usable (%s); using the defaults", exc)
            self.apply_settings(default_settings())

    def apply_settings(self, settings: dict[str, Any]) -> None:
        self.settings = settings
        ex = settings["exclude"]
        self._excluded = entity_matcher(ex["entities"], ex["domains"], ex["entity_globs"])

    def save_settings(self, settings: dict[str, Any]) -> None:
        self.store.set_kv(SETTINGS_KEY, settings)
        self.apply_settings(settings)

    @property
    def keep_days(self) -> int:
        return int(self.settings["keep_days"])

    def is_excluded(self, entity_id: str) -> bool:
        return self._excluded(entity_id)

    # -- what a row holds -------------------------------------------------------
    def unrecorded_for(self, entity_id: str) -> frozenset[str]:
        domain = entity_id.split(".", 1)[0]
        out = ALL_DOMAINS_UNRECORDED | UNRECORDED_ATTRIBUTES.get(domain, frozenset())
        ent = self.hub.entities.get(entity_id)
        driver = self.hub.drivers.get(ent.config_entry_id) if ent is not None and ent.config_entry_id else None
        extra = getattr(driver, "unrecorded_attributes", None) if driver is not None else None
        return out | frozenset(extra) if extra else out

    def recorded_attributes(self, state: State) -> dict[str, Any]:
        drop = self.unrecorded_for(state.entity_id)
        return {k: v for k, v in state.attributes.items() if k not in drop}

    # -- recording --------------------------------------------------------------
    def start(self) -> None:
        if self.writer is not None:
            self.writer.start()
            self.store.writer = self.writer
        self._unsub = self.hub.bus.subscribe(self._on_change, "state_changed")

    def detach(self) -> None:
        """Stop listening for changes (on the event loop)."""
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    def stop(self) -> None:
        """Stop listening and commit what the writer holds (blocks: call it off the loop at shutdown)."""
        self.detach()
        if self.writer is not None:
            self.writer.stop()
            if self.store.writer is self.writer:
                self.store.writer = None

    def _on_change(self, _t: str, ev: dict[str, Any]) -> None:
        new, old = ev.get("new_state"), ev.get("old_state")
        if new is None:
            self._last.pop(ev.get("entity_id") or "", None)
            return
        try:
            self.handle(new, old)
        except Exception:
            log.exception("recording %s failed", new.entity_id)

    def handle(self, new: State, old: State | None) -> None:
        eid = new.entity_id
        # The restore state keeps every attribute, excluded entity or not.
        if self.writer is not None:
            self.writer.submit("last", (eid, json.dumps(new.as_dict(), default=str)))
        else:
            self.store.save_last_state(new)
        if self._excluded(eid):
            self.excluded += 1
            return
        canonical = canonical_attrs(self.recorded_attributes(new))
        digest = attrs_hash(canonical)
        prev = self._last.get(eid)
        if prev is None and old is not None and old.state == new.state:
            prev = (old.state, attrs_hash(canonical_attrs(self.recorded_attributes(old))))
        if prev == (new.state, digest):
            self.skipped += 1
            return
        self._last[eid] = (new.state, digest)
        row = (eid, new.state, canonical, digest, new.last_changed.timestamp(), new.last_updated.timestamp(),
               int(old is None or old.state != new.state))
        self.recorded += 1
        if self.writer is not None:
            self.writer.submit("row", row)
        else:
            with self.store._lock:
                self.store.write_history(self.store._db, [row])

    # -- jobs on the writer's connection ------------------------------------------
    async def run_job(self, fn: Callable[[sqlite3.Connection | None], Any]) -> Any:
        if self.writer is not None and self.writer.alive:
            # Queueing may wait for room, so it happens off the loop too.
            return await asyncio.wrap_future(await asyncio.to_thread(self.writer.run_job, fn))
        return await asyncio.to_thread(fn, None)

    async def purge(self, keep_days: float | None = None, *, repack: bool = False, apply_filter: bool = False) -> int:
        days = self.keep_days if keep_days is None else keep_days
        excluded = self._excluded

        def job(db: sqlite3.Connection | None) -> int:
            n = self.store.prune_history(days, db)
            if apply_filter:
                n += self.store.purge_entities(excluded, 0, db)
            if repack:
                self.store.repack(db)
            return n

        n = await self.run_job(job)
        if apply_filter:
            self._last.clear()
        log.info("recorder purge: %d rows older than %g days%s%s", n, days,
                 " and every excluded entity's" if apply_filter else "", ", repacked" if repack else "")
        return n

    async def purge_entities(self, match: Callable[[str], bool], keep_days: float = 0) -> int:
        n = await self.run_job(lambda db: self.store.purge_entities(match, keep_days, db))
        for eid in [e for e in self._last if match(e)]:
            self._last.pop(eid, None)
        return n

    def stats(self) -> dict[str, Any]:
        w = self.writer
        return {
            "threaded": w is not None, "running": bool(w and w.alive),
            "recorded": self.recorded, "skipped_unrecorded_only": self.skipped, "excluded": self.excluded,
            "queued": w.q.qsize() if w else 0, "written": w.written if w else self.recorded,
            "dropped": w.dropped if w else 0, "failed": w.failed if w else 0, "batches": w.batches if w else 0,
        }

    def health(self) -> dict[str, Any]:
        """Cheap enough for /health: the file size and the writer's backlog."""
        path = self.store.path
        size = os.path.getsize(path) if path != ":memory:" and os.path.exists(path) else 0
        s = self.stats()
        return {"db_bytes": size, "queued": s["queued"], "dropped": s["dropped"], "failed": s["failed"],
                "running": s["running"] or not s["threaded"], "keep_days": self.keep_days}


# -- services ---------------------------------------------------------------------------------------------------

def _days(value: Any, default: float, name: str = "keep_days") -> float:
    if value is None:
        return default
    try:
        days = float(value)
    except (TypeError, ValueError):
        raise HubError(f"{name} must be a number of days") from None
    if days < 0:
        raise HubError(f"{name} cannot be negative")
    return days


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return [str(v) for v in value]


def register_services(hub: Any, recorder: Recorder) -> None:
    async def purge(_hub: Any, call: ServiceCall) -> dict[str, Any]:
        days = _days(call.data.get("keep_days"), recorder.keep_days)
        n = await recorder.purge(days, repack=bool(call.data.get("repack")),
                                 apply_filter=bool(call.data.get("apply_filter")))
        return {"purged_rows": n}

    async def purge_entities(_hub: Any, call: ServiceCall) -> dict[str, Any]:
        ids, domains, globs = list(call.entity_ids), _as_list(call.data.get("domains")), \
            _as_list(call.data.get("entity_globs"))
        if not (ids or domains or globs):
            raise HubError("recorder.purge_entities needs an entity, a domain or a glob")
        n = await recorder.purge_entities(entity_matcher(ids, domains, globs),
                                          _days(call.data.get("keep_days"), 0))
        return {"purged_rows": n}

    hub.register_service(DOMAIN, "purge", purge, {"keep_days": {}, "repack": {}, "apply_filter": {}})
    hub.register_service(DOMAIN, "purge_entities", purge_entities,
                         {"entity_id": {}, "domains": {}, "entity_globs": {}, "keep_days": {}})
    hub.admin_services |= {(DOMAIN, "purge"), (DOMAIN, "purge_entities")}


# -- admin API ----------------------------------------------------------------------------------------------------

def build_router(runtime: Any, auth: Any) -> APIRouter:
    from services.homehub.auth import bearer

    router = APIRouter()

    def admin(request: Request) -> None:
        token = bearer(request.headers.get("authorization")) or request.headers.get("x-internal-secret")
        p = auth.authenticate(token)
        if p is None or not p.is_admin:
            raise HTTPException(401, detail="Unauthorized")

    def recorder() -> Recorder:
        rec = getattr(runtime, "recorder", None)
        if rec is None:
            raise HTTPException(503, detail="The recorder is not running")
        return rec

    def payload(rec: Recorder) -> dict[str, Any]:
        return {"settings": rec.settings, "defaults": default_settings(),
                "unrecorded_attributes": {"all": sorted(ALL_DOMAINS_UNRECORDED),
                                          **{d: sorted(a) for d, a in sorted(UNRECORDED_ATTRIBUTES.items())}},
                "stats": rec.stats()}

    @router.get("/hub/recorder")
    async def get_settings(request: Request) -> dict[str, Any]:
        admin(request)
        return payload(recorder())

    @router.put("/hub/recorder")
    async def put_settings(request: Request) -> dict[str, Any]:
        admin(request)
        rec = recorder()
        try:
            body = await request.json()
        except ValueError:
            body = None
        try:
            settings = validate_settings(body, rec.settings)
        except ValueError as exc:
            raise HTTPException(400, detail=str(exc)) from None
        await asyncio.to_thread(rec.save_settings, settings)
        return payload(rec)

    @router.get("/hub/recorder/size")
    async def size(request: Request) -> dict[str, Any]:
        admin(request)
        rec = recorder()
        report = await asyncio.to_thread(rec.store.size_report)
        return {**report, "keep_days": rec.keep_days, "writer": rec.stats()}

    return router
