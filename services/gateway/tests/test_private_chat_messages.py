"""The private chat's own message store, and the move of a chat's history off Talk.

The gateway keeps each private chat's sealed messages itself. These tests check
that it keeps only well-formed ciphertext, for the two members only, with ids
that only grow; that a kill mid-write cannot leave half a record; that unread
counts, read markers, deletes (tombstones, marks with them) and the long poll
behave; that notices carry no content; and that moving a chat made on Talk
keeps every message id, writes an export first, verifies before switching,
never deletes the Talk room unless asked and the room still matches, and
falls back to Talk rather than show an empty history.
"""
import asyncio
import base64
import json
import os
import stat

import httpx
import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse

from services.gateway import main as gateway_main
from services.gateway import private_chat, private_chat_messages
from services.gateway.tests.test_private_chat import _as, _create, _legacy, env  # noqa: F401  (env is a fixture)

TOKEN = "room123abc"
BASE = f"/api/private-chat/chats/{TOKEN}"


def _sealed(n: int = 40, byte: int = 9) -> str:
    """A sealed message the way a device makes one: the locked line and the envelope."""
    iv = base64.b64encode(bytes(range(12))).decode()
    ct = base64.b64encode(bytes([byte]) * n).decode()
    envelope = json.dumps({"kind": "sealed", "title": "Encrypted message", "v": 1, "iv": iv, "ct": ct})
    return f"\U0001f512 Encrypted message\n\n```jarvis-envelope\n{envelope}\n```jarvis-envelope"


@pytest.fixture
def chat(env):  # noqa: F811
    assert _create(env).status_code == 200
    return env


def _send(ctx, user: str, sealed: str | None = None, **extra):
    return ctx["client"].post(f"{BASE}/messages", json={"sealed": sealed or _sealed(), **extra}, headers=_as(user))


def _list(ctx, user: str, **params) -> list[dict]:
    resp = ctx["client"].get(f"{BASE}/messages", params=params, headers=_as(user))
    assert resp.status_code == 200, resp.text
    return resp.json()["detail"]["messages"]


def _record_files(root) -> list:
    folder = root / "messages" / TOKEN / "m"
    return sorted(folder.iterdir()) if folder.is_dir() else []


# --- The store ------------------------------------------------------------------


def test_messages_are_kept_in_order_with_growing_ids_and_read_back_like_talk_messages(chat):
    ids = [_send(chat, user, _sealed(byte=n)).json()["detail"]["message"]["id"] for n, user in enumerate(["alex", "sam", "alex"], 1)]
    assert ids == [1, 2, 3]
    messages = _list(chat, "sam")
    assert [m["id"] for m in messages] == ids
    first = messages[0]
    assert first["actor_id"] == "alex" and first["actor_display_name"] == "Alex"
    assert first["message"] == _sealed(byte=1) and first["message_type"] == "comment" and not first["deleted"]
    assert isinstance(first["timestamp"], int)
    # Sending is reading: the sender's marker is at their own message.
    assert chat["client"].get(f"{BASE}/messages", headers=_as("alex")).json()["detail"]["last_read"] == 3


def test_paging_before_and_after(chat):
    for _ in range(7):
        assert _send(chat, "alex").status_code == 200
    assert [m["id"] for m in _list(chat, "alex", limit=3)] == [5, 6, 7]
    assert [m["id"] for m in _list(chat, "alex", limit=3, before=5)] == [2, 3, 4]
    assert [m["id"] for m in _list(chat, "alex", limit=2, after=2)] == [3, 4]
    assert _list(chat, "alex", after=7) == []


def test_a_page_says_whether_there_are_earlier_messages(chat):
    for _ in range(5):
        assert _send(chat, "alex").status_code == 200

    def has_more(**params) -> bool:
        resp = chat["client"].get(f"{BASE}/messages", params=params, headers=_as("alex"))
        assert resp.status_code == 200, resp.text
        return resp.json()["detail"]["has_more"]

    assert has_more(limit=3) is True
    assert has_more(limit=5) is False
    assert has_more(limit=50) is False
    assert has_more(limit=2, before=3) is False
    assert has_more(limit=1, before=3) is True


def test_a_reply_names_a_message_the_chat_has(chat):
    _send(chat, "alex")
    reply = _send(chat, "sam", reply_to=1).json()["detail"]["message"]
    assert reply["reply_to"] == 1 and reply["parent"] == {"id": 1}
    assert _send(chat, "sam", reply_to=99).status_code == 422


@pytest.mark.parametrize("value", [
    "meet at 6",
    "```jarvis-envelope\n{\"kind\":\"text\",\"title\":\"hi\"}\n```",
    # Readable text beside a real envelope.
    "meet at 6\n" + _sealed(),
    # An envelope whose iv is not 12 bytes.
    _sealed().replace(base64.b64encode(bytes(range(12))).decode(), base64.b64encode(b"short").decode()),
    None,
    42,
])
def test_anything_but_a_sealed_envelope_is_refused(chat, value):
    resp = chat["client"].post(f"{BASE}/messages", json={"sealed": value}, headers=_as("alex"))
    assert resp.status_code == 422
    assert _record_files(chat["root"]) == []


