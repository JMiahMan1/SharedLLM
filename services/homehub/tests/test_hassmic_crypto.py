"""hassmic's crypto in Python, byte for byte against vectors computed by hassmic's own C code.

``VECTORS`` came from a small harness compiled against echo-dot-assist's ``src/third_party/monocypher.c``
(commit 1d7d957) with ``arb.c`` ``wrap_key``/``seal``/``take_key``, ``arb_web_key``, ``web.c`` ``voucher``,
``session_key``, ``signed_ok`` and ``respond_s`` copied into it, for the fixed inputs of the protocol spec (G.1).
Only the outputs are kept here.
"""
from __future__ import annotations

import base64

import pytest

from services.homehub import hassmic

HUB_SK = bytes(range(0x00, 0x20))
ECHO_ARB_SK = bytes(range(0x20, 0x40))
ECHO_WEB_SK = bytes(range(0x40, 0x60))
B_SK = bytes(range(0x60, 0x80))
K = bytes([0xA5]) * 32
N = 0x00000000FFFFFFFF
NONCE24 = bytes(range(0x80, 0x98))
VOUCH_NONCE = bytes(range(0xA0, 0xB0))
CTR = "1764000000000001"
REQUEST_BODY = b"wake_engine=microwakeword\n"
RESPONSE_BODY = b'{"applied":1,"errors":"","github_unreachable":""}'

VECTORS = {
    "hub_pk": "8f40c5adb68f25624ae5b214ea767a6ec94d829d3d7b5e1ad1ba6f3e2138285f",
    "echo_arb_pk": "358072d6365880d1aeea329adf9121383851ed21a28e3b75e965d0d2cd166254",
    "echo_web_pk": "79a631eede1bf9c98f12032cdeadd0e7a079398fc786b88cc846ec89af85a51a",
    "b_pk": "675dd574ed7789310b3d2e7681f3790b466c773b1521fecf36577958371ea52f",
    "wrap_key": "6af28aee79a5608453e38887d48a91e44dd14d49970fc96af82926d6132f824c",
    "blob": "8f40c5adb68f25624ae5b214ea767a6ec94d829d3d7b5e1ad1ba6f3e2138285f808182838485868788898a8b8c8d8e8f9091929394"
            "959697a0b17aa46cd9abd9782ff4632dcd0141965654d0ab6149072cf2ea07c05bc630aa57e5ce24772bc7aceeff184827d470",
    "web_voucher_key": "5858ee9dbbdf750380a499171d1deacdabc84964c7189429cd1b65d49fe28b75",
    "voucher": "9aba68487e86fef59cef669b937ef281",
    "voucher_via_homehub2": "137d93bc21b75ab1bdd35d96f9b64458",
    "session_key": "6320f10a17ac5bde73ff8e28467ba2abe3c7e27bb78094332b857a5f34b726ae",
    "request_mac": "97f1025f4edf73160a284df2979b7def",
    "response_mac": "b1f3b8c9a98e795474d86a3ba76a9146",
    "arbitration_mac_key": "b9ebbf9e94eda61d87a482fe6da57f09b840b156717284cc697a161549e65b85",
    # pub = echo_arb_pk, net = N, node "echo-test", ctr 7, no flags, tagged with arbitration_mac_key
    "beacon": "484d413101358072d6365880d1aeea329adf9121383851ed21a28e3b75e965d0d2cd166254ffffffff00000000096563686f2d74"
              "657374070000000000000059d5fc15de4bfd0b6c5fd1a262981535",
}


def v(name: str) -> bytes:
    return bytes.fromhex(VECTORS[name])


def test_public_keys():
    assert hassmic.x25519_public(HUB_SK) == v("hub_pk")
    assert hassmic.x25519_public(ECHO_ARB_SK) == v("echo_arb_pk")
    assert hassmic.x25519_public(ECHO_WEB_SK) == v("echo_web_pk")
    assert hassmic.x25519_public(B_SK) == v("b_pk")


def test_wrap_key_from_both_sides():
    hub_pk, arb_pk = v("hub_pk"), v("echo_arb_pk")
    assert hassmic.wrap_key(HUB_SK, arb_pk, hub_pk, arb_pk, N) == v("wrap_key")
    assert hassmic.wrap_key(ECHO_ARB_SK, hub_pk, hub_pk, arb_pk, N) == v("wrap_key")


def test_sealed_key_blob_matches_c_and_opens_like_take_key():
    blob = hassmic.seal_network_key(HUB_SK, v("echo_arb_pk"), K, N, nonce=NONCE24)
    assert blob == v("blob")
    assert len(blob) == 104 and len(base64.b64encode(blob)) == 140
    assert hassmic.open_network_key(ECHO_ARB_SK, v("blob"), N) == K


