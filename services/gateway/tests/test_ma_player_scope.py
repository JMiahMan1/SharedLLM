"""P2-T26 / §7.3: per-user MA player scope on WS /api/ma-jsonrpc.

Physical players map onto HA ``media_player.mass_<id>`` entities and are
checked against Identity's device assignments; the browser's own web player
(announced on the sendspin socket) is exempt for that user.
"""
import asyncio
import json
import threading
from unittest.mock import AsyncMock

import pytest
import websockets

import services.gateway.main as gateway_main
import services.gateway.ma_scope as ma_scope
from services.execution.entity_access import EntityPermissions


@pytest.fixture(autouse=True)
def _clean_web_player_ids():
    ma_scope.forget_web_player_ids()
    yield
    ma_scope.forget_web_player_ids()


def _perms(assigned=(), protected=(), permitted=()):
    return EntityPermissions(
        assigned=frozenset(assigned),
        protected=frozenset(protected),
        permitted=frozenset(permitted),
    )


def _frame(command="players/cmd/play", message_id="m1", **args):
    return json.dumps({"message_id": message_id, "command": command, "args": args})


def test_ma_entity_id_slug():
    assert ma_scope.ma_entity_id("kitchen") == "media_player.mass_kitchen"
    assert ma_scope.ma_entity_id("Office Display") == "media_player.mass_office_display"
    assert ma_scope.ma_entity_id("sendspin-js") == "media_player.mass_sendspin_js"
    assert ma_scope.ma_entity_id("a.b/c") == "media_player.mass_a_b_c"


def test_player_refs_collect_every_reference_key():
    frame = json.loads(
        _frame(
            player_id="p1",
            queue_id="q1",
            target_player="p2",
            source_queue_id="s1",
            target_queue_id="t1",
            child_player_ids=["c1", "c2", ""],
        )
    )
    assert ma_scope.player_refs_in_frame(frame) == {"p1", "q1", "p2", "s1", "t1", "c1", "c2"}


def test_admin_bypasses_without_lookup(monkeypatch):
    lookup = AsyncMock()
    monkeypatch.setattr(ma_scope, "load_entity_permissions", lookup)
    result = asyncio.run(
        ma_scope.check_ma_frame_scope(_frame(player_id="someone_elses"), user="admin", is_admin=True)
    )
    assert result is None
    lookup.assert_not_called()


def test_own_web_player_is_exempt(monkeypatch):
    lookup = AsyncMock()
    monkeypatch.setattr(ma_scope, "load_entity_permissions", lookup)
    ma_scope.record_web_player_id("michele", "web-123")
    result = asyncio.run(
        ma_scope.check_ma_frame_scope(
            _frame(command="player_queues/play_media", queue_id="web-123"),
            user="michele",
            is_admin=False,
        )
    )
    assert result is None
    lookup.assert_not_called()


def test_frame_without_player_refs_is_untouched(monkeypatch):
    lookup = AsyncMock()
    monkeypatch.setattr(ma_scope, "load_entity_permissions", lookup)
    frame = json.dumps({"message_id": "m9", "command": "music/search", "args": {"search_query": "x"}})
    assert asyncio.run(ma_scope.check_ma_frame_scope(frame, user="michele", is_admin=False)) is None
    lookup.assert_not_called()


def test_assigned_player_allowed(monkeypatch):
    monkeypatch.setattr(
        ma_scope,
        "load_entity_permissions",
        AsyncMock(return_value=_perms(assigned={"media_player.mass_kitchen"})),
    )
    result = asyncio.run(
        ma_scope.check_ma_frame_scope(
            _frame(command="player_queues/play_media", queue_id="kitchen"),
            user="michele",
            is_admin=False,
        )
    )
    assert result is None


def test_other_players_player_denied(monkeypatch):
    monkeypatch.setattr(
        ma_scope,
        "load_entity_permissions",
        AsyncMock(return_value=_perms(assigned={"media_player.mass_kitchen"})),
    )
    result = asyncio.run(
        ma_scope.check_ma_frame_scope(
            _frame(message_id="m7", player_id="office"), user="michele", is_admin=False
        )
    )
    assert result is not None
    assert json.loads(result) == {"error_code": "forbidden", "message_id": "m7"}