def test_an_oversized_message_is_refused(chat):
    # Past the limit (base64 makes the ciphertext 4/3 as long).
    assert _send(chat, "alex", _sealed(n=private_chat_messages.MAX_MESSAGE_BYTES)).status_code == 413
    assert _record_files(chat["root"]) == []


def test_a_message_with_a_sealed_link_preview_fits(chat):
    """A 60 KB thumbnail and 8,000 characters of text, sealed and base64ed twice, still fit in one message."""
    plain = 8_000 * 4 + 60 * 1024 * 4 // 3 + 2_000
    assert _send(chat, "alex", _sealed(n=plain + 16)).status_code == 200
    assert len(_record_files(chat["root"])) == 1


@pytest.mark.parametrize("outsider", ["kid", "teen", "gran", "uncle", "nochat"])
def test_only_the_two_members_read_write_wait_or_mark(chat, outsider):
    _send(chat, "alex")
    c = chat["client"]
    assert c.get(f"{BASE}/messages", headers=_as(outsider)).status_code in (403, 404)
    assert _send(chat, outsider).status_code in (403, 404)
    assert c.get(f"{BASE}/messages/wait", params={"last_id": 0, "wait": 1}, headers=_as(outsider)).status_code in (403, 404)
    assert c.post(f"{BASE}/read", json={}, headers=_as(outsider)).status_code in (403, 404)
    assert c.delete(f"{BASE}/messages/1", headers=_as(outsider)).status_code in (403, 404)
    assert len(_record_files(chat["root"])) == 1


def test_what_is_on_disk_is_ciphertext_and_public_metadata_only(chat):
    _send(chat, "alex", _sealed(byte=3), reply_to=None)
    (path,) = _record_files(chat["root"])
    record = json.loads(path.read_text())
    assert set(record) == {"id", "by", "at", "sealed", "reply_to", "deleted"}
    assert record["sealed"] == _sealed(byte=3)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


# --- Crash safety ---------------------------------------------------------------


def test_a_kill_mid_write_leaves_no_half_record(chat, monkeypatch):
    _send(chat, "alex", _sealed(byte=1))

    def killed(src, dst):
        raise KeyboardInterrupt  # the process dies between writing the temp file and renaming it

    with monkeypatch.context() as patched, pytest.raises(KeyboardInterrupt):
        patched.setattr(private_chat_messages.os, "replace", killed)
        private_chat_messages.get_store().append(TOKEN, "sam", _sealed(byte=2))
    # Only the first message, whole; no temp file left behind.
    names = [p.name for p in (chat["root"] / "messages" / TOKEN / "m").iterdir()]
    assert names == ["1.json"]
    assert [m["id"] for m in _list(chat, "alex")] == [1]
    # And the next send still gets the next id.
    assert _send(chat, "sam").json()["detail"]["message"]["id"] == 2


def test_a_stray_temp_file_is_ignored(chat):
    _send(chat, "alex")
    folder = chat["root"] / "messages" / TOKEN / "m"
    (folder / ".2.json.dead.tmp").write_text('{"id": 2, "sealed": "half')
    assert [m["id"] for m in _list(chat, "alex")] == [1]


def test_a_damaged_record_is_an_error_not_a_shorter_thread(chat):
    _send(chat, "alex")
    _send(chat, "sam")
    (chat["root"] / "messages" / TOKEN / "m" / "2.json").write_text('{"id": 2, "sealed": "ha')
    resp = chat["client"].get(f"{BASE}/messages", headers=_as("alex"))
    assert resp.status_code == 503


# --- Read markers and the inbox -----------------------------------------------


def _inbox(ctx, user: str, monkeypatch, conversations=None) -> list[dict]:
    async def proxy(request, endpoint, payload=None, *, method="POST", as_user=None):
        return JSONResponse(status_code=200, content={"status": "SUCCESS", "detail": {
            "conversations": [dict(c) for c in (conversations or [{"token": "family1", "last_message": "dinner at 6"}])]}})

    monkeypatch.setattr(gateway_main, "_proxy_execution_with_identity", proxy)
    resp = ctx["client"].get("/api/communication/talk/conversations", headers=_as(user))
    assert resp.status_code == 200, resp.text
    return resp.json()["detail"]["conversations"]


def test_the_inbox_lists_the_chat_with_unread_counts_and_no_content(chat, monkeypatch):
    _send(chat, "alex")
    _send(chat, "alex")
    convs = _inbox(chat, "sam", monkeypatch)
    family, private = convs
    assert family["last_message"] == "dinner at 6" and "private" not in family
    assert private["token"] == TOKEN and private["private"] is True and private["storage"] == "store"
    assert private["unread_messages"] == 2
    assert private["last_message"] == "Encrypted message"
    assert private["last_message_actor_id"] == "alex" and private["last_message_id"] == 2
    assert private["last_activity"] > 0
    assert _sealed() not in json.dumps(convs)
    # Alex has read both (sent them); Sam has not, so nobody-has-read-it-all is 0.
    assert _inbox(chat, "alex", monkeypatch)[1]["unread_messages"] == 0
    assert private["last_common_read"] == 0
    # Someone outside the chat never sees it.
    assert [c["token"] for c in _inbox(chat, "kid", monkeypatch)] == ["family1"]