def test_a_blob_for_another_network_id_does_not_open():
    with pytest.raises(hassmic.HassmicError):
        hassmic.open_network_key(ECHO_ARB_SK, v("blob"), N + 1)
    tampered = bytearray(v("blob"))
    tampered[-1] ^= 1
    with pytest.raises(hassmic.HassmicError):
        hassmic.open_network_key(ECHO_ARB_SK, bytes(tampered), N)


def test_a_random_nonce_still_opens():
    blob = hassmic.seal_network_key(HUB_SK, v("echo_arb_pk"), K, N)
    assert blob[32:56] != NONCE24 and hassmic.open_network_key(ECHO_ARB_SK, blob, N) == K


def test_the_echo_own_key_is_refused_as_sender():
    with pytest.raises(hassmic.HassmicError):
        hassmic.seal_network_key(ECHO_ARB_SK, v("echo_arb_pk"), K, N)


def test_a_low_order_key_is_refused():
    with pytest.raises(hassmic.HassmicError):
        hassmic.x25519(HUB_SK, bytes(32))


def test_web_voucher_key_and_voucher():
    wk = hassmic.web_voucher_key(K)
    assert wk == v("web_voucher_key")
    assert hassmic.voucher(wk, v("echo_web_pk"), v("b_pk"), VOUCH_NONCE, b"homehub") == v("voucher")
    assert hassmic.voucher(wk, v("echo_web_pk"), v("b_pk"), VOUCH_NONCE, b"homehub2") == v("voucher_via_homehub2")


def test_session_key_from_both_sides():
    web_pk, b_pk = v("echo_web_pk"), v("b_pk")
    assert hassmic.session_key(B_SK, web_pk, web_pk, b_pk) == v("session_key")
    assert hassmic.session_key(ECHO_WEB_SK, b_pk, web_pk, b_pk) == v("session_key")


def test_request_and_response_macs():
    ks = v("session_key")
    assert hassmic.request_mac(ks, "POST", "/api/set", CTR, REQUEST_BODY) == v("request_mac")
    assert hassmic.response_mac(ks, CTR, RESPONSE_BODY) == v("response_mac")


def test_beacon_parse_and_tag():
    beacon = hassmic.parse_beacon(v("beacon"), "192.168.10.21")
    assert beacon is not None
    assert (beacon.pub, beacon.net, beacon.node, beacon.ctr, beacon.ip) == (v("echo_arb_pk"), N, "echo-test", 7, "192.168.10.21")
    assert hassmic.arbitration_mac_key(K) == v("arbitration_mac_key")
    assert beacon.verifies(v("arbitration_mac_key"))
    assert not beacon.verifies(hassmic.arbitration_mac_key(bytes(32)))


def test_member_beacon_is_byte_for_byte_the_c_beacon():
    """HomeHub's member beacon is built exactly as arb.c builds one (same inputs, same bytes)."""
    built = hassmic.member_beacon(v("echo_arb_pk"), N, v("arbitration_mac_key"), 7, node="echo-test", flags=0)
    assert built == v("beacon")
    quiet = hassmic.parse_beacon(hassmic.member_beacon(v("echo_arb_pk"), N, v("arbitration_mac_key"), 8,
                                                       node="homehub"), "192.168.10.5")
    assert quiet is not None and quiet.flags == hassmic.F_QUIET and quiet.verifies(v("arbitration_mac_key"))


def test_probe_beacon_is_a_loner_beacon():
    raw = hassmic.probe_beacon(v("hub_pk"))
    beacon = hassmic.parse_beacon(raw)
    assert beacon is not None and beacon.net == 0 and beacon.node == "homehub" and beacon.pub == v("hub_pk")
    assert raw == b"HMA1\x01" + v("hub_pk") + bytes(8) + b"\x07homehub"


@pytest.mark.parametrize("raw", [b"", b"HMA1\x02" + bytes(60), b"HMA1\x01" + bytes(40) + b"\x03ABC",
                                 b"HMA1\x01" + bytes(32) + (5).to_bytes(8, "little") + b"\x01a" + bytes(3)])
def test_bad_beacons_are_ignored(raw):
    assert hassmic.parse_beacon(raw) is None


def test_counter_strictly_increases():
    seen = [int(hassmic.next_counter()) for _ in range(50)]
    assert seen == sorted(set(seen)) and len(str(seen[0])) == 16
