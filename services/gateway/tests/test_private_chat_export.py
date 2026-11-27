"""Exporting and restoring the private chat from the app: always encrypted, members only, merged by id.

The chat is a fake one sealed exactly as the phones seal one
(tests/unit/private_chat_backup_fixture.py). The export is opened with the
restore script's own code, so it is checked to be the nightly backup format;
a restore is driven the way the app drives it (the device has opened the file
and sends back what was in it, sealed).
"""
from __future__ import annotations

import copy
import io
import json
import sys
from pathlib import Path

import pytest

from services.gateway import private_chat, private_chat_export
from services.gateway.tests.test_private_chat import PEOPLE, _as, env  # noqa: F401  (env is a fixture)

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests" / "unit"))

import private_chat_backup_fixture as fixture  # noqa: E402

opener = fixture.opener
import hashlib  # noqa: E402

#: The fake chat with what a real record always has (the vectors leave out what the crypto does not need).
CHAT = {**fixture.VECTORS["v2_chat"], "created_by": "alexfake", "created_at": fixture.T0 - 86_400,
        "auth_hash": hashlib.sha256(b"fake auth").hexdigest()}
PASSWORD = fixture.VECTORS["passwords"]["second"]
TOKEN = CHAT["token"]
A, B = CHAT["members"]
EXPORT = f"/api/private-chat/chats/{TOKEN}/export"
FAKE_PEOPLE = {
    **PEOPLE,
    A: {"username": A, "display_name": "Alex Fake", "family_role": "father", "is_minor": False, "nextcloud_user": A},
    B: {"username": B, "display_name": "Sam Fake", "family_role": "mother", "is_minor": False, "nextcloud_user": B},
}


@pytest.fixture
def pc(env, monkeypatch):  # noqa: F811  (the imported fixture)
    async def people():
        return dict(FAKE_PEOPLE)

    monkeypatch.setattr(private_chat, "_people", people)
    monkeypatch.setattr(private_chat_export, "_exports", {})
    monkeypatch.setattr(private_chat_export, "_exporting", set())
    return env


def _chat_on_server(pc) -> dict:
    expect = fixture.build_chat(pc["root"], json.loads(json.dumps(CHAT)), PASSWORD)
    record = json.loads((pc["root"] / "chats" / f"{TOKEN}.json").read_text())
    record["storage"] = "store"
    (pc["root"] / "chats" / f"{TOKEN}.json").write_text(json.dumps(record))
    return expect


def _tree(root: Path, *, skip_registry: bool = False) -> dict[str, bytes]:
    """Every file under a chat folder. ``skip_registry`` also leaves out what a restore rewrites by design:
    the registry record, and an id floor of 0 (no floor is the same thing)."""
    def skipped(p: Path) -> bool:
        return skip_registry and (p.parts[-2] == "chats" or (p.name == "meta.json" and json.loads(p.read_text()) == {"floor": 0}))

    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file() and not skipped(p)}


def _open(data: bytes, password: str = PASSWORD) -> dict[str, bytes]:
    """What a device (here, the restore script) finds in an exported file."""
    import tarfile

    sink = io.BytesIO()
    backup = Path(pytest.importorskip("tempfile").mkdtemp()) / "x.jarvis-backup"
    backup.write_bytes(data)
    opener.decrypt_to(backup, password, sink)
    sink.seek(0)
    with tarfile.open(fileobj=sink, mode="r:") as tar:
        return {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}


# --- Export ---------------------------------------------------------------------


def test_a_member_downloads_the_chat_in_the_backup_format(pc):
    _chat_on_server(pc)
    before = _tree(pc["root"])
    resp = pc["client"].get(EXPORT, headers=_as(A))
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/octet-stream"
    assert resp.headers["cache-control"] == "no-store"
    name = resp.headers["x-backup-filename"]
    assert name.startswith("private-chat-") and name.endswith(".jarvis-backup")
    assert A not in name and B not in name and TOKEN not in name
    assert f'filename="{name}"' in resp.headers["content-disposition"]
    # The restore script opens it with the chat password: it is the nightly format, and it holds the whole chat.
    assert resp.content.startswith(private_chat_export.backup_format().MAGIC)
    assert _open(resp.content) == before
    assert _open(resp.content, fixture.VECTORS["passwords"]["first"]) == before
    # Nothing readable went out and nothing was left behind.
    for clear in (b"ustar", b"messages/", b"Good morning", A.encode(), b"jarvis-envelope"):
        assert clear not in resp.content[len(private_chat_export.backup_format().MAGIC) + 4 + 4096:]
    assert _tree(pc["root"]) == before