def test_marking_read_clears_the_count_and_never_moves_back(chat, monkeypatch):
    for _ in range(3):
        _send(chat, "alex")
    c = chat["client"]
    assert c.post(f"{BASE}/read", json={"last_id": 2}, headers=_as("sam")).json()["detail"]["last_read"] == 2
    assert _inbox(chat, "sam", monkeypatch)[1]["unread_messages"] == 1
    assert c.post(f"{BASE}/read", json={"last_id": 1}, headers=_as("sam")).json()["detail"]["last_read"] == 2
    assert c.post(f"{BASE}/read", headers=_as("sam")).json()["detail"]["last_read"] == 3
    # Past the newest message is the newest message.
    assert c.post(f"{BASE}/read", json={"last_id": 999}, headers=_as("sam")).json()["detail"]["last_read"] == 3
    private = _inbox(chat, "sam", monkeypatch)[1]
    assert private["unread_messages"] == 0 and private["last_common_read"] == 3


def test_a_moved_chat_keeps_its_talk_entry_but_counts_from_the_store(chat, monkeypatch):
    _send(chat, "alex")
    talk_entry = {"token": TOKEN, "display_name": "Private chat", "unread_messages": 9, "last_message": _sealed()}
    convs = _inbox(chat, "sam", monkeypatch, [talk_entry])
    assert len(convs) == 1
    assert convs[0]["unread_messages"] == 1 and convs[0]["last_message"] == "Encrypted message"


# --- Waiting --------------------------------------------------------------------


async def _async_client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway_main.app), base_url="http://test")


async def test_a_wait_answers_the_moment_a_message_is_sent(chat):
    async with await _async_client() as client:
        waiting = asyncio.create_task(client.get(f"{BASE}/messages/wait", params={"last_id": 0, "wait": 20}, headers=_as("sam")))
        await asyncio.sleep(0.3)
        assert not waiting.done()
        started = asyncio.get_running_loop().time()
        sent = await client.post(f"{BASE}/messages", json={"sealed": _sealed()}, headers=_as("alex"))
        assert sent.status_code == 200
        resp = await asyncio.wait_for(waiting, 5)
        assert asyncio.get_running_loop().time() - started < 3
    assert resp.status_code == 200
    assert [m["id"] for m in resp.json()["detail"]["messages"]] == [1]


async def test_a_wait_returns_empty_when_its_time_is_up(chat):
    async with await _async_client() as client:
        resp = await client.get(f"{BASE}/messages/wait", params={"last_id": 0, "wait": 1}, headers=_as("sam"))
    assert resp.status_code == 200 and resp.json()["detail"]["messages"] == []


async def test_a_wait_behind_newer_messages_answers_at_once(chat):
    _send(chat, "alex")
    async with await _async_client() as client:
        resp = await asyncio.wait_for(
            client.get(f"{BASE}/messages/wait", params={"last_id": 0, "wait": 20}, headers=_as("sam")), 3)
    assert [m["id"] for m in resp.json()["detail"]["messages"]] == [1]


# --- Deleting -------------------------------------------------------------------


def test_only_the_author_deletes_and_a_tombstone_keeps_the_id(chat):
    _send(chat, "alex", _sealed(byte=5))
    _send(chat, "sam")
    c = chat["client"]
    assert c.delete(f"{BASE}/messages/1", headers=_as("sam")).status_code == 403
    assert c.delete(f"{BASE}/messages/1", headers=_as("alex")).status_code == 200
    assert c.delete(f"{BASE}/messages/1", headers=_as("alex")).status_code == 404
    on_disk = json.loads((chat["root"] / "messages" / TOKEN / "m" / "1.json").read_text())
    assert on_disk["deleted"] is True and on_disk["sealed"] is None and on_disk["by"] == "alex"
    assert _sealed(byte=5) not in json.dumps(on_disk)
    shown = _list(chat, "sam")
    assert [(m["id"], m["deleted"], m["message"]) for m in shown] == [(1, True, ""), (2, False, _sealed())]
    # A deleted id is never handed out again.
    assert _send(chat, "alex").json()["detail"]["message"]["id"] == 3


def test_deleting_takes_the_edit_and_reactions_with_it(chat):
    _send(chat, "alex")
    c = chat["client"]
    assert c.post(f"{BASE}/messages/1/reactions", json={"sealed": _sealed()}, headers=_as("sam")).status_code == 200
    assert c.put(f"{BASE}/messages/1/edit", json={"sealed": _sealed()}, headers=_as("alex")).status_code == 200
    assert (chat["root"] / "marks" / TOKEN / "1.json").exists()
    assert c.delete(f"{BASE}/messages/1", headers=_as("alex")).status_code == 200
    assert not (chat["root"] / "marks" / TOKEN / "1.json").exists()


