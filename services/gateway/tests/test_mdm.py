"""The gateway's device-management routes (HANDOVER.md Phase 12).

Identity owns the data; these tests fake identity at the gateway boundary and
check that the MDM routes reuse it, refuse to invent anything (an unreadable
member, a push with no published build), and reach the shared-folder store.
"""
import base64
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway import mdm
from services.gateway.main import app


class _Resp:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def json(self, content_type=None):
        return self._payload


class _Client:
    """A fake internal HTTP client: one canned answer per URL substring."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        for needle, (status, payload) in self.routes.items():
            if needle in url:
                return _Ctx(_Resp(status, payload))
        return _Ctx(_Resp(404, {}))

    def post(self, url, **kwargs):
        self.calls.append(url)
        for needle, (status, payload) in self.routes.items():
            if needle in url:
                return _Ctx(_Resp(status, payload))
        return _Ctx(_Resp(404, {}))


class _Ctx:
    def __init__(self, resp):
        self.resp = resp

    async def __aenter__(self):
        return self.resp

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def world(monkeypatch, tmp_path):
    """A gateway with identity faked and the shared folders under tmp_path."""
    state = {"admin": "dad", "caller": "dad", "devices": {}, "internal": {}, "updates": {},
             "notices": [], "library_path": "/Books/Text"}

    async def _require_admin(_request):
        return state["admin"]

    async def _require_authenticated(_request):
        return state["caller"]

    async def _as_caller(_request, method, path, **kw):
        if path in state["devices"]:
            return state["devices"][path]
        return 403, {"detail": "Only the father or an admin can see device details"}

    async def _get_app_update_version(_request):
        return state["updates"]

    async def _send(payload, **kw):
        state["notices"].append(payload)
        return True

    async def _fetch_global_setting(_key, default=""):
        return state["library_path"]

    monkeypatch.setattr(gateway_main, "_require_admin", _require_admin)
    monkeypatch.setattr(gateway_main, "_require_authenticated", _require_authenticated)
    monkeypatch.setattr(gateway_main, "_identity_as_caller", _as_caller)
    monkeypatch.setattr(gateway_main, "get_app_update_version", _get_app_update_version)
    monkeypatch.setattr(gateway_main, "fetch_global_setting", _fetch_global_setting)
    monkeypatch.setattr(mdm._notify, "send", _send)
    monkeypatch.setattr(mdm, "MDM_FILES_DIR", tmp_path / "mdm")
    client = _Client(state["internal"])
    monkeypatch.setattr(mdm, "get_client", lambda: client)
    state["client"] = client
    return TestClient(app), state


# --- inventory -------------------------------------------------------------


def test_inventory_aggregates_and_marks_unreadable_members(world):
    client, state = world
    state["internal"]["/api/users"] = (200, [
        {"username": "dad", "display_name": "Dad", "is_admin": True},
        {"username": "kid", "display_name": "Kid", "is_admin": False},
        {"username": "default", "display_name": "System", "is_system_default": True},
    ])
    state["devices"]["/api/family/devices/kid"] = (200, {"devices": [
        {"device_key": "kid-phone-0001", "kind": "phone", "owner_username": "kid",
         "app_version": "1.9.0", "capabilities": {"guard": {"deviceOwner": True}}},
    ]})
    # dad's own devices are refused by identity; that is reported, not invented.
    mdm.queue_notice("kid-phone-0001", "kid", "Jarvis app 2.0.0 is available", "Update now.")
    body = client.get("/api/mdm/devices").json()
    assert [d["device_key"] for d in body["devices"]] == ["kid-phone-0001"]
    assert body["devices"][0]["owner_display_name"] == "Kid"
    assert body["devices"][0]["pending_notices"] == 1  # waiting for its next check-in
    members = {m["username"]: m for m in body["members"]}
    assert members["kid"]["readable"] is True and members["kid"]["device_count"] == 1
    assert members["dad"]["readable"] is False and members["dad"]["device_count"] is None
    assert "default" not in members  # the system account is never a member


def test_inventory_includes_the_callers_own_devices_from_the_user_panel(world):
    client, state = world
    state["internal"]["/api/users"] = (200, [{"username": "dad", "display_name": "Dad", "is_admin": True}])
    # Identity's per-member route refuses the caller themselves, so their own
    # devices come from the user-panel list (and only their own are kept).
    state["devices"]["/api/user-panel/devices"] = (200, [
        {"device_key": "dad-phone-0001", "kind": "phone", "owner_username": "dad", "capabilities": {}},
        {"device_key": "kid-phone-0001", "kind": "phone", "owner_username": "kid", "capabilities": {}},
    ])
    body = client.get("/api/mdm/devices").json()
    assert [d["device_key"] for d in body["devices"]] == ["dad-phone-0001"]
    assert body["members"][0]["readable"] is True


# --- app-update push -------------------------------------------------------


def test_update_push_refuses_when_no_build_is_published(world):
    client, state = world
    state["updates"] = {"bundle_available": False, "apk_available": False}
    resp = client.post("/api/mdm/devices/update", json={"usernames": ["kid"]})
    assert resp.status_code == 409
    assert state["notices"] == []


def test_update_push_notices_the_owner_without_a_parents_only_audience(world):
    client, state = world
    state["updates"] = {"bundle_available": True, "apk_available": True,
                        "version": "2.0.0", "apk_version_code": 412}
    resp = client.post("/api/mdm/devices/update", json={"usernames": ["kid"]})
    assert resp.status_code == 200 and resp.json()["sent"] == 1
    assert resp.json()["queued"] == 0  # a person is pushed to, not queued for
    note = state["notices"][0]
    assert note["user"] == "kid" and note["category"] == "app_updates"
    assert "audience" not in note  # reaches the device owner, a minor included
    assert note["actions"] == [{"label": "Update now", "type": "view", "url": "/settings"}]


def test_update_push_queues_a_notice_for_the_named_device_not_the_person(world):
    """Selecting one phone must not tell the person's other phone."""
    client, state = world
    state["updates"] = {"bundle_available": True, "apk_available": False, "version": "2.0.0"}
    state["internal"]["/api/internal/device-registered/kid-phone-0001"] = (200, {"registered": True, "owner": "kid"})
    resp = client.post("/api/mdm/devices/update", json={"device_keys": ["kid-phone-0001"]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["sent"] == 0 and body["queued"] == 1
    assert body["owners"] == [] and body["devices"] == ["kid-phone-0001"]
    assert state["notices"] == []  # no push at all went out to the person

    queued = mdm.notices_for("kid-phone-0001")
    assert len(queued) == 1
    note = queued[0]
    assert note["owner"] == "kid"
    assert note["title"] == "Jarvis app 2.0.0 is available"
    assert note["body"].startswith("Update now:")
    assert note["url"] == "/settings" and note["tag"] == "mdm_app_update"
    assert note["actions"] == [{"label": "Update now", "type": "view", "url": "/settings"}]
    assert mdm.notices_for("kid-tab-0002") == []  # only the named phone


def test_update_push_refuses_a_device_key_nothing_owns(world):
    client, state = world
    state["updates"] = {"bundle_available": True, "apk_available": True, "version": "2.0.0"}
    resp = client.post("/api/mdm/devices/update", json={"device_keys": ["ghost-phone"]})
    assert resp.status_code == 422 and "not a registered device" in resp.json()["detail"]
    assert mdm.notices_for("ghost-phone") == []  # nothing queued for a key nothing owns
    assert state["notices"] == []


def test_update_push_refuses_esphome_devices(world):
    client, state = world
    state["updates"] = {"bundle_available": True, "apk_available": True, "version": "2.0.0"}
    resp = client.post("/api/mdm/devices/update", json={"device_keys": ["esphome:744dbd2c9728"]})
    assert resp.status_code == 422 and "ESPHome" in resp.json()["detail"]


def test_a_notice_is_handed_only_to_the_phone_that_owns_it(world, monkeypatch):
    client, state = world
    state["internal"]["/api/internal/device-registered/kid-phone-0001"] = (200, {"registered": True, "owner": "kid"})
    state["internal"]["/api/internal/device-registered/dad-phone-0003"] = (200, {"registered": True, "owner": "dad"})
    state["devices"]["/api/screen-time/kid/rules"] = (200, {"rules": {}, "technical": True})

    async def _no_usage(*_args, **_kwargs):
        return []

    monkeypatch.setattr(gateway_main, "_own_telemetry", _no_usage)
    mine = mdm.queue_notice("kid-phone-0001", "kid", "Jarvis app 2.0.0 is available", "Update now.", url="/settings")
    mdm.queue_notice("kid-tab-0002", "kid", "Jarvis app 2.0.0 is available", "Update now.", url="/settings")

    handed = client.get("/api/screen-time/kid/status", params={"device_key": "kid-phone-0001"}).json()
    assert [n["id"] for n in handed["notices"]] == [mine["id"]]

    # A check-in that does not name itself gets no notices field at all, so
    # "not device-scoped" never reads as "nothing waiting".
    assert "notices" not in client.get("/api/screen-time/kid/status").json()
    # Another member's key is not a way to read their queue.
    assert "notices" not in client.get("/api/screen-time/kid/status", params={"device_key": "dad-phone-0003"}).json()
    # Nor is a key nothing owns.
    assert "notices" not in client.get("/api/screen-time/kid/status", params={"device_key": "ghost"}).json()


def test_a_phone_acknowledges_the_notices_it_showed(world, monkeypatch):
    client, state = world
    state["internal"]["/api/internal/device-registered/kid-phone-0001"] = (200, {"registered": True, "owner": "kid"})
    first = mdm.queue_notice("kid-phone-0001", "kid", "Jarvis app 2.0.0 is available", "Update now.")
    second = mdm.queue_notice("kid-phone-0001", "kid", "Jarvis app 2.0.0 is available", "Update now.")

    state["caller"] = "kid"
    resp = client.post("/api/mdm/devices/notices/ack", json={"device_key": "kid-phone-0001", "ids": [first["id"]]})
    assert resp.status_code == 200 and resp.json()["cleared"] == 1
    assert [n["id"] for n in mdm.notices_for("kid-phone-0001")] == [second["id"]]

    # Someone else's phone is not theirs to clear.
    state["caller"] = "kate"
    resp = client.post("/api/mdm/devices/notices/ack", json={"device_key": "kid-phone-0001", "ids": [second["id"]]})
    assert resp.status_code == 403
    assert len(mdm.notices_for("kid-phone-0001")) == 1

    # An admin acts for the family (the Devices page).
    async def _admin(_request):
        return True

    monkeypatch.setattr(gateway_main, "_caller_is_admin", _admin)
    state["caller"] = "dad"
    resp = client.post("/api/mdm/devices/notices/ack", json={"device_key": "kid-phone-0001", "ids": [second["id"]]})
    assert resp.status_code == 200 and resp.json()["cleared"] == 1
    assert mdm.notices_for("kid-phone-0001") == []


def test_ack_refuses_a_request_that_would_drop_the_wrong_notices(world):
    client, state = world
    state["internal"]["/api/internal/device-registered/kid-phone-0001"] = (200, {"registered": True, "owner": "kid"})
    mdm.queue_notice("kid-phone-0001", "kid", "Jarvis app 2.0.0 is available", "Update now.")
    state["caller"] = "kid"

    assert client.post("/api/mdm/devices/notices/ack", json={"ids": ["x"]}).status_code == 422
    assert client.post("/api/mdm/devices/notices/ack",
                       json={"device_key": "kid-phone-0001", "ids": []}).status_code == 422
    assert client.post("/api/mdm/devices/notices/ack",
                       json={"device_key": "ghost", "ids": ["x"]}).status_code == 422
    assert len(mdm.notices_for("kid-phone-0001")) == 1  # nothing was dropped


def test_queued_notices_are_capped_and_expire(tmp_path):
    root = tmp_path / "m"
    for i in range(mdm.NOTICE_MAX + 2):
        mdm.queue_notice("phone", "kid", f"build {i}", "Update now.", root=root)
    live = mdm.notices_for("phone", root=root)
    assert len(live) == mdm.NOTICE_MAX and live[0]["title"] == "build 2"

    # A notice older than the TTL is not offered: a phone that was off for a
    # week does not suddenly announce a build that has been replaced.
    stale = {"id": "old", "owner": "kid", "title": "old build", "body": "Update now.",
             "at": (datetime.now(UTC) - timedelta(seconds=mdm.NOTICE_TTL_S + 60)).isoformat()}
    data = mdm.read_notices(root)
    data["phone"].append(stale)
    (root / "notice_requests.json").write_text(json.dumps(data))
    assert "old build" not in [n["title"] for n in mdm.notices_for("phone", root=root)]

    assert mdm.clear_notices("phone", ["nope"], root=root) == 0
    assert mdm.clear_notices("phone", [], root=root) == 0  # an empty list names nothing
    assert mdm.clear_notices("phone", root=root) == mdm.NOTICE_MAX + 1  # the stale one goes too
    assert mdm.notices_for("phone", root=root) == []
    assert mdm.clear_notices("phone", root=root) == 0


# --- shared folders --------------------------------------------------------


def test_shared_folder_store_round_trips(tmp_path):
    root = tmp_path / "m"
    mdm.store_file("school", "worksheet.pdf", b"abc", "application/pdf", "dad", root=root)
    listed = mdm.list_folder("school", root=root)
    assert [f["name"] for f in listed] == ["worksheet.pdf"]
    assert listed[0]["size"] == 3 and len(listed[0]["sha256"]) == 64
    assert mdm.pushed_folders("kid", root=root) == []
    mdm.set_push("school", ["Kid", "kid"], root=root)
    assert mdm.pushed_folders("kid", root=root) == ["school"]
    assert mdm.delete_file("school", "worksheet.pdf", root=root) is True
    assert mdm.list_folder("school", root=root) == []


def test_shared_folder_rejects_a_path_in_a_file_name(tmp_path):
    root = tmp_path / "m"
    with pytest.raises(ValueError):
        mdm.store_file("library", "../etc/passwd", b"x", None, "dad", root=root)


def test_upload_list_push_and_device_listing(world):
    client, state = world
    up = client.post("/api/mdm/files/school", files={"file": ("reading.txt", b"hello", "text/plain")})
    assert up.status_code == 200 and up.json()["file"]["name"] == "reading.txt"

    listed = client.get("/api/mdm/files").json()
    school = next(f for f in listed["folders"] if f["folder"] == "school")
    assert [f["name"] for f in school["files"]] == ["reading.txt"]

    pushed = client.post("/api/mdm/files/push", json={"folder": "school", "usernames": ["kid"]})
    assert pushed.status_code == 200 and pushed.json()["pushed_to"] == ["kid"]
    assert state["notices"][-1]["user"] == "kid"

    state["caller"] = "kid"
    mine = client.get("/api/mdm/files/for-me").json()
    assert [f["folder"] for f in mine["folders"]] == ["school"]

    dl = client.get("/api/mdm/files/school/reading.txt")
    assert dl.status_code == 200 and dl.content == b"hello"


def test_upload_to_an_unknown_folder_is_404(world):
    client, _ = world
    assert client.post("/api/mdm/files/music", files={"file": ("a.txt", b"x", "text/plain")}).status_code == 404


# --- remote wipe -----------------------------------------------------------


def _device_row(owner=True):
    return {"devices": [{"device_key": "kid-phone-0001", "kind": "phone",
                         "capabilities": {"guard": {"deviceOwner": owner}}}]}


def test_wipe_needs_an_explicit_confirmation(world):
    client, state = world
    state["internal"]["/api/internal/device-registered/kid-phone-0001"] = (200, {"owner": "kid"})
    state["devices"]["/api/family/devices/kid"] = (200, _device_row())
    assert client.post("/api/mdm/devices/kid-phone-0001/wipe", json={}).status_code == 422
    assert mdm.wipe_requested("kid") is False


def test_wipe_is_refused_on_a_phone_that_is_not_device_owner(world):
    client, state = world
    state["internal"]["/api/internal/device-registered/kid-phone-0001"] = (200, {"owner": "kid"})
    state["devices"]["/api/family/devices/kid"] = (200, _device_row(owner=False))
    resp = client.post("/api/mdm/devices/kid-phone-0001/wipe", json={"confirm": True})
    assert resp.status_code == 409 and mdm.wipe_requested("kid") is False


def test_wipe_queues_for_a_device_owner_phone(world):
    client, state = world
    state["internal"]["/api/internal/device-registered/kid-phone-0001"] = (200, {"owner": "kid"})
    state["devices"]["/api/family/devices/kid"] = (200, _device_row())
    resp = client.post("/api/mdm/devices/kid-phone-0001/wipe", json={"confirm": True})
    assert resp.status_code == 200 and resp.json()["status"] == "queued"
    assert mdm.wipe_requested("kid") is True
    mdm.clear_wipe("kid-phone-0001")
    assert mdm.wipe_requested("kid") is False


def test_the_wipe_queue_is_plain_json(tmp_path):
    root = tmp_path / "m"
    mdm.queue_wipe("k1", "Kid", root=root)
    saved = json.loads((root / "wipe_requests.json").read_text())
    assert saved["k1"]["owner"] == "kid"


def test_an_old_queued_wipe_is_ignored(tmp_path):
    root = tmp_path / "m"
    root.mkdir(parents=True)
    (root / "wipe_requests.json").write_text(json.dumps(
        {"k1": {"owner": "kid", "at": "2020-01-01T00:00:00+00:00"}}))
    assert mdm.wipe_requested("kid", root=root) is False
    mdm.queue_wipe("k1", "kid", root=root)
    assert mdm.wipe_requested("kid", root=root) is True


# --- audit log -------------------------------------------------------------


def test_audit_log_is_append_only_and_newest_first(tmp_path):
    root = tmp_path / "m"
    mdm.record_audit("file_upload", "Dad", target="school/a.txt", detail={"size": 3}, root=root)
    mdm.record_audit("device_wipe", "dad", target="kid-phone-0001", root=root)
    rows = mdm.read_audit(root=root)
    assert [r["action"] for r in rows] == ["device_wipe", "file_upload"]
    assert rows[1]["actor"] == "dad" and rows[1]["target"] == "school/a.txt"
    assert rows[1]["detail"] == {"size": 3}


def test_audit_log_is_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(mdm, "AUDIT_MAX", 3)
    root = tmp_path / "m"
    for i in range(5):
        mdm.record_audit("file_upload", "dad", target=f"school/{i}.txt", root=root)
    rows = mdm.read_audit(root=root)
    assert [r["target"] for r in rows] == ["school/4.txt", "school/3.txt", "school/2.txt"]


def test_each_mdm_action_is_recorded(world):
    client, state = world
    state["updates"] = {"bundle_available": True, "apk_available": False, "version": "2.0.0"}
    client.post("/api/mdm/devices/update", json={"usernames": ["kid"]})
    client.post("/api/mdm/files/school", files={"file": ("reading.txt", b"hello", "text/plain")})
    client.post("/api/mdm/files/push", json={"folder": "school", "usernames": ["kid"]})
    client.delete("/api/mdm/files/school/reading.txt")
    actions = [r["action"] for r in mdm.read_audit()]
    assert actions == ["file_delete", "folder_push", "file_upload", "app_update_push"]
    assert all(r["actor"] == "dad" for r in mdm.read_audit())


def test_audit_route_returns_the_log(world):
    client, _ = world
    mdm.record_audit("device_lock", "dad", target="kid")
    body = client.get("/api/mdm/audit").json()
    assert body["events"][0]["action"] == "device_lock"
    assert body["events"][0]["target"] == "kid"


# --- the Library set from the Calibre shelf --------------------------------


def test_describe_library_keeps_books_and_drops_everything_else():
    entries = [
        {"path": "/Books/Text/Author/Title (1)/Title - Author.epub", "name": "Title - Author.epub",
         "is_dir": False, "size": 1234},
        {"path": "/Books/Text/metadata.db", "name": "metadata.db", "is_dir": False, "size": 10},
        {"path": "/Books/Text/Author/Title (1)/cover.jpg", "name": "cover.jpg", "is_dir": False, "size": 9},
        {"path": "/Books/Text/Author", "name": "Author", "is_dir": True},
        {"path": "/Elsewhere/Escape.pdf", "name": "Escape.pdf", "is_dir": False, "size": 5},
    ]
    books = mdm.describe_library(entries, "/Books/Text")
    assert [b["name"] for b in books] == ["Title - Author.epub"]
    assert books[0]["format"] == "epub" and books[0]["size"] == 1234


def test_library_reports_an_unconfigured_shelf_rather_than_an_empty_one(world):
    client, state = world
    state["library_path"] = "   "
    body = client.get("/api/mdm/library/books").json()
    assert body["configured"] is False and body["books"] == []
    assert "calibre_library_path" in body["reason"]


def test_library_lists_books_from_the_shelf_through_storage(world):
    client, state = world
    state["internal"]["/providers/list"] = (200, {"status": "SUCCESS", "entries": [
        {"path": "/Books/Text/A/Book (1)/Book - A.epub", "name": "Book - A.epub", "is_dir": False, "size": 42},
        {"path": "/Books/Text/metadata.db", "name": "metadata.db", "is_dir": False, "size": 3},
    ]})
    body = client.get("/api/mdm/library/books").json()
    assert body["configured"] is True and body["root"] == "/Books/Text"
    assert [b["name"] for b in body["books"]] == ["Book - A.epub"]
    assert any("providers/list" in call for call in state["client"].calls)


def test_library_reports_a_storage_failure_as_its_own_state(world):
    client, state = world
    state["internal"]["/providers/list"] = (500, {"detail": "boom"})
    body = client.get("/api/mdm/library/books").json()
    assert body["configured"] is True and body["books"] == [] and "error" in body


def test_library_listing_is_bounded_and_says_when_it_was_cut(world, monkeypatch):
    client, state = world
    monkeypatch.setattr(mdm, "LIBRARY_MAX_BOOKS", 1)
    state["internal"]["/providers/list"] = (200, {"status": "SUCCESS", "entries": [
        {"path": "/Books/Text/A/One (1)/One - A.epub", "name": "One - A.epub", "is_dir": False, "size": 1},
        {"path": "/Books/Text/B/Two (2)/Two - B.pdf", "name": "Two - B.pdf", "is_dir": False, "size": 2},
    ]})
    body = client.get("/api/mdm/library/books").json()
    assert len(body["books"]) == 1 and body["count"] == 2 and body["truncated"] is True


def test_library_fetch_returns_the_book_bytes(world):
    client, state = world
    state["internal"]["/providers/fetch"] = (
        200, {"status": "SUCCESS", "content_b64": base64.b64encode(b"a book").decode("ascii")})
    resp = client.post("/api/mdm/library/books/file",
                       json={"path": "/Books/Text/A/Book (1)/Book - A.epub"})
    assert resp.status_code == 200 and resp.content == b"a book"
    assert resp.headers["content-type"].startswith("application/epub")


def test_library_fetch_refuses_a_path_outside_the_shelf(world):
    client, state = world
    resp = client.post("/api/mdm/library/books/file", json={"path": "/etc/passwd"})
    assert resp.status_code == 422
    assert not [c for c in state["client"].calls if "providers/fetch" in c]


def test_library_fetch_is_a_refusal_when_the_shelf_is_not_configured(world):
    client, state = world
    state["library_path"] = ""
    resp = client.post("/api/mdm/library/books/file", json={"path": "/Books/Text/x.pdf"})
    assert resp.status_code == 409


# --- device awareness (phones vs tablets vs ESPHome companion nodes) --------


def test_device_platform_distinguishes_android_from_esphome():
    assert mdm.device_platform({"device_key": "phone-1", "kind": "phone"}) == "android"
    assert mdm.device_platform({"device_key": "tab-1", "kind": "tablet"}) == "android"
    assert mdm.device_platform({"device_key": "esphome:744dbd2c9728", "kind": "watch"}) == "esphome"
    assert mdm.device_platform({"device_key": "esphome:s3-speaker", "kind": "speaker"}) == "esphome"
    assert mdm.device_platform({"device_key": "esphome:fnk0104b", "kind": "screen"}) == "esphome"
    assert mdm.device_platform({"device_key": "node-1", "kind": "assistant", "esphome_version": "2026.9.0"}) == "esphome"


def test_esphome_ota_uploads_firmware_and_records_audit(world):
    client, state = world
    fw_bytes = b"\xe9\x04\x02\x20" + b"esp32-s3-firmware-image"
    state["internal"]["/execute/esphome"] = (
        200,
        {
            "status": "SUCCESS",
            "message": "Uploaded watch.bin to jarvis-watch",
            "service": "esphome",
            "detail": {
                "device": "jarvis-watch",
                "host": "192.168.2.105",
                "method": "esphome_ota",
                "ota_version": 2,
            },
        },
    )
    resp = client.post(
        "/api/mdm/esphome/jarvis-watch/ota",
        files={"file": ("watch.bin", fw_bytes, "application/octet-stream")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "SUCCESS"
    assert body["method"] == "esphome_ota"
    assert body["filename"] == "watch.bin"
    assert body["bytes"] == len(fw_bytes)
    events = mdm.read_audit()
    assert events[0]["action"] == "esphome_ota"
    assert events[0]["target"] == "jarvis-watch"
    assert events[0]["detail"]["filename"] == "watch.bin"


def test_esphome_ota_rejects_non_bin_files(world):
    client, _ = world
    resp = client.post(
        "/api/mdm/esphome/jarvis-watch/ota",
        files={"file": ("notes.txt", b"not a firmware binary", "text/plain")},
    )
    assert resp.status_code == 422
    assert ".bin" in resp.json()["detail"]


def test_update_push_refuses_esphome_devices(world):
    client, state = world
    state["updates"] = {"bundle_available": True, "apk_available": True, "version": "2.0.0"}
    resp = client.post("/api/mdm/devices/update", json={"device_keys": ["esphome:744dbd2c9728"]})
    assert resp.status_code == 422
    assert "ESPHome" in resp.json()["detail"]


def test_wipe_refuses_esphome_devices(world):
    client, _ = world
    resp = client.post("/api/mdm/devices/esphome:744dbd2c9728/wipe", json={"confirm": True})
    assert resp.status_code == 409
    assert "ESPHome" in resp.json()["detail"]


def test_wipe_uses_tablet_noun_for_tablet_devices(world):
    client, state = world
    state["internal"]["/api/internal/device-registered/kid-tab-0001"] = (200, {"owner": "kid"})
    state["devices"]["/api/family/devices/kid"] = (
        200,
        {"devices": [{"device_key": "kid-tab-0001", "kind": "tablet", "capabilities": {"guard": {"deviceOwner": True}}}]},
    )
    resp = client.post("/api/mdm/devices/kid-tab-0001/wipe", json={"confirm": True})
    assert resp.status_code == 200
    assert "tablet" in resp.json()["delivery"]
    mdm.clear_wipe("kid-tab-0001")


def test_workspace_esphome_flash_reads_file_and_calls_execution_ota(world):
    client, state = world
    state["internal"]["/execute/workspace_file_read"] = (
        200,
        {"status": "SUCCESS", "detail": {"content": "ZXNwaG9tZS1iaW4="}},  # base64 for 'esphome-bin'
    )
    state["internal"]["/execute/esphome"] = (
        200,
        {
            "status": "SUCCESS",
            "service": "esphome",
            "message": "Flashed firmware",
            "detail": {"method": "esphome_ota", "bytes": 11},
        },
    )
    resp = client.post(
        "/api/workspaces/ws-speaker/esphome/flash",
        json={"device": "smart-speaker", "path": "smart-speaker.bin"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "SUCCESS"
    assert body["device"] == "smart-speaker"
    events = [e for e in mdm.read_audit() if e["action"] == "esphome_ota"]
    assert len(events) >= 1


def test_a_book_content_type_does_not_depend_on_the_host_mime_table():
    """The slim base image ships no mime table and Python 3.11 does not know the
    book formats, so the Library names them itself instead of trusting
    ``mimetypes`` (which served EPUBs as ``application/octet-stream``)."""
    assert mdm.library_content_type("Book - A.epub") == "application/epub+zip"
    assert mdm.library_content_type("A.MOBI") == "application/x-mobipocket-ebook"
    assert mdm.library_content_type("notes.notabook") == "application/octet-stream"