def test_the_wrong_password_does_not_open_an_export(pc):
    _chat_on_server(pc)
    data = pc["client"].get(EXPORT, headers=_as(B)).content
    with pytest.raises(opener.WrongPassword):
        _open(data, "not the password")


def test_no_clear_temp_file_is_ever_written(pc, monkeypatch):
    _chat_on_server(pc)
    import tempfile

    made: list[str] = []
    real = tempfile.mkstemp

    def spy(*a, **kw):
        made.append("mkstemp")
        return real(*a, **kw)

    monkeypatch.setattr(tempfile, "mkstemp", spy)
    monkeypatch.setattr(tempfile, "NamedTemporaryFile", lambda *a, **kw: made.append("named") or real(*a, **kw))
    assert pc["client"].get(EXPORT, headers=_as(A)).status_code == 200
    assert made == []


@pytest.mark.parametrize("user", ["alex", "kid", "gran", "nochat"])
def test_only_members_may_export(pc, user):
    _chat_on_server(pc)
    resp = pc["client"].get(EXPORT, headers=_as(user))
    assert resp.status_code in (403, 404)
    assert resp.content[:4] != private_chat_export.backup_format().MAGIC[:4]
    assert TOKEN not in resp.text


def test_exports_are_rate_limited_per_chat(pc):
    _chat_on_server(pc)
    for _ in range(private_chat_export.EXPORT_LIMIT):
        assert pc["client"].get(EXPORT, headers=_as(A)).status_code == 200
    resp = pc["client"].get(EXPORT, headers=_as(B))
    assert resp.status_code == 429
    assert int(resp.headers["retry-after"]) > 0


def test_one_export_at_a_time():
    assert private_chat_export._claim_export("tok1", 0.0) == 0
    assert private_chat_export._claim_export("tok1", 1.0) == -1
    private_chat_export._release_export("tok1")
    assert private_chat_export._claim_export("tok1", 2.0) == 0
    private_chat_export._release_export("tok1")


def test_a_chat_without_a_backup_key_is_not_exported(pc):
    _chat_on_server(pc)
    path = pc["root"] / "chats" / f"{TOKEN}.json"
    record = json.loads(path.read_text())
    record.pop("backup_key")
    path.write_text(json.dumps(record))
    resp = pc["client"].get(EXPORT, headers=_as(A))
    assert resp.status_code == 409
    assert "backup key" in resp.json()["detail"]


def test_the_stream_stops_when_the_download_goes_away(pc):
    import asyncio

    _chat_on_server(pc)
    chat = private_chat.get_store().get(TOKEN)
    assert private_chat_export._claim_export(TOKEN, 0.0) == 0

    async def first_only():
        stream = private_chat_export._stream(TOKEN, chat)
        first = await stream.__anext__()
        await stream.aclose()
        return first

    first = asyncio.run(first_only())
    assert first.startswith(private_chat_export.backup_format().MAGIC)
    # The export slot was given back.
    assert private_chat_export._claim_export(TOKEN, 1.0) == 0


# --- Restore -------------------------------------------------------------------


def _device_payload(root: Path) -> dict:
    """What the app reads out of an opened backup (the tar's files), in the shape it uploads."""
    token = TOKEN
    load = lambda p: json.loads(p.read_text())  # noqa: E731
    msgs = root / "messages" / token
    return {
        "registry": load(root / "chats" / f"{token}.json"),
        "blobs": {p.name: p.read_bytes() for p in sorted((root / "blobs" / token).iterdir())},
        "records": {
            "messages": [load(p) for p in sorted((msgs / "m").glob("*.json"))],
            "gallery": [load(p) for p in sorted((root / "gallery" / token).glob("*.json"))],
            "marks": [load(p) for p in sorted((root / "marks" / token).glob("*.json"))],
            "beloved": [load(p) for p in sorted((root / "beloved" / token).glob("*/*.json"))],
            "read": load(msgs / "read.json"),
            "floor": load(msgs / "meta.json")["floor"],
        },
    }