# --- Video clips and the blobs a message is made of ---------------------------------


#: A video piece the way the device uploads one: a 4 MiB piece plus its IV and tag (shortened here).
PIECE = bytes(range(256)) * 8 + bytes(28)


def _upload(ctx, user: str, data: bytes = PIECE) -> str:
    resp = ctx["client"].post(f"{BASE}/blobs", content=data, headers=_as(user))
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _blob_file(ctx, blob_id: str):
    return ctx["root"] / "blobs" / TOKEN / blob_id


def test_a_clip_is_pieces_and_a_poster_the_message_names_and_deleting_it_takes_every_one(chat):
    pieces = [_upload(chat, "alex") for _ in range(3)]
    poster = _upload(chat, "alex", b"p" * 300)
    resp = _send(chat, "alex", _sealed(byte=7), blobs=[*pieces, poster])
    assert resp.status_code == 200, resp.text
    # The list is kept on the server's record, never shown to the app (it has it inside the encryption).
    shown = resp.json()["detail"]["message"]
    assert "blobs" not in shown
    on_disk = json.loads((chat["root"] / "messages" / TOKEN / "m" / "1.json").read_text())
    assert on_disk["blobs"] == [*pieces, poster]
    # Another blob of the chat (a picture in another message) is untouched by this delete.
    other = _upload(chat, "sam")
    assert _send(chat, "sam", blobs=[other]).status_code == 200
    resp = chat["client"].delete(f"{BASE}/messages/1", headers=_as("alex"))
    assert resp.status_code == 200
    assert resp.json()["detail"]["blobs_deleted"] == 4
    for blob_id in [*pieces, poster]:
        assert not _blob_file(chat, blob_id).exists()
        assert chat["client"].get(f"{BASE}/blobs/{blob_id}", headers=_as("sam")).status_code == 404
    assert _blob_file(chat, other).exists()
    # The tombstone no longer names them.
    tomb = json.loads((chat["root"] / "messages" / TOKEN / "m" / "1.json").read_text())
    assert "blobs" not in tomb and tomb["deleted"] is True


def test_deleting_a_clip_takes_its_edit_reactions_and_blobs_together(chat):
    pieces = [_upload(chat, "alex") for _ in range(2)]
    _send(chat, "alex", blobs=pieces)
    c = chat["client"]
    assert c.post(f"{BASE}/messages/1/reactions", json={"sealed": _sealed()}, headers=_as("sam")).status_code == 200
    # Only the author deletes: the other parent's try leaves every piece in place.
    assert c.delete(f"{BASE}/messages/1", headers=_as("sam")).status_code == 403
    assert all(_blob_file(chat, b).exists() for b in pieces)
    assert c.delete(f"{BASE}/messages/1", headers=_as("alex")).status_code == 200
    assert not (chat["root"] / "marks" / TOKEN / "1.json").exists()
    assert not any(_blob_file(chat, b).exists() for b in pieces)


def test_a_piece_already_gone_does_not_stop_the_delete(chat):
    pieces = [_upload(chat, "alex") for _ in range(2)]
    _send(chat, "alex", blobs=pieces)
    # The sending device deleted one itself (as a Talk-era client does).
    assert chat["client"].delete(f"{BASE}/blobs/{pieces[0]}", headers=_as("alex")).status_code == 200
    resp = chat["client"].delete(f"{BASE}/messages/1", headers=_as("alex"))
    assert resp.status_code == 200 and resp.json()["detail"]["blobs_deleted"] == 1
    assert not _blob_file(chat, pieces[1]).exists()


@pytest.mark.parametrize("blobs", [
    "a" * 32,                          # not a list
    ["../../chats/room123abc.json"],   # not a blob id
    ["A" * 32],                        # not lower-case hex
    [7],
    ["f" * 32],                        # a blob this chat does not have
])
def test_a_message_naming_anything_but_this_chats_blobs_is_refused(chat, blobs):
    assert _send(chat, "alex", blobs=blobs).status_code == 422
    assert _record_files(chat["root"]) == []


def test_the_same_blob_twice_and_too_many_blobs_are_refused(chat, monkeypatch):
    piece = _upload(chat, "alex")
    assert _send(chat, "alex", blobs=[piece, piece]).status_code == 422
    monkeypatch.setattr(private_chat_messages, "MAX_BLOBS_PER_MESSAGE", 2)
    pieces = [_upload(chat, "alex") for _ in range(3)]
    assert _send(chat, "alex", blobs=pieces).status_code == 422
    assert _send(chat, "alex", blobs=pieces[:2]).status_code == 200
    assert private_chat_messages.MAX_BLOBS_PER_MESSAGE == 2


def test_a_clip_message_is_still_only_a_sealed_envelope(chat):
    piece = _upload(chat, "alex")
    for value in ["a video of the kids", '{"t": "video", "chunks": []}', None]:
        assert chat["client"].post(f"{BASE}/messages", json={"sealed": value, "blobs": [piece]}, headers=_as("alex")).status_code == 422
    assert _record_files(chat["root"]) == []
    # The piece stays where it was (the device deletes what it uploaded when the send fails).
    assert _blob_file(chat, piece).exists()


