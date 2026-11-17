"""A keyless request may act as the household only from inside the house."""

from services.gateway.client_origin import caller_address, is_inside

CADDY = "172.26.0.18"
FRONT = frozenset({"192.168.2.30"})


def test_direct_container_call_is_inside():
    assert is_inside("172.26.0.5", None, FRONT)


def test_lan_device_through_caddy_is_inside():
    assert is_inside(CADDY, "192.168.2.44", FRONT)


def test_internet_caller_through_caddy_is_outside():
    assert not is_inside(CADDY, "203.0.113.7", FRONT)


def test_internet_caller_through_the_front_proxy_is_outside():
    assert caller_address(CADDY, "203.0.113.7, 192.168.2.30", FRONT) == "203.0.113.7"
    assert not is_inside(CADDY, "203.0.113.7, 192.168.2.30", FRONT)


def test_spoofed_lan_address_on_the_left_is_ignored():
    # The front proxy appends the real caller after whatever the caller sent.
    assert not is_inside(CADDY, "192.168.2.5, 203.0.113.7, 192.168.2.30", FRONT)


def test_lan_hairpin_through_the_front_proxy_is_inside():
    assert is_inside(CADDY, "192.168.2.1, 192.168.2.30", FRONT)


def test_forwarded_header_from_a_public_peer_is_not_believed():
    assert not is_inside("203.0.113.9", "192.168.2.44", FRONT)


def test_wireguard_and_loopback_are_inside_garbage_is_not():
    assert is_inside(CADDY, "10.8.0.3", FRONT)
    assert is_inside("127.0.0.1", None, FRONT)
    assert not is_inside(CADDY, "not-an-ip", FRONT)
    assert not is_inside(None, None, FRONT)


def test_keyless_chat_from_the_internet_is_refused():
    from fastapi.testclient import TestClient

    from services.gateway.main import app

    outside = TestClient(app, client=("172.26.0.18", 50000))
    body = {"model": "jarvis", "messages": [{"role": "user", "content": "which lights are on"}]}
    r = outside.post("/v1/chat/completions", json=body, headers={"X-Forwarded-For": "203.0.113.7"})
    assert r.status_code == 401
    r = outside.post("/api/chat", json=body, headers={"X-Forwarded-For": "203.0.113.7"})
    assert r.status_code == 401
    # Naming a shared device does not get round it.
    r = outside.post(
        "/v1/chat/completions", json={**body, "device_id": "kiosk-1"}, headers={"X-Forwarded-For": "203.0.113.7"}
    )
    assert r.status_code == 401