def test_group_with_one_foreign_child_denied(monkeypatch):
    monkeypatch.setattr(
        ma_scope,
        "load_entity_permissions",
        AsyncMock(return_value=_perms(assigned={"media_player.mass_kitchen"})),
    )
    result = asyncio.run(
        ma_scope.check_ma_frame_scope(
            _frame(
                command="players/cmd/group_many",
                message_id="m8",
                target_player="kitchen",
                child_player_ids=["kitchen", "office"],
            ),
            user="michele",
            is_admin=False,
        )
    )
    assert result is not None
    assert json.loads(result)["message_id"] == "m8"


def test_transfer_checks_both_queues(monkeypatch):
    monkeypatch.setattr(
        ma_scope,
        "load_entity_permissions",
        AsyncMock(
            return_value=_perms(
                assigned={"media_player.mass_kitchen", "media_player.mass_living_room"}
            )
        ),
    )
    allowed = asyncio.run(
        ma_scope.check_ma_frame_scope(
            _frame(
                command="player_queues/transfer",
                source_queue_id="kitchen",
                target_queue_id="living_room",
            ),
            user="michele",
            is_admin=False,
        )
    )
    assert allowed is None
    denied = asyncio.run(
        ma_scope.check_ma_frame_scope(
            _frame(
                command="player_queues/transfer",
                message_id="m10",
                source_queue_id="kitchen",
                target_queue_id="office",
            ),
            user="michele",
            is_admin=False,
        )
    )
    assert denied is not None
    assert json.loads(denied)["message_id"] == "m10"


def test_identity_unreachable_fails_open(monkeypatch):
    monkeypatch.setattr(ma_scope, "load_entity_permissions", AsyncMock(return_value=None))
    result = asyncio.run(
        ma_scope.check_ma_frame_scope(_frame(player_id="office"), user="michele", is_admin=False)
    )
    assert result is None


class _FakeMaWs:
    """Minimal stand-in for a MA server WebSocket (same shape as the
    allowlist test's fake)."""

    def __init__(self, holder: dict):
        self.holder = holder
        self.sent: list[dict] = []
        self.queue: asyncio.Queue = asyncio.Queue()
        self.close_event = threading.Event()
        holder["ws"] = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send(self, text: str) -> None:
        data = json.loads(text)
        if data.get("message_id") == "gateway-auth":
            await self.queue.put(json.dumps({"type": "auth/ok", "message_id": "gateway-auth"}))
            return
        self.sent.append(data)
        await self.queue.put(json.dumps({"message_id": data.get("message_id"), "result": {"ok": True}}))

    async def recv(self):
        while True:
            try:
                return self.queue.get_nowait()
            except asyncio.QueueEmpty:
                if self.close_event.is_set():
                    raise RuntimeError("fake MA closed")
                await asyncio.sleep(0.05)

    async def close(self) -> None:
        pass


NON_ADMIN = {
    "user": "michele",
    "is_admin": False,
    "mass_url": "http://ma:8095",
    "mass_token": "ma-token",
}


def test_ws_proxy_enforces_player_scope(client, monkeypatch):
    """A non-admin family member may drive their own player but not a sibling's."""
    monkeypatch.setattr(gateway_main, "resolve_identity", AsyncMock(return_value=dict(NON_ADMIN)))
    monkeypatch.setattr(
        ma_scope,
        "load_entity_permissions",
        AsyncMock(return_value=_perms(assigned={"media_player.mass_kitchen"})),
    )
    holder: dict = {}
    monkeypatch.setattr(websockets, "connect", lambda *a, **k: _FakeMaWs(holder))

    with client.websocket_connect("/api/ma-jsonrpc?token=test-key") as ws:
        assert json.loads(ws.receive_text()).get("type") == "auth/ok"

        ws.send_text(_frame(command="player_queues/play_media", message_id="ok1", queue_id="kitchen"))
        assert json.loads(ws.receive_text()).get("result") == {"ok": True}

        ws.send_text(_frame(command="player_queues/play_media", message_id="no1", queue_id="office"))
        denied = json.loads(ws.receive_text())
        assert denied == {"error_code": "forbidden", "message_id": "no1"}

        fake_ws = holder.get("ws")
        assert fake_ws is not None
        assert [f.get("message_id") for f in fake_ws.sent] == ["ok1"]
        fake_ws.close_event.set()
