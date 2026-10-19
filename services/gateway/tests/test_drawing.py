"""The family drawing board is shared live, one room at a time."""
import pytest
from fastapi.testclient import TestClient

from services.gateway import drawing
from services.gateway.main import app


@pytest.fixture(autouse=True)
def fresh_hub(monkeypatch):
    monkeypatch.setattr(drawing, "hub", drawing.DrawingHub())

    async def fake_user(token):
        if token == "mom-token":
            return {"username": "mom", "full_name": "Mom"}
        if token == "kid-token":
            return {"username": "kid", "full_name": "Kid"}
        return None

    monkeypatch.setattr(drawing, "_user_for_token", fake_user)


def _segment(segment_id="s1", **overrides):
    segment = {
        "id": segment_id,
        "x1": 10,
        "y1": 20,
        "x2": 30,
        "y2": 40,
        "color": "#2563eb",
        "width": 6,
    }
    segment.update(overrides)
    return segment


def test_two_people_draw_on_one_board_and_late_joiners_replay_it():
    c = TestClient(app)

    with c.websocket_connect("/api/family/drawing/ws?token=mom-token&room=family") as mom:
        mom_welcome = mom.receive_json()
        assert mom_welcome["type"] == "welcome"
        assert mom_welcome["self"]["name"] == "Mom"
        assert mom_welcome["segments"] == []
        assert [p["name"] for p in mom_welcome["participants"]] == ["Mom"]

        with c.websocket_connect("/api/family/drawing/ws?token=kid-token&room=family") as kid:
            kid_welcome = kid.receive_json()
            assert [p["name"] for p in kid_welcome["participants"]] == ["Mom", "Kid"]

            joined = mom.receive_json()
            assert joined == {
                "type": "participant-joined",
                "participant": kid_welcome["self"],
            }

            kid.send_json({"type": "segment", "segment": _segment("s1")})
            relayed = mom.receive_json()
            assert relayed["type"] == "segment"
            assert relayed["segment"]["id"] == "s1"
            assert relayed["segment"]["user"] == "kid"
            assert relayed["segment"]["name"] == "Kid"

            kid.send_json({"type": "segment", "segment": _segment("s2", x2=60)})
            assert mom.receive_json()["segment"]["id"] == "s2"

            # A second board sees none of this and starts blank.
            with c.websocket_connect("/api/family/drawing/ws?token=mom-token&room=other") as other:
                other_welcome = other.receive_json()
                assert other_welcome["segments"] == []
                assert [p["name"] for p in other_welcome["participants"]] == ["Mom"]

            # The next person to join gets the strokes already drawn.
            with c.websocket_connect("/api/family/drawing/ws?token=mom-token&room=family") as late:
                late_welcome = late.receive_json()
                assert [s["id"] for s in late_welcome["segments"]] == ["s1", "s2"]
                assert [p["name"] for p in late_welcome["participants"]] == ["Mom", "Kid", "Mom"]
                # The existing board learns about the late joiner too.
                assert mom.receive_json()["participant"]["name"] == "Mom"
                late.send({"type": "websocket.disconnect", "code": 1000})
                assert mom.receive_json()["type"] == "participant-left"

            kid.send_json({"type": "undo", "id": "s1"})
            assert mom.receive_json() == {"type": "undo", "id": "s1"}

            kid.send_json({"type": "clear"})
            cleared = mom.receive_json()
            assert cleared["type"] == "clear"
            assert cleared["by"]["username"] == "kid"


def test_a_drawer_only_undoes_their_own_last_stroke():
    c = TestClient(app)

    with c.websocket_connect("/api/family/drawing/ws?token=mom-token&room=family") as mom:
        mom.receive_json()
        with c.websocket_connect("/api/family/drawing/ws?token=kid-token&room=family") as kid:
            kid.receive_json()
            mom.receive_json()

            kid.send_json({"type": "segment", "segment": _segment("kid-1")})
            assert mom.receive_json()["segment"]["id"] == "kid-1"

            # Mom naming the kid's stroke must not remove it.
            mom.send_json({"type": "undo", "id": "kid-1"})
            kid.send_json({"type": "segment", "segment": _segment("kid-2")})
            assert mom.receive_json()["segment"]["id"] == "kid-2"

            kid.send_json({"type": "undo"})
            assert mom.receive_json() == {"type": "undo", "id": "kid-2"}


def test_invalid_geometry_never_reaches_the_other_canvas():
    c = TestClient(app)

    with c.websocket_connect("/api/family/drawing/ws?token=mom-token&room=family") as mom:
        mom.receive_json()
        with c.websocket_connect("/api/family/drawing/ws?token=kid-token&room=family") as kid:
            kid.receive_json()
            mom.receive_json()

            kid.send_json({"type": "segment", "segment": _segment("bad", x1=-1)})
            kid.send_json({"type": "segment", "segment": _segment("bad", x1="not a number")})
            kid.send_json({"type": "segment", "segment": _segment("bad", width=999)})

            # The next accepted stroke proves the invalid ones were dropped.
            kid.send_json({"type": "segment", "segment": _segment("good", color="red")})
            relayed = mom.receive_json()
            assert relayed["segment"]["id"] == "good"
            assert relayed["segment"]["color"] == "#1e293b"


def test_a_closed_board_tells_the_people_still_drawing():
    c = TestClient(app)

    with c.websocket_connect("/api/family/drawing/ws?token=mom-token&room=family") as mom:
        mom.receive_json()
        with c.websocket_connect("/api/family/drawing/ws?token=kid-token&room=family") as kid:
            kid_welcome = kid.receive_json()
            mom.receive_json()

            kid.send({"type": "websocket.disconnect", "code": 1000})
            assert mom.receive_json() == {"type": "participant-left", "id": kid_welcome["self"]["id"]}