def test_the_pieces_stay_inside_the_one_blob_limit(chat):
    # A 4 MiB piece with its IV and tag is far below the limit, which did not have to move for video.
    assert private_chat.MAX_BLOB_BYTES == 25 * 1024 * 1024 + 12 + 16
    assert private_chat.MAX_BLOB_BYTES > 4 * 1024 * 1024 + 28
    assert private_chat_messages.MAX_BLOBS_PER_MESSAGE >= 14  # 13 pieces of a 50 MiB clip and its poster


def test_a_clip_tells_the_other_parent_who_sent_something_and_nothing_else(chat):
    pieces = [_upload(chat, "alex") for _ in range(2)]
    _send(chat, "alex", _sealed(byte=8), blobs=pieces)
    (notice,) = chat["notices"]
    assert notice["user"] == "sam"
    text = json.dumps(notice)
    assert notice["body"] == "New private message from Alex"
    for secret in [*pieces, "video", "clip"]:
        assert secret not in text


def test_a_message_without_blobs_keeps_the_record_it_always_had(chat):
    _send(chat, "alex", blobs=[])
    record = json.loads(_record_files(chat["root"])[0].read_text())
    assert "blobs" not in record


def test_marks_on_a_moved_chat_check_the_stored_author(chat):
    _send(chat, "alex")
    c = chat["client"]
    assert c.put(f"{BASE}/messages/1/edit", json={"sealed": _sealed()}, headers=_as("sam")).status_code == 403
    assert c.post(f"{BASE}/messages/9/reactions", json={"sealed": _sealed()}, headers=_as("sam")).status_code == 404
    # Talk is never asked.
    assert not any(call.get("action") == "message" for call in chat["calls"])


def test_deleting_the_chat_takes_its_messages(chat):
    _send(chat, "alex")
    assert chat["client"].delete(f"/api/private-chat/chats/{TOKEN}", headers=_as("sam")).status_code == 200
    assert not (chat["root"] / "messages" / TOKEN).exists()
    assert not any(call.get("action") == "delete_conversation" for call in chat["calls"])


# --- Notices --------------------------------------------------------------------


def test_a_new_message_tells_the_other_parent_who_and_nothing_else(chat):
    _send(chat, "alex", _sealed(byte=4))
    (notice,) = chat["notices"]
    assert notice["user"] == "sam"
    assert notice["title"] == "Private chat" and notice["body"] == "New private message from Alex"
    assert not any(a.get("type") == "reply" for a in notice.get("actions", []))
    assert _sealed(byte=4) not in json.dumps(notice)


async def test_no_notice_when_the_other_parent_read_it_in_time(chat, monkeypatch):
    monkeypatch.setattr(private_chat_messages, "NOTICE_DELAY_S", 0.5)
    async with await _async_client() as client:
        await client.post(f"{BASE}/messages", json={"sealed": _sealed()}, headers=_as("alex"))
        await client.post(f"{BASE}/read", json={}, headers=_as("sam"))
        await asyncio.sleep(0.8)
    assert chat["notices"] == []


def test_a_nudge_on_a_moved_chat_reads_the_store(chat):
    c = chat["client"]
    assert c.post(f"{BASE}/nudge", headers=_as("alex")).status_code == 409  # nothing sent yet
    _send(chat, "alex")
    chat["notices"].clear()
    assert c.post(f"{BASE}/nudge", headers=_as("alex")).status_code == 200
    assert chat["notices"][-1]["body"] == "Alex nudged you in your private chat"
    assert c.post(f"{BASE}/nudge", headers=_as("sam")).status_code == 409  # not Sam's message
    c.post(f"{BASE}/read", json={}, headers=_as("sam"))
    chat["redis"].kv.clear()
    assert c.post(f"{BASE}/nudge", headers=_as("alex")).status_code == 409  # read already
    assert not any(call.get("action") == "open" for call in chat["calls"])


async def test_a_call_rings_the_other_member_without_a_talk_room(chat, monkeypatch):
    async def no_talk(*a, **kw):
        raise AssertionError("Talk was asked")

    monkeypatch.setattr(gateway_main, "_talk_detail", no_talk)
    display, recipients = await gateway_main._call_recipients(None, TOKEN, "alex")
    assert display == "Alex" and recipients == {"sam"}
    assert await gateway_main._call_recipients(None, TOKEN, "kid") is None


# --- The old Talk room once a chat has moved -----------------------------------


@pytest.mark.parametrize("action,message", [("send", _sealed()), ("edit_message", _sealed()), ("delete_message", None)])
def test_a_moved_chats_talk_room_takes_no_new_writes(chat, action, message):
    with pytest.raises(HTTPException) as err:
        private_chat.guard_talk(TOKEN, action, message)
    assert err.value.status_code == 403
    private_chat.guard_talk(TOKEN, "messages")  # reading it is still fine


# --- A chat still on Talk goes through Talk -----------------------------------


