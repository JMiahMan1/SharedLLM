"""The fields the chat panel draws from: reactions, replies and who spoke last."""

import pytest

from services.execution.handlers import talk as talk_handler


class FakeProvider:
    username = "u"
    password = "p"
    base_url = "https://cloud.test"


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(talk_handler, "resolve_personal_data_provider", lambda _ctx: FakeProvider())


def request(action, **kwargs):
    from services.execution.schemas import TalkRequest

    return TalkRequest(user_context={"user": "jeremiah"}, action=action, **kwargs)


def test_a_message_carries_its_reactions_and_the_message_it_replies_to():
    summary = talk_handler._message_summary({
        "id": 5,
        "actorId": "michele",
        "actorDisplayName": "Michele",
        "message": "Yes!",
        "reactions": {"👍": 2},
        "reactionsSelf": ["👍"],
        "parent": {"id": 4, "actorDisplayName": "Jeremiah", "message": "Tacos tonight?"},
    })
    assert summary["reactions"] == {"👍": 2}
    assert summary["reactions_self"] == ["👍"]
    assert summary["parent"] == {"id": 4, "actor_display_name": "Jeremiah", "message": "Tacos tonight?"}


def test_a_message_without_extras_has_empty_ones():
    summary = talk_handler._message_summary({"id": 1, "message": "hi"})
    assert summary["reactions"] == {}
    assert summary["parent"] is None


def test_a_conversation_says_who_spoke_last_and_when():
    summary = talk_handler._conversation_summary({
        "token": "t", "displayName": "Family", "type": 2, "unreadMention": True,
        "lastMessage": {"message": "Dinner at 6", "actorDisplayName": "Michele", "actorId": "michele", "timestamp": 1715000000},
    })
    assert summary["last_message_actor"] == "Michele"
    assert summary["last_message_actor_id"] == "michele"
    assert summary["last_message_timestamp"] == 1715000000
    assert summary["type"] == 2
    assert summary["unread_mention"] is True


async def test_a_reply_is_sent_with_reply_to(provider, monkeypatch):
    calls: list[dict] = []

    async def fake_talk_request(_provider, method, endpoint, *, params=None, data=None, timeout=30):
        calls.append({"data": data})
        return True, {"id": 9, "message": "Yes"}, ""

    monkeypatch.setattr(talk_handler, "_talk_request_with_retry", fake_talk_request)
    await talk_handler.handle_talk(request("send", token="room", message="Yes", reply_to=4))
    await talk_handler.handle_talk(request("send", token="room", message="Plain"))

    assert calls[0]["data"] == {"message": "Yes", "replyTo": "4"}
    assert calls[1]["data"] == {"message": "Plain"}


def test_a_shared_file_keeps_what_the_panel_needs_to_draw_it():
    summary = talk_handler._message_summary({
        "id": 7,
        "message": "{file}",
        "messageParameters": {
            "file": {"type": "file", "id": "2025198", "name": "photo.jpg", "mimetype": "image/jpeg",
                     "size": "1024", "path": "Talk/photo.jpg", "preview-available": "yes", "etag": "x"},
            "actor": {"type": "user", "id": "jeremiah", "name": "Jeremiah"},
        },
    })
    assert summary["parameters"]["file"] == {
        "type": "file", "id": "2025198", "name": "photo.jpg", "mimetype": "image/jpeg",
        "size": "1024", "path": "Talk/photo.jpg", "preview-available": "yes",
    }
    # Talk sends [] rather than {} when a message has none.
    assert talk_handler._message_summary({"id": 1, "messageParameters": []})["parameters"] == {}


def test_a_conversation_carries_its_read_markers():
    summary = talk_handler._conversation_summary({"token": "t", "lastCommonReadMessage": 598, "lastReadMessage": 600})
    assert summary["last_common_read"] == 598
    assert summary["last_read"] == 600


def test_a_jarvis_question_is_recognised_however_it_is_typed():
    assert talk_handler.validate_jarvis_mention("@Jarvis what's for dinner")
    assert talk_handler.validate_jarvis_mention("  @jarvis hi")
    assert not talk_handler.validate_jarvis_mention("ask jarvis later")


def test_a_jarvis_answer_is_marked_as_jarvis():
    post = talk_handler._assistant_post("Tacos.")
    assert post.startswith("Tacos.")
    assert '"kind": "assistant"' in post


async def test_a_file_is_uploaded_then_shared_into_the_room(monkeypatch):
    import base64

    uploads, shares = [], []

    class Provider(FakeProvider):
        def sanitize_filename(self, value, fallback):
            return value

        async def ensure_directory(self, path):
            pass

        async def upload_file(self, path, data, content_type):
            uploads.append((path, data, content_type))
            return True

    monkeypatch.setattr(talk_handler, "resolve_personal_data_provider", lambda _ctx: Provider())

    async def fake_talk_request(_provider, method, endpoint, *, params=None, data=None, timeout=30):
        shares.append((endpoint, data))
        return True, {"id": 1}, ""

    monkeypatch.setattr(talk_handler, "_talk_request_with_retry", fake_talk_request)
    result = await talk_handler.handle_talk(request(
        "send_file", token="room", file_base64=base64.b64encode(b"jpegbytes").decode(),
        mime_type="image/jpeg", file_name="photo.jpg", caption="Look!",
    ))

    assert result.status == "SUCCESS"
    assert uploads[0][1] == b"jpegbytes" and uploads[0][2] == "image/jpeg"
    assert uploads[0][0].startswith("Talk Uploads/") and uploads[0][0].endswith("photo.jpg")
    assert shares[0][1]["shareWith"] == "room"
    assert '"caption": "Look!"' in shares[0][1]["talkMetaData"]


async def test_edit_and_delete_hit_the_message(provider, monkeypatch):
    calls = []

    async def fake_talk_request(_provider, method, endpoint, *, params=None, data=None, timeout=30):
        calls.append((method, endpoint, data))
        return True, {"id": 5, "message": "fixed"}, ""

    monkeypatch.setattr(talk_handler, "_talk_request_with_retry", fake_talk_request)
    await talk_handler.handle_talk(request("edit_message", token="room", message_id=5, message="fixed"))
    await talk_handler.handle_talk(request("delete_message", token="room", message_id=5))

    assert calls[0] == ("PUT", "/ocs/v2.php/apps/spreed/api/v1/chat/room/5", {"message": "fixed"})
    assert calls[1][0] == "DELETE" and calls[1][1].endswith("/chat/room/5")


async def test_a_file_path_cannot_climb_out_of_the_users_files(monkeypatch):
    from services.execution.schemas import TalkFileRequest

    monkeypatch.setattr(talk_handler, "resolve_personal_data_provider", lambda _ctx: FakeProvider())
    status, _type, _body = await talk_handler.fetch_talk_file(
        TalkFileRequest(user_context={"user": "jeremiah"}, path="Talk/../../etc/passwd")
    )
    assert status == 400


async def test_pooled_sessions_keep_no_cookies():
    """Sessions are shared by every user of a host; a cookie jar let one
    user's Nextcloud session ride along on another user's request."""
    from aiohttp import DummyCookieJar

    from services.execution import http_client

    session = await http_client.get_session("cloud.example.test")
    try:
        assert isinstance(session.cookie_jar, DummyCookieJar)
    finally:
        await session.close()
        http_client._SESSION_CACHE.clear()
