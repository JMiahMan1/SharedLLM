"""Device identification helpers (services/control_plane/device_identity.py)."""
from __future__ import annotations

from services.control_plane import device_identity as di


def test_private_mac_detection():
    assert di.is_private_mac("02:00:00:00:00:01") is True
    assert di.is_private_mac("06:00:00:00:00:02") is True
    assert di.is_private_mac("0a:00:00:00:00:03") is True
    assert di.is_private_mac("0e:00:00:00:00:04") is True
    assert di.is_private_mac("00:11:22:33:44:55") is False
    assert di.is_private_mac("11:22:33:44:55:66") is False


def test_oui_lookup_from_fixture_and_missing(tmp_path):
    csv_path = tmp_path / "oui.csv"
    csv_path.write_text(
        "Registry,Assignment,Organization Name,Organization Address\n"
        "MA-L,001122,Apple Inc,\"Cupertino\"\n"
        "MA-L,AABBCC,Samsung Electronics,\"Seoul\"\n",
        encoding="utf-8",
    )
    assert di.load_oui(csv_path) == {"001122": "Apple Inc", "aabbcc": "Samsung Electronics"}
    assert di.oui_organization("00:11:22:33:44:55", csv_path) == "Apple Inc"
    assert di.oui_make("00:11:22:33:44:55", csv_path) == "Apple"
    assert di.oui_organization("02:00:00:00:00:01", csv_path) is None
    assert di.load_oui(tmp_path / "nope.csv") == {}
    assert di.oui_organization("00:11:22:33:44:55", tmp_path / "nope.csv") is None


def test_ua_parser():
    def p(ua):
        return di.parse_user_agent(ua)

    assert p("Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X)")["kind"] == "tablet"
    assert p("Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X)")["make"] == "Apple"

    assert p("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)")["kind"] == "phone"
    assert p("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)")["make"] == "Apple"

    ua_tablet = "Mozilla/5.0 (Linux; Android 16; SM-P620 Build/...) AppleWebKit/537.36"
    r = p(ua_tablet)
    assert r["kind"] == "tablet"
    assert r["make"] == "Samsung"
    assert r["model"] == "SM-P620"
    assert r["os"] == "Android"

    ua_phone = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Mobile Safari/537.36"
    r2 = p(ua_phone)
    assert r2["kind"] == "phone"
    assert r2["os"] == "Android"

    assert p("Mozilla/5.0 (Windows NT 10.0; Win64; x64)").get("os") == "Windows"
    assert p("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)").get("os") == "macOS"
    assert p("Mozilla/5.0 (X11; CrOS x86_64)").get("os") == "ChromeOS"
    assert p("Roku/DVP-9.1").get("make") == "Roku"
    assert p("Mozilla/5.0 (Web0S; Linux/SmartTV)").get("make") == "LG"
    assert p("Mozilla/5.0 (Linux; Android 9; AFTMM Build/...)").get("make") == "Amazon"
    assert p("Mozilla/5.0 (PlayStation 5)").get("make") == "Sony"


def test_dns_inference():
    assert di.infer_from_dns(["apple.com", "icloud.com"])["make"] == "Apple"
    assert di.infer_from_dns(["connectivitycheck.gstatic.com"])["os"] == "Android"
    assert di.infer_from_dns(["msftconnecttest.com"])["os"] == "Windows"
    assert di.infer_from_dns(["roku.com"])["make"] == "Roku"
    assert di.infer_from_dns(["samsungcloudsolution.com"])["make"] == "Samsung"
    assert di.infer_from_dns(["device-metrics-us.amazon.com"])["make"] == "Amazon"
    assert di.infer_from_dns(["nintendo.net"])["make"] == "Nintendo"
    assert di.infer_from_dns(["playstation.net"])["make"] == "PlayStation"
    assert di.infer_from_dns(["xboxlive.com"])["make"] == "Xbox"