def _restore(pc, payload: dict, user: str = A, *, merge: bool = False) -> dict:
    client = pc["client"]
    resp = client.post("/api/private-chat/restore", json={"registry": payload["registry"], "merge": merge}, headers=_as(user))
    if resp.status_code != 200:
        return {"begin": resp}
    have = set(client.get(f"/api/private-chat/chats/{TOKEN}/restore/have", headers=_as(user)).json()["blobs"])
    for blob_id, data in payload["blobs"].items():
        if blob_id in have:
            continue
        put = client.put(f"/api/private-chat/chats/{TOKEN}/restore/blobs/{blob_id}", content=data, headers=_as(user))
        assert put.status_code == 200, put.text
    records = client.post(f"/api/private-chat/chats/{TOKEN}/restore/records", json=payload["records"], headers=_as(user))
    assert records.status_code == 200, records.text
    finish = client.post(f"/api/private-chat/chats/{TOKEN}/restore/finish", headers=_as(user))
    assert finish.status_code == 200, finish.text
    return {"begin": resp, "records": records.json()["result"], "finish": finish.json()}


@pytest.fixture
def backup(tmp_path_factory):
    """A backup's contents as a device found them, from a chat on another server."""
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    expect = fixture.build_chat(elsewhere, json.loads(json.dumps(CHAT)), PASSWORD)
    return {"root": elsewhere, "payload": _device_payload(elsewhere), "expect": expect}


def test_a_chat_missing_here_is_made_again_from_its_backup(pc, backup):
    out = _restore(pc, backup["payload"])
    assert out["begin"].json()["created"] is True
    assert out["records"]["messages"]["added"] == 6
    # Every message, blob, gallery entry, edit, reaction and Beloved record is back, byte for byte, under its own id.
    want = {k: v for k, v in _tree(backup["root"], skip_registry=True).items() if not k.startswith("migration/")}
    got = _tree(pc["root"], skip_registry=True)
    assert got == want
    chat = private_chat.get_store().get(TOKEN)
    assert chat["members"] == sorted([A, B]) and chat["verifier"] == CHAT["verifier"]
    assert chat["backup_key"]["public_key"] == CHAT["backup_key"]["public_key"]
    assert chat["auth_hash"] == CHAT["auth_hash"] or len(chat["auth_hash"]) == 64
    assert "restore" not in chat and chat["storage"] == "store"
    # The other parent is told, with nothing of the chat in it.
    notice = json.dumps(pc["notices"][-1])
    assert "restored the private chat from a backup" in notice
    assert "Good morning" not in notice


def test_the_restored_chat_exports_to_the_same_thing(pc, backup):
    _restore(pc, backup["payload"])
    files = _open(pc["client"].get(EXPORT, headers=_as(B)).content)
    for name, data in _tree(backup["root"], skip_registry=True).items():
        if not name.startswith("migration/") and not name.endswith("meta.json"):
            assert files[name] == data


def test_restoring_twice_never_duplicates(pc, backup):
    _restore(pc, backup["payload"])
    again = _restore(pc, backup["payload"], merge=True)
    assert again["records"]["messages"] == {"added": 0, "same": 6, "kept": 0}
    assert again["records"]["beloved"]["added"] == 0
    assert again["finish"]["counts"]["messages"] == 6


def test_a_chat_with_data_needs_a_merge_and_keeps_what_is_here(pc, backup):
    _chat_on_server(pc)
    store = private_chat.get_store().root / "messages" / TOKEN / "m"
    # Here: message 3 was deleted after the backup; message 7 is newer than it.
    tomb = {"id": 3, "by": A, "at": fixture.T0 + 120, "sealed": None, "reply_to": 1, "deleted": True, "deleted_at": 1}
    (store / "3.json").write_text(json.dumps(tomb, sort_keys=True))
    newer = {"id": 7, "by": B, "at": fixture.T0 + 999, "sealed": fixture.LINE + "\n\n" + json.loads(
        (store / "1.json").read_text())["sealed"].split("\n\n", 1)[1], "reply_to": None, "deleted": False}
    (store / "7.json").write_text(json.dumps(newer, sort_keys=True))

    first = _restore(pc, backup["payload"])
    assert first["begin"].status_code == 409
    assert first["begin"].json()["needs_merge"] is True
    assert first["begin"].json()["counts"]["messages"] == 7

    merged = _restore(pc, backup["payload"], merge=True)
    result = merged["records"]["messages"]
    assert result["added"] == 0 and result["kept"] + result["same"] == 6
    assert json.loads((store / "3.json").read_text())["deleted"] is True
    assert (store / "7.json").is_file()
    assert sorted(int(p.stem) for p in store.glob("*.json")) == [1, 2, 3, 4, 5, 6, 7]