def test_until_it_moves_the_routes_pass_through_to_talk(env):  # noqa: F811
    _legacy(env)
    c = env["client"]
    assert c.post(f"{BASE}/messages", json={"sealed": _sealed()}, headers=_as("alex")).status_code == 200
    assert c.post(f"{BASE}/messages", json={"sealed": "plain"}, headers=_as("alex")).status_code == 422
    assert c.get(f"{BASE}/messages", headers=_as("alex")).json()["source"] == "talk"
    assert c.post(f"{BASE}/read", json={}, headers=_as("alex")).status_code == 200
    assert [call["action"] for call in env["calls"]] == ["send", "messages", "mark_read"]
    assert not (env["root"] / "messages").exists()


def test_a_chat_on_talk_passes_talks_has_more_through(env, monkeypatch):  # noqa: F811
    _legacy(env)

    async def talk(request, payload):
        assert payload["action"] == "messages"
        return 200, {"status": "SUCCESS", "detail": {"messages": [{"id": 4}], "has_more": True}}

    monkeypatch.setattr(private_chat, "_talk", talk)
    detail = env["client"].get(f"{BASE}/messages", headers=_as("alex")).json()["detail"]
    assert detail["has_more"] is True and [m["id"] for m in detail["messages"]] == [4]


def test_a_talk_page_that_does_not_say_whether_there_is_more_is_an_error(env, monkeypatch):  # noqa: F811
    _legacy(env)

    async def talk(request, payload):
        return 200, {"status": "SUCCESS", "detail": {"messages": [{"id": 4}]}}

    monkeypatch.setattr(private_chat, "_talk", talk)
    resp = env["client"].get(f"{BASE}/messages", headers=_as("alex"))
    assert resp.status_code == 502
    assert "earlier messages" in resp.json()["detail"]


# --- Moving a chat off Talk ---------------------------------------------------


def _raw(mid: int, kind: str = "comment", actor: str = "Alex", message: str | None = None, parent: int | None = None) -> dict:
    """A message as Talk's chat API returns it."""
    return {
        "id": mid, "token": TOKEN, "actorType": "users", "actorId": actor, "actorDisplayName": actor,
        "timestamp": 1_700_000_000 + mid, "messageType": kind,
        "systemMessage": "conversation_created" if kind == "system" else "",
        "message": message if message is not None else ("Message deleted" if kind == "comment_deleted" else _sealed(byte=mid % 250)),
        **({"parent": {"id": parent}} if parent else {}),
    }


def _live_shape() -> list[dict]:
    """Like the live chat: 6 comments, 5 deleted, 7 system lines."""
    return (
        [_raw(i, "system", actor="alex") for i in (100, 101, 102, 110, 120, 130, 140)]
        + [_raw(i, "comment_deleted", actor="sam") for i in (103, 105, 107, 109, 111)]
        + [_raw(104, actor="ALEX"), _raw(106, actor="sam", parent=104), _raw(108), _raw(112, actor="sam"), _raw(113), _raw(150)]
    )


@pytest.fixture
def talk(env, monkeypatch):  # noqa: F811
    """A Talk room with a chat made before the store, and a fake Talk answering as a member."""
    _legacy(env)
    room = {"messages": _live_shape(), "calls": [], "deleted": False, "open": {"last_read": 150, "last_common_read": 108}}

    async def talk_as(username, payload, *, timeout=60.0):
        room["calls"].append({"as": username, **payload})
        action = payload["action"]
        if action == "messages":
            assert payload["export"] is True and payload["limit"] == 200
            ordered = sorted(room["messages"], key=lambda r: r["id"], reverse=True)
            below = payload.get("last_known_id")
            page = [r for r in ordered if below is None or r["id"] < below][:200]
            return 200, {"status": "SUCCESS", "detail": {"messages": [], "raw": page, "export": True}}
        if action == "open":
            return 200, {"status": "SUCCESS", "detail": {"conversation": room["open"]}}
        if action == "delete_conversation":
            assert payload["delete_only"] is True
            room["deleted"] = True
            return 200, {"status": "SUCCESS", "detail": {"deleted": True}}
        raise AssertionError(action)

    monkeypatch.setattr(private_chat_messages, "_talk_as", talk_as)
    env["room"] = room
    return env


def _registry(ctx) -> dict:
    return json.loads((ctx["root"] / "chats" / f"{TOKEN}.json").read_text())


