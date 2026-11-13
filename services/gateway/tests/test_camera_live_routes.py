"""Real-time camera video through the gateway: who may start it, and that it goes through the hub only.

The hub is mocked at the HTTP layer (and its WebSocket by a fake session);
identity and permissions are faked as in ``test_camera_routes.py``.
"""
import pytest
from conftest_media import DEFAULT_IDENTITY, TEST_HA_TOKEN, TEST_HA_URL, TEST_USER, mock_upstream
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from services.execution.entity_access import EntityPermissions
from services.gateway import camera_live_routes, camera_routes
from services.gateway import main as gateway_main
from services.shared.media_token import sign

CAM = "camera.backyard_fluent"
OTHER = "camera.garage_door_camera"
OFFER = {"type": "offer", "sdp": "v=0\r\noffer\r\n"}
ANSWER = {"type": "answer", "sdp": "v=0\r\nanswer\r\n"}


@pytest.fixture
def perms(monkeypatch):
    """Admins see everything; a member only the cameras assigned to them."""

    async def load(username, is_admin=False):
        return EntityPermissions.everything() if is_admin else EntityPermissions(assigned=frozenset({OTHER}))

    monkeypatch.setattr(camera_routes, "load_entity_permissions", load)


@pytest.fixture
def member(monkeypatch):
    async def resolve(body):
        return {**DEFAULT_IDENTITY, "is_admin": False}

    monkeypatch.setattr(gateway_main, "resolve_identity", resolve)


def _mq():
    token, _exp = sign(TEST_USER)
    return {"user": TEST_USER, "mt": token}


def _sent(upstream, method, prefix):
    return [(str(url), call.kwargs) for (m, url), calls in upstream.requests.items() if m == method
            and str(url).startswith(prefix) for call in calls]


# -- WebRTC signaling ------------------------------------------------------------------

def test_webrtc_offer_goes_to_the_hub_with_the_callers_token(client, upstream, perms):
    url = f"{TEST_HA_URL}/api/homehub/cameras/{CAM}/live/webrtc"
    mock_upstream(upstream, "POST", url, payload=ANSWER)
    resp = client.post(f"/api/media/cameras/{CAM}/live/webrtc?quality=main", json=OFFER)
    assert resp.status_code == 200 and resp.json() == ANSWER
    (_u, kwargs), = _sent(upstream, "POST", url)
    assert kwargs["headers"]["Authorization"] == f"Bearer {TEST_HA_TOKEN}"
    assert kwargs["headers"]["Content-Type"] == "application/json"
    assert kwargs["params"] == {"quality": "main"}
    assert TEST_HA_TOKEN not in resp.text and TEST_HA_URL not in resp.text


def test_webrtc_takes_the_media_token_from_a_page_without_the_key(upstream, perms, fake_identity):
    mock_upstream(upstream, "POST", f"{TEST_HA_URL}/api/homehub/cameras/{CAM}/live/webrtc",
                  body=b"v=0\r\nanswer\r\n", status=201, content_type="application/sdp")
    browser = TestClient(gateway_main.app)
    resp = browser.post(f"/api/media/cameras/{CAM}/live/webrtc", params=_mq(), content=b"v=0\r\noffer\r\n",
                        headers={"Content-Type": "application/sdp"})
    assert resp.status_code == 201 and resp.headers["content-type"].startswith("application/sdp")
    assert browser.post(f"/api/media/cameras/{CAM}/live/webrtc", json=OFFER).status_code == 401
    forged = browser.post(f"/api/media/cameras/{CAM}/live/webrtc", params={"user": TEST_USER, "mt": "1.bad"},
                          json=OFFER)
    assert forged.status_code == 403


def test_a_camera_the_caller_may_not_see_cannot_be_watched_live(client, upstream, perms, member):
    assert client.post(f"/api/media/cameras/{CAM}/live/webrtc", json=OFFER).status_code == 404
    assert client.get(f"/api/media/cameras/{CAM}/live/stream.mp4").status_code == 404
    assert client.post("/api/media/cameras/light.kitchen/live/webrtc", json=OFFER).status_code == 404
    assert not _sent(upstream, "POST", TEST_HA_URL) and not _sent(upstream, "GET", TEST_HA_URL)