def test_hostname_patterns():
    assert di.infer_from_hostname("iPad")["kind"] == "tablet"
    assert di.infer_from_hostname("iPhone")["kind"] == "phone"
    assert di.infer_from_hostname("Galaxy-Tab")["kind"] == "tablet"
    assert di.infer_from_hostname("DESKTOP-ABC")["kind"] == "desktop"
    assert di.infer_from_hostname("MacBook-Pro")["kind"] == "laptop"
    assert di.infer_from_hostname("Chromecast")["kind"] == "streaming"
    assert di.infer_from_hostname("Roku-Device")["kind"] == "streaming"
    assert di.infer_from_hostname("LGwebOSTV")["kind"] == "tv"
    assert di.infer_from_hostname("SamsungTV")["make"] == "Samsung"


MAC_SAFARI = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)


def test_ipad_sending_a_mac_user_agent_is_a_tablet_when_it_has_touch():
    assert di.parse_user_agent(MAC_SAFARI + " [touch]") == {"kind": "tablet", "make": "Apple", "os": "iPadOS"}


def test_a_mac_without_touch_stays_macos():
    assert di.parse_user_agent(MAC_SAFARI + " [no-touch]")["os"] == "macOS"
    assert di.parse_user_agent(MAC_SAFARI)["os"] == "macOS"


def test_dhcp_vendor_classes_name_the_os_family():
    assert di.infer_from_dhcp("android-dhcp-14", "1,3,6") == {"os": "Android"}
    assert di.infer_from_dhcp("MSFT 5.0", "") == {"os": "Windows"}
    assert di.infer_from_dhcp("udhcp 1.36.1", "") == {"os": "Embedded Linux"}


def test_dhcp_option_lists_without_a_vendor_class():
    assert di.infer_from_dhcp("", "1,121,3,6,15,108,114,119,252,95,44,46") == {"make": "Apple"}
    assert di.infer_from_dhcp("", "1,3,6,15,31,33,43,44,46,47,119,121,249,252") == {"os": "Windows"}
    assert di.infer_from_dhcp("", "1,3,6") == {}
    assert di.infer_from_dhcp(None, None) == {}


def test_bonjour_apple_models_tell_an_ipad_from_an_iphone():
    ipad = di.infer_from_mdns({"services": ["_device-info._tcp"], "txt": {"model": "iPad13,4"}})
    assert ipad == {"kind": "tablet", "make": "Apple", "os": "iPadOS", "model": "iPad (iPad13,4)"}
    assert di.infer_from_mdns({"txt": {"model": "iPhone15,2"}})["kind"] == "phone"
    assert di.infer_from_mdns({"txt": {"model": "MacBookPro18,1"}})["kind"] == "laptop"
    assert di.infer_from_mdns({"txt": {"am": "AudioAccessory5,1"}})["kind"] == "speaker"
    assert di.infer_from_mdns({"txt": {"model": "AppleTV14,1"}})["kind"] == "streaming"


def test_bonjour_other_announcements():
    cast = di.infer_from_mdns({"services": ["_googlecast._tcp"], "txt": {"md": "Chromecast", "fn": "Den"}})
    assert cast == {"kind": "streaming", "model": "Chromecast"}
    printer = di.infer_from_mdns({"services": ["_ipp._tcp"], "txt": {"ty": "Example LaserJet 400", "usb_MFG": "Example"}})
    assert printer == {"kind": "printer", "make": "Example", "model": "Example LaserJet 400"}
    assert di.infer_from_mdns({"services": ["_sonos._tcp"]}) == {"kind": "speaker", "make": "Sonos"}
    assert di.infer_from_mdns({"services": ["_companion-link._tcp"], "txt": {}}) == {"make": "Apple"}
    assert di.infer_from_mdns({"services": ["_smb._tcp"]}) == {}
    assert di.infer_from_mdns(None) == {}


def test_the_oui_file_is_read_from_the_network_data_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("OUI_FILE", raising=False)
    monkeypatch.setenv("NETWORK_DATA_DIR", str(tmp_path))
    assert di._oui_path() == tmp_path / "oui.csv"
    monkeypatch.setenv("OUI_FILE", str(tmp_path / "other.csv"))
    assert di._oui_path() == tmp_path / "other.csv"


def test_an_oui_file_added_later_is_read_without_a_restart(tmp_path):
    csv_path = tmp_path / "late.csv"
    assert di.load_oui(csv_path) == {}
    csv_path.write_text("Registry,Assignment,Organization Name,Organization Address\nMA-L,001122,Apple Inc,x\n")
    assert di.load_oui(csv_path) == {"001122": "Apple Inc"}