async def test_the_move_keeps_every_id_author_time_and_reply_and_skips_system_lines(talk):
    assert await private_chat_messages.migrate_chat(TOKEN) == "moved"
    store = private_chat_messages.get_store()
    assert store.ids(TOKEN) == [103, 104, 105, 106, 107, 108, 109, 111, 112, 113, 150]
    assert store.get(TOKEN, 104) == {"id": 104, "by": "alex", "at": 1_700_000_104, "sealed": _sealed(byte=104),
                                     "reply_to": None, "deleted": False, "talk_id": 104}
    assert store.get(TOKEN, 106)["reply_to"] == 104 and store.get(TOKEN, 106)["by"] == "sam"
    tomb = store.get(TOKEN, 103)
    assert tomb["deleted"] is True and tomb["sealed"] is None and tomb["by"] == "sam"
    # System lines never become messages, and no new id can land on any id Talk used.
    assert store.floor(TOKEN) == 150
    record = _registry(talk)
    assert record["storage"] == "store" and record["talk_room"] is True
    m = record["migration"]
    assert (m["comments"], m["tombstones"], m["system_skipped"], m["talk_max_id"]) == (6, 5, 7, 150)
    assert m["ids"] == store.ids(TOKEN)
    # The room is kept, read as its owner, and nothing in it moved a read marker or notification.
    assert talk["room"]["deleted"] is False
    assert {c["as"] for c in talk["room"]["calls"]} == {"alex"}
    # Read markers carry over from Talk: the owner's own, the other parent's common one.
    assert store.read_markers(TOKEN) == {"alex": 150, "sam": 108}


async def test_after_the_move_the_thread_reads_the_store_and_new_ids_follow_talks(talk):
    await private_chat_messages.migrate_chat(TOKEN)
    shown = _list(talk, "sam")
    assert [m["id"] for m in shown if not m["deleted"]] == [104, 106, 108, 112, 113, 150]
    assert all(m["message"] == "" for m in shown if m["deleted"])
    assert _send(talk, "alex").json()["detail"]["message"]["id"] == 151
    # An edit made before the move still hangs on its message.
    resp = talk["client"].put(f"{BASE}/messages/104/edit", json={"sealed": _sealed()}, headers=_as("alex"))
    assert resp.status_code == 200


async def test_the_move_writes_a_full_export_first_and_never_overwrites_one(talk, monkeypatch):
    await private_chat_messages.migrate_chat(TOKEN)
    folder = talk["root"] / "migration" / TOKEN
    (export,) = folder.glob("talk-export-*.json")
    assert stat.S_IMODE(export.stat().st_mode) == 0o600
    body = json.loads(export.read_text())
    assert len(body["messages"]) == 18 and body["registry"]["token"] == TOKEN
    assert body["registry"]["verifier"] == _registry(talk)["verifier"]
    # The same second again (a retry): a second file, the first untouched.
    before = export.read_bytes()
    monkeypatch.setattr(private_chat_messages.time, "strftime", lambda *a: export.name[len("talk-export-"):-len(".json")])
    name = private_chat_messages._write_export(TOKEN, {"token": TOKEN}, [])
    assert name != export.name and export.read_bytes() == before


async def test_paging_reads_the_whole_room(talk):
    talk["room"]["messages"] = [_raw(i, actor="alex" if i % 2 else "sam") for i in range(1000, 1450)]
    assert await private_chat_messages.migrate_chat(TOKEN) == "moved"
    assert len(private_chat_messages.get_store().ids(TOKEN)) == 450
    pages = [c.get("last_known_id") for c in talk["room"]["calls"] if c["action"] == "messages"]
    assert pages == [None, 1250, 1050]


async def test_the_move_is_idempotent(talk):
    assert await private_chat_messages.migrate_chat(TOKEN) == "moved"
    calls = len(talk["room"]["calls"])
    assert await private_chat_messages.migrate_chat(TOKEN) == "already"
    assert len(talk["room"]["calls"]) == calls
    # A run cut short after writing records (before the switch) picks up where it left off.
    record = _registry(talk)
    record.pop("storage")
    (talk["root"] / "chats" / f"{TOKEN}.json").write_text(json.dumps(record))
    assert await private_chat_messages.migrate_chat(TOKEN) == "moved"
    assert len(list((talk["root"] / "migration" / TOKEN).glob("talk-export-*.json"))) == 2


async def test_a_count_that_does_not_match_keeps_the_chat_on_talk(talk, monkeypatch):
    real = private_chat_messages.MessageStore.put_migrated

    def drop_one(self, token, record):
        return False if record["id"] == 112 else real(self, token, record)

    monkeypatch.setattr(private_chat_messages.MessageStore, "put_migrated", drop_one)
    with pytest.raises(RuntimeError, match="moved messages"):
        await private_chat_messages.migrate_chat(TOKEN)
    assert "storage" not in _registry(talk)
    assert talk["room"]["deleted"] is False
    # Still on Talk: the thread reads Talk.
    assert talk["client"].get(f"{BASE}/messages", headers=_as("alex")).json()["source"] == "talk"


@pytest.mark.parametrize("bad", [
    _raw(160, message="meet at 6"),                 # readable text someone posted from the Nextcloud app
    _raw(160, kind="voice-message"),                # a kind the chat never sends
    _raw(160, actor="stranger"),                    # nobody in the family
])
async def test_anything_that_cannot_move_keeps_the_chat_on_talk(talk, bad):
    talk["room"]["messages"].append(bad)
    with pytest.raises(RuntimeError):
        await private_chat_messages.migrate_chat(TOKEN)
    assert "storage" not in _registry(talk)
    assert private_chat_messages.get_store().ids(TOKEN) == []
    # The export was still written: whatever happens, there is a copy.
    assert list((talk["root"] / "migration" / TOKEN).glob("talk-export-*.json"))