def test_a_reaction_and_seen_marks_merge_by_id(pc, backup):
    _restore(pc, backup["payload"])
    path = private_chat.get_store().root / "marks" / TOKEN / "3.json"
    mark = json.loads(path.read_text())
    mark["reactions"][0]["seen_by"] = [B]
    path.write_text(json.dumps(mark))
    _restore(pc, backup["payload"], merge=True)
    mark = json.loads(path.read_text())
    assert len(mark["reactions"]) == 1 and sorted(mark["reactions"][0]["seen_by"]) == sorted([A, B])


def test_only_a_member_of_the_backup_may_restore_it(pc, backup):
    resp = _restore(pc, backup["payload"], user="alex")["begin"]
    assert resp.status_code == 403
    assert private_chat.get_store().get(TOKEN) is None
    for user in ("kid", "gran"):
        assert _restore(pc, backup["payload"], user=user)["begin"].status_code == 403


def test_a_backup_of_people_not_here_is_explained(pc, backup, monkeypatch):
    people = {k: v for k, v in FAKE_PEOPLE.items() if k != B}

    async def fewer():
        return people

    monkeypatch.setattr(private_chat, "_people", fewer)
    resp = _restore(pc, backup["payload"])["begin"]
    assert resp.status_code == 409
    assert B in resp.json()["detail"] and "Add them" in resp.json()["detail"]
    assert private_chat.get_store().get(TOKEN) is None


def test_a_couple_with_another_chat_here_is_refused(pc, backup):
    other = {**CHAT, "token": "anotherchat1", "storage": "store"}
    private_chat.get_store().save(other)
    resp = _restore(pc, backup["payload"])["begin"]
    assert resp.status_code == 409 and resp.json()["token"] == "anotherchat1"


def test_mismatched_members_or_key_are_refused(pc, backup):
    _chat_on_server(pc)
    other_members = copy.deepcopy(backup["payload"])
    other_members["registry"]["members"] = [A, "sam"]
    assert _restore(pc, other_members)["begin"].status_code == 409
    other_key = copy.deepcopy(backup["payload"])
    other_key["registry"]["verifier"] = fixture.VECTORS["v1_chat"]["verifier"]
    resp = _restore(pc, other_key, merge=True)["begin"]
    assert resp.status_code == 409 and "another key" in resp.json()["detail"]


def test_nothing_is_accepted_without_an_open_restore(pc, backup):
    _chat_on_server(pc)
    client = pc["client"]
    assert client.put(f"/api/private-chat/chats/{TOKEN}/restore/blobs/{'f' * 32}", content=b"x" * 64, headers=_as(A)).status_code == 409
    assert client.post(f"/api/private-chat/chats/{TOKEN}/restore/records", json={"messages": []}, headers=_as(A)).status_code == 409
    # Nor for the other member, while one member's restore is open.
    assert client.post("/api/private-chat/restore", json={"registry": backup["payload"]["registry"], "merge": True},
                       headers=_as(A)).status_code == 200
    assert client.post(f"/api/private-chat/chats/{TOKEN}/restore/records", json={"messages": []}, headers=_as(B)).status_code == 409
    # Nor into a chat that is not the one in the restore, or by someone outside it.
    assert client.put(f"/api/private-chat/chats/anotherchat1/restore/blobs/{'f' * 32}", content=b"x" * 64,
                      headers=_as(A)).status_code == 404
    assert client.get(f"/api/private-chat/chats/{TOKEN}/restore/have", headers=_as("alex")).status_code == 404


@pytest.mark.parametrize("bad", [
    {"messages": [{"id": 99, "by": A, "at": 1, "sealed": "Good morning in clear", "deleted": False}]},
    {"messages": [{"id": 99, "by": "alex", "at": 1, "sealed": None, "deleted": True}]},
    {"messages": [{"id": 0, "by": A, "at": 1, "sealed": None, "deleted": True}]},
    {"gallery": [{"id": "e" * 32, "by": A, "at": 1, "sealed": "a picture of us"}]},
    {"marks": [{"message_id": 1, "edit": {"by": A, "at": 1, "sealed": "new words in clear"}, "reactions": []}]},
    {"beloved": [{"id": "e" * 32, "kind": "note", "by": A, "at": 1, "sealed": "a love note in clear"}]},
    {"beloved": [{"id": "e" * 32, "kind": "secrets", "by": A, "at": 1, "sealed": "x"}]},
])
def test_only_sealed_records_by_members_are_accepted(pc, backup, bad):
    client = pc["client"]
    assert client.post("/api/private-chat/restore", json={"registry": backup["payload"]["registry"]},
                       headers=_as(A)).status_code == 200
    good = backup["payload"]["records"]["messages"][0]
    resp = client.post(f"/api/private-chat/chats/{TOKEN}/restore/records",
                       json={"messages": [good], **{k: v for k, v in bad.items() if k != "messages"},
                             **({"messages": [good, *bad["messages"]]} if "messages" in bad else {})},
                       headers=_as(A))
    assert resp.status_code in (413, 422)
    # One bad record refuses the whole batch: the good one was not written either.
    assert not (private_chat.get_store().root / "messages" / TOKEN / "m" / "1.json").exists()


