"""The gateway's device-management routes (HANDOVER.md Phase 12).

Identity owns the data; these tests fake identity at the gateway boundary and
check that the MDM routes reuse it, refuse to invent anything (an unreadable
member, a push with no published build), and reach the shared-folder store.
"""
import base64
import json

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
    body = client.get("/api/mdm/devices").json()
    assert [d["device_key"] for d in body["devices"]] == ["kid-phone-0001"]
    assert body["devices"][0]["owner_display_name"] == "Kid"
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
    note = state["notices"][0]
    assert note["user"] == "kid" and note["category"] == "app_updates"
    assert "audience" not in note  # reaches the device owner, a minor included
    assert note["actions"] == [{"label": "Update now", "type": "view", "url": "/settings"}]


def test_update_push_resolves_a_device_key_to_its_owner(world):
    client, state = world
    state["updates"] = {"bundle_available": True, "apk_available": False, "version": "2.0.0"}
    state["internal"]["/api/internal/device-registered/kid-phone-0001"] = (200, {"registered": True, "owner": "kid"})
    resp = client.post("/api/mdm/devices/update", json={"device_keys": ["kid-phone-0001"]})
    assert resp.status_code == 200 and resp.json()["owners"] == ["kid"]


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
    assert mdm.device_platform({"device_key": "node-1", "kind": "assistant", "esphome_version": "2026.9.0"}) == "esphome"


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