async def test_paging_that_does_not_move_back_is_an_error_not_a_loop(talk, monkeypatch):
    talk["room"]["messages"] = [_raw(i) for i in range(1000, 1300)]

    async def old_execution(username, payload, *, timeout=60.0):
        page = sorted(talk["room"]["messages"], key=lambda r: r["id"], reverse=True)[:200]
        return 200, {"status": "SUCCESS", "detail": {"messages": [], "raw": page, "export": True}}

    monkeypatch.setattr(private_chat_messages, "_talk_as", old_execution)
    with pytest.raises(RuntimeError, match="Talk room"):
        await private_chat_messages.migrate_chat(TOKEN)
    assert "storage" not in _registry(talk)


async def test_failures_are_retried_with_backoff_and_logged_without_content(talk, monkeypatch, caplog):
    attempts = {"n": 0}
    real = private_chat_messages.migrate_chat

    async def flaky(token):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("Talk is down")
        return await real(token)

    slept: list[float] = []

    async def sleep(s):
        slept.append(s)

    monkeypatch.setattr(private_chat_messages, "migrate_chat", flaky)
    await private_chat_messages.migrate_all(sleep=sleep)
    assert attempts["n"] == 3 and slept == [60, 120]
    assert _registry(talk)["storage"] == "store"
    assert "Encrypted" not in caplog.text and "jarvis-envelope" not in caplog.text


async def test_the_talk_room_is_kept_unless_the_flag_says_otherwise(talk, monkeypatch):
    await private_chat_messages.migrate_chat(TOKEN)
    assert talk["room"]["deleted"] is False

    record = _registry(talk)
    for field in ("storage", "migration", "talk_room"):
        record.pop(field)
    (talk["root"] / "chats" / f"{TOKEN}.json").write_text(json.dumps(record))
    monkeypatch.setattr(private_chat_messages, "DELETE_TALK_ROOM", True)
    await private_chat_messages.migrate_chat(TOKEN)
    assert talk["room"]["deleted"] is True
    after = _registry(talk)
    assert after["talk_room"] is False and after["talk_room_deleted_at"] > 0


async def test_deleting_the_room_refuses_a_chat_that_has_not_moved(talk):
    with pytest.raises(HTTPException) as err:
        await private_chat_messages.delete_talk_room(TOKEN, dry_run=False)
    assert err.value.status_code == 409 and talk["room"]["deleted"] is False


async def test_deleting_the_room_refuses_when_talk_and_the_store_disagree(talk):
    await private_chat_messages.migrate_chat(TOKEN)
    talk["room"]["messages"].append(_raw(149))  # appeared in Talk after the move
    with pytest.raises(HTTPException) as err:
        await private_chat_messages.delete_talk_room(TOKEN, dry_run=False)
    assert err.value.status_code == 409 and talk["room"]["deleted"] is False


async def test_a_dry_run_says_what_would_go_and_deletes_nothing(talk, caplog):
    await private_chat_messages.migrate_chat(TOKEN)
    # A message deleted in the store since the move still matches its Talk original.
    private_chat_messages.get_store().tombstone(TOKEN, 113)
    report = await private_chat_messages.delete_talk_room(TOKEN, dry_run=True)
    assert report == {"deleted": False, "dry_run": True, "would_delete": {"comments": 6, "tombstones": 5, "system": 7}}
    assert talk["room"]["deleted"] is False
    assert "would delete the Talk room" in caplog.text
    assert (await private_chat_messages.delete_talk_room(TOKEN, dry_run=False))["deleted"] is True


def test_the_admin_route_is_for_admins(talk, monkeypatch):
    async def not_admin(request):
        raise HTTPException(status_code=403, detail="Admin only")

    monkeypatch.setattr(gateway_main, "_require_admin", not_admin)
    resp = talk["client"].post(f"/api/private-chat/admin/chats/{TOKEN}/delete-talk-room", headers=_as("alex"))
    assert resp.status_code == 403 and talk["room"]["deleted"] is False


async def test_a_store_missing_moved_messages_falls_back_to_talk(talk, monkeypatch):
    await private_chat_messages.migrate_chat(TOKEN)
    _send(talk, "alex")  # 151, after the move
    for name in ("104.json", "106.json"):
        os.unlink(talk["root"] / "messages" / TOKEN / "m" / name)

    async def talk_history(request, payload):
        assert payload["action"] == "messages"
        from services.execution.handlers.talk import _message_summary

        return 200, {"status": "SUCCESS", "detail": {
            "messages": [_message_summary(r) for r in talk["room"]["messages"]], "has_more": False}}

    monkeypatch.setattr(private_chat, "_talk", talk_history)
    resp = talk["client"].get(f"{BASE}/messages", headers=_as("alex")).json()
    assert resp["source"] == "talk"
    ids = [m["id"] for m in resp["detail"]["messages"]]
    assert 104 in ids and 106 in ids and 151 in ids