def test_a_blob_is_never_replaced_and_must_look_encrypted(pc, backup):
    _restore(pc, backup["payload"])
    client = pc["client"]
    client.post("/api/private-chat/restore", json={"registry": backup["payload"]["registry"], "merge": True}, headers=_as(A))
    blob_id, data = next(iter(backup["payload"]["blobs"].items()))
    url = f"/api/private-chat/chats/{TOKEN}/restore/blobs/{blob_id}"
    assert client.put(url, content=data, headers=_as(A)).json()["result"] == "same"
    assert client.put(url, content=b"\x00" * len(data), headers=_as(A)).json()["result"] == "kept"
    assert (private_chat.get_store().root / "blobs" / TOKEN / blob_id).read_bytes() == data
    assert client.put(f"/api/private-chat/chats/{TOKEN}/restore/blobs/{'9' * 32}", content=b"tiny",
                      headers=_as(A)).status_code == 422
    assert client.put(f"/api/private-chat/chats/{TOKEN}/restore/blobs/not-an-id", content=data,
                      headers=_as(A)).status_code in (404, 422)


def test_the_registry_keeps_only_public_fields(pc, backup):
    payload = copy.deepcopy(backup["payload"])
    payload["registry"]["password"] = "never kept"
    payload["registry"]["talk_room"] = "a-talk-room"
    payload["registry"]["storage"] = "talk"
    _restore(pc, payload)
    chat = private_chat.get_store().get(TOKEN)
    assert "password" not in chat and "talk_room" not in chat and chat["storage"] == "store"
    assert set(chat) <= {"token", "members", "created_by", "created_at", "storage", "version", "kdf", "wrapped_key",
                         "verifier", "auth_hash", "history", "backup_key", "backup_keys_retired", "restored_at",
                         "restored_by", "last_restored_at", "last_restored_by"}


def _padded(record: dict) -> dict:
    """A gallery record padded as the app pads it (privateGalleryModel.padded): one size for all of them."""
    bare = len(json.dumps({**record, "p": ""}, separators=(",", ":")))
    return {**record, "p": " " * max(0, 8192 - bare)}


def test_gallery_membership_records_come_back_so_the_gallery_is_the_same(pc, backup):
    """What is in the gallery is sealed gallery entries (the opt-in start, adds, removes): a restore
    brings every one back under its own id and time, so both phones work out the same gallery."""
    root = backup["root"]
    dk = fixture.data_key(CHAT, PASSWORD)
    photo, poster = fixture._blob_id(1), fixture._blob_id(2)
    records = [
        ("5" * 32, fixture.T0 + 90, {"t": "gallery_start", "kept": [photo]}),
        ("6" * 32, fixture.T0 + 86_500, {"t": "gallery", "op": "add", "blobs": [poster]}),
        ("7" * 32, fixture.T0 + 86_600, {"t": "gallery", "op": "remove", "blobs": [photo]}),
    ]
    for entry_id, at, record in records:
        entry = {"id": entry_id, "by": A, "at": at, "sealed": fixture.seal(dk, _padded(record))}
        # Under the gateway's limit, and nothing about which picture it names is in the clear.
        assert len(entry["sealed"].encode()) <= private_chat.MAX_GALLERY_ENTRY_BYTES
        assert photo not in entry["sealed"] and poster not in entry["sealed"]
        (root / "gallery" / TOKEN / f"{entry_id}.json").write_text(json.dumps(entry))
    sizes = {len(json.loads((root / "gallery" / TOKEN / f"{i}.json").read_text())["sealed"]) for i, _, _ in records}
    assert len(sizes) == 1, "every membership record looks the same size to the server"

    out = _restore(pc, _device_payload(root))
    assert out["records"]["gallery"]["added"] == 4
    for entry_id, at, _record in records:
        here = json.loads((pc["root"] / "gallery" / TOKEN / f"{entry_id}.json").read_text())
        there = json.loads((root / "gallery" / TOKEN / f"{entry_id}.json").read_text())
        assert here == there and here["at"] == at
    # And an export of the restored chat carries them again.
    files = _open(pc["client"].get(EXPORT, headers=_as(B)).content)
    for entry_id, _at, _record in records:
        assert f"gallery/{TOKEN}/{entry_id}.json" in files