def test_webrtc_refuses_junk_before_the_hub(client, upstream, perms):
    url = f"/api/media/cameras/{CAM}/live/webrtc"
    assert client.post(url, content=b"x", headers={"Content-Type": "text/plain"}).status_code == 415
    assert client.post(url, content=b"x" * (camera_live_routes.MAX_OFFER_BYTES + 1),
                       headers={"Content-Type": "application/sdp"}).status_code == 400
    assert not _sent(upstream, "POST", TEST_HA_URL)


def test_the_hubs_reason_is_passed_on_and_anything_else_is_502(client, upstream, perms):
    url = f"{TEST_HA_URL}/api/homehub/cameras/{CAM}/live/webrtc"
    mock_upstream(upstream, "POST", url, status=409, payload={"message": "an MJPEG camera"})
    resp = client.post(f"/api/media/cameras/{CAM}/live/webrtc", json=OFFER)
    assert resp.status_code == 409 and resp.json()["detail"] == "an MJPEG camera"
    upstream.clear()
    mock_upstream(upstream, "POST", url, status=500, payload={"message": "rtsp://admin:pw@10.0.0.1/x"})
    resp = client.post(f"/api/media/cameras/{CAM}/live/webrtc", json=OFFER)
    assert resp.status_code == 502 and "rtsp" not in resp.text


# -- MP4 --------------------------------------------------------------------------------

def test_mp4_stream_is_relayed_unbuffered(client, upstream, perms):
    url = f"{TEST_HA_URL}/api/homehub/cameras/{CAM}/live/stream.mp4"
    mock_upstream(upstream, "GET", url, body=b"\x00\x00\x00\x18ftypiso5moof", content_type="video/mp4")
    resp = client.get(f"/api/media/cameras/{CAM}/live/stream.mp4", params={"quality": "sub"})
    assert resp.status_code == 200 and resp.content.startswith(b"\x00\x00\x00\x18ftyp")
    assert resp.headers["x-accel-buffering"] == "no" and resp.headers["content-type"] == "video/mp4"
    (_u, kwargs), = _sent(upstream, "GET", url)
    assert kwargs["params"] == {"quality": "sub"}


# -- the MSE WebSocket ------------------------------------------------------------------------

class _Msg:
    def __init__(self, type_, data):
        self.type, self.data = type_, data


class FakeHubWS:
    def __init__(self):
        import aiohttp

        self.out = [_Msg(aiohttp.WSMsgType.TEXT, '{"type":"mse","value":"video/mp4; codecs=\\"avc1.640029\\""}'),
                    _Msg(aiohttp.WSMsgType.BINARY, b"\x00moof")]
        self.sent = []

    def __aiter__(self):
        return self

    async def __anext__(self):
        import asyncio

        if self.out:
            return self.out.pop(0)
        await asyncio.sleep(0.3)
        raise StopAsyncIteration

    async def send_str(self, text):
        self.sent.append(text)

    async def close(self):
        pass


@pytest.fixture
def hub_ws(monkeypatch):
    seen = {}

    class FakeSession:
        def __init__(self, **_kw):
            pass

        def ws_connect(self, url, **kw):
            seen["url"], seen["kw"] = url, kw
            ws = seen.setdefault("ws", FakeHubWS())

            class _CM:
                async def __aenter__(self):
                    return ws

                async def __aexit__(self, *a):
                    return False

            return _CM()

        async def close(self):
            seen["closed"] = True

    monkeypatch.setattr(camera_live_routes.aiohttp, "ClientSession", FakeSession)
    return seen


def test_ws_needs_the_media_token(perms, fake_identity, hub_ws):
    browser = TestClient(gateway_main.app)
    with pytest.raises(WebSocketDisconnect) as exc, browser.websocket_connect(f"/api/media/cameras/{CAM}/live/ws"):
        pass
    assert exc.value.code == 4401 and "url" not in hub_ws