def _padded_bytes(record: dict) -> dict:
    """A gallery record padded to 8192 UTF-8 bytes of JSON, as the app pads one with words in it (an edit's text)."""
    bare = len(json.dumps({**record, "p": ""}, separators=(",", ":"), ensure_ascii=False).encode())
    return {**record, "p": " " * max(0, 8192 - bare)}


def _seal_like_the_app(dk: bytes, obj: dict) -> str:
    """fixture.seal, but with words left as UTF-8 the way JSON.stringify writes them (Python escapes them by default)."""
    import base64
    import os

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    iv = os.urandom(12)
    ct = AESGCM(dk).encrypt(iv, json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode(), None)
    envelope = {"kind": "sealed", "title": "Encrypted message", "v": 1,
                "iv": base64.b64encode(iv).decode(), "ct": base64.b64encode(ct).decode()}
    return f"{fixture.LINE}\n\n{fixture.FENCE}\n{json.dumps(envelope, separators=(',', ':'))}\n{fixture.FENCE}"


def test_gallery_edit_records_and_their_pictures_come_back_so_both_phones_show_the_same_version(pc, backup):
    """A gallery edit is a sealed gallery entry naming the original and the edited picture's blob, padded to the
    size of every other gallery record (words and emoji in its recipe included). A restore brings back the entries
    under their own ids and times and the edited blob beside the original, which is never changed."""
    root = backup["root"]
    dk = fixture.data_key(CHAT, PASSWORD)
    original = fixture._blob_id(1)
    edited = "e" * 32
    (root / "blobs" / TOKEN / edited).write_bytes(fixture.encrypt_bytes(dk, b"\xff\xd8 an edited picture"))
    recipe = {"v": 1, "filter": "warm", "adjust": {"brightness": 10, "contrast": 0, "saturation": 0, "warmth": 0, "vignette": 0},
              "rotate": 0, "flipH": False, "flipV": False, "aspect": "free", "crop": None,
              "overlays": [{"id": "t", "kind": "text", "text": "Happy birthday \U0001F382 café", "font": "hand", "color": "#ffffff",
                            "pill": True, "x": 0.5, "y": 0.5, "scale": 1, "rotation": 0}]}
    records = [
        ("8" * 32, fixture.T0 + 90_000, {"t": "gallery_edit", "of": original, "blob": edited, "mime": "image/jpeg", "recipe": recipe}),
        ("9" * 32, fixture.T0 + 90_100, {"t": "gallery_edit", "of": original, "revert": True}),
        ("a" * 32, fixture.T0 + 90_200, {"t": "gallery", "op": "add", "blobs": [original]}),
    ]
    for entry_id, at, record in records:
        entry = {"id": entry_id, "by": A, "at": at, "sealed": _seal_like_the_app(dk, _padded_bytes(record))}
        assert len(entry["sealed"].encode()) <= private_chat.MAX_GALLERY_ENTRY_BYTES
        assert original not in entry["sealed"] and edited not in entry["sealed"]
        (root / "gallery" / TOKEN / f"{entry_id}.json").write_text(json.dumps(entry))
    sizes = {len(json.loads((root / "gallery" / TOKEN / f"{i}.json").read_text())["sealed"]) for i, _, _ in records}
    assert len(sizes) == 1, "an edit, a revert and a membership record look the same size to the server"

    before = (root / "blobs" / TOKEN / original).read_bytes()
    _restore(pc, _device_payload(root))
    store = pc["root"]
    for entry_id, at, _record in records:
        here = json.loads((store / "gallery" / TOKEN / f"{entry_id}.json").read_text())
        assert here == json.loads((root / "gallery" / TOKEN / f"{entry_id}.json").read_text()) and here["at"] == at
    assert (store / "blobs" / TOKEN / edited).read_bytes() == (root / "blobs" / TOKEN / edited).read_bytes()
    assert (store / "blobs" / TOKEN / original).read_bytes() == before
    files = _open(pc["client"].get(EXPORT, headers=_as(B)).content)
    assert f"blobs/{TOKEN}/{edited}" in files
    for entry_id, _at, _record in records:
        assert f"gallery/{TOKEN}/{entry_id}.json" in files