def test_ws_refuses_a_camera_the_caller_may_not_see(perms, member, hub_ws):
    browser = TestClient(gateway_main.app)
    with pytest.raises(WebSocketDisconnect) as exc, browser.websocket_connect(
            f"/api/media/cameras/{CAM}/live/ws?user={_mq()['user']}&mt={_mq()['mt']}"):
        pass
    assert exc.value.code == 4404 and "url" not in hub_ws


def test_ws_relays_mse_through_the_hub_with_the_callers_token(perms, fake_identity, hub_ws):
    browser = TestClient(gateway_main.app)
    q = _mq()
    with browser.websocket_connect(f"/api/media/cameras/{CAM}/live/ws?user={q['user']}&mt={q['mt']}"
                                   "&quality=main") as ws:
        ws.send_text('{"type":"mse","value":"avc1.640029"}')
        ws.send_text('{"type":"publish","value":"rtsp://x"}')
        assert '"mse"' in ws.receive_text()
        assert ws.receive_bytes() == b"\x00moof"
    assert hub_ws["url"] == f"ws://ha.local:8123/api/homehub/cameras/{CAM}/live/ws"
    assert hub_ws["kw"]["headers"]["Authorization"] == f"Bearer {TEST_HA_TOKEN}"
    assert hub_ws["kw"]["params"] == {"quality": "main"}
    assert hub_ws["ws"].sent == ['{"type":"mse","value":"avc1.640029"}']  # "publish" never reaches go2rtc
    assert hub_ws.get("closed") is True


# -- admin settings ----------------------------------------------------------------------------

@pytest.fixture
def admin_hub(monkeypatch):
    seen = []

    async def admin(request):
        auth = request.headers.get("Authorization")
        if auth == "Bearer admin-key":
            return "parent"
        raise HTTPException(status_code=403 if auth else 401, detail="Admin only")

    async def call(method, path, json_body=None):
        seen.append((method, path, json_body))
        return {"configured": True}

    monkeypatch.setattr(gateway_main, "_require_admin", admin)
    monkeypatch.setattr(camera_live_routes, "_admin_hub", call)
    return seen


def test_live_video_settings_are_for_admins(admin_hub):
    c = TestClient(gateway_main.app, raise_server_exceptions=False)
    url = "/api/admin/cameras/live-video"
    assert c.get(url, headers={"Authorization": "Bearer kid"}).status_code == 403
    assert c.put(url, json={}).status_code == 401
    assert c.post(f"{url}/sync", headers={"Authorization": "Bearer kid"}).status_code == 403
    assert admin_hub == []
    admin = {"Authorization": "Bearer admin-key"}
    assert c.get(url, headers=admin).json() == {"configured": True}
    assert c.put(url, json={"webrtc_port": 8555}, headers=admin).status_code == 200
    assert c.put(url, json=[1], headers=admin).status_code == 400
    assert c.post(f"{url}/sync", headers=admin).status_code == 200
    assert admin_hub == [("GET", "/hub/live-video", None), ("PUT", "/hub/live-video", {"webrtc_port": 8555}),
                         ("POST", "/hub/live-video/sync", None)]


def test_motion_recording_settings_are_for_admins(admin_hub):
    c = TestClient(gateway_main.app, raise_server_exceptions=False)
    url = "/api/admin/cameras/recording"
    assert c.get(url, headers={"Authorization": "Bearer kid"}).status_code == 403
    assert c.put(url, json={}).status_code == 401
    assert admin_hub == []
    admin = {"Authorization": "Bearer admin-key"}
    assert c.get(url, headers=admin).json() == {"configured": True}
    assert c.put(url, json={"account": "household"}, headers=admin).status_code == 200
    assert c.put(url, json="x", headers=admin).status_code == 400
    assert admin_hub == [("GET", "/hub/camera-recording", None),
                         ("PUT", "/hub/camera-recording", {"account": "household"})]
