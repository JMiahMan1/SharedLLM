"""The local security scan: sideloads, risky access, the stalkerware list,
and how findings become flags (docs/APP_VETTING.md)."""
import json

import pytest

from services.common import app_security, app_vetting

JARVIS = "com.jarvisos.app"
# A package and a certificate that are on the bundled list (TheTruthSpy).
LISTED_PACKAGE = "com.ispyoo"
LISTED_CERT = "31A6ECECD97CF39BC4126B8745CD94A7C30BF81C"


def _app(**kw):
    base = {"package": "com.example.game", "label": "Example", "installer": "com.android.vending",
            "permissions": [], "capabilities": [], "special_access": [], "cert_sha1": []}
    return {**base, **kw}


def test_the_bundled_list_names_its_source_license_and_commit():
    source = app_security.stalkerware_source()
    assert source["source"] == "https://github.com/AssoEchap/stalkerware-indicators"
    assert source["license"].startswith("CC BY 4.0")
    assert len(source["commit"]) == 40 and source["commit_date"]
    data = json.loads(app_security.STALKERWARE_FILE.read_text())
    assert len(data["apps"]) > 50
    assert all(app["packages"] or app["certificates_sha1"] for app in data["apps"])
    assert JARVIS not in app_security.stalkerware_index()["packages"]


def test_stalkerware_matches_by_exact_package_or_certificate():
    assert app_security.match_stalkerware(LISTED_PACKAGE) == {"name": "TheTruthSpy", "matched": "package"}
    assert app_security.match_stalkerware(LISTED_PACKAGE + ".extra") is None
    assert app_security.match_stalkerware("com.example.innocent", [LISTED_CERT.lower()]) == {
        "name": "TheTruthSpy", "matched": "certificate"}
    colons = ":".join(LISTED_CERT[i:i + 2] for i in range(0, 40, 2))
    assert app_security.match_stalkerware("com.example.innocent", [colons])["matched"] == "certificate"
    assert app_security.match_stalkerware("com.example.innocent", ["00" * 20, "nonsense"]) is None


def test_a_stalkerware_match_is_a_severe_security_flag():
    app = _app(package=LISTED_PACKAGE)
    findings = app_security.scan(app)
    assert findings[0]["check"] == "stalkerware" and findings[0]["severity"] == "severe"
    assert "TheTruthSpy" in findings[0]["text"]
    flags = app_security.flags(app, findings)
    assert flags == [{"reason": "stalkerware", "category": "security", "level": "severe", "text": findings[0]["text"]}]


def test_where_an_app_came_from():
    assert app_security.sideload_finding(_app()) is None
    unknown = app_security.sideload_finding(_app(installer=None))
    assert unknown["key"] == "unknown_installer" and "does not say where" in unknown["text"]
    other = app_security.sideload_finding(_app(installer="com.amazon.venezia"))
    assert other["key"] == "other_store" and other["text"] == "Installed from Amazon Appstore, not Google Play"
    by_app = app_security.sideload_finding(_app(installer="com.example.downloader"))
    assert by_app["key"] == "other_installer" and "com.example.downloader" in by_app["text"]
    # An app asked for, not installed, says nothing about where it came from.
    assert app_security.sideload_finding({"package": "com.example.game"}) is None


def test_each_risky_access_is_a_plain_sentence():
    app = _app(capabilities=list(app_security.CAPABILITIES), special_access=["accessibility"],
               permissions=["camera", "microphone", "location"])
    found = {f["key"]: f for f in app_security.access_findings(app)}
    assert set(found) == set(app_security.CAPABILITIES) | {"camera_microphone_location"}
    assert found["accessibility"]["active"] and found["accessibility"]["severity"] == "high"
    assert found["accessibility"]["text"].endswith("and it is switched on")
    assert found["device_admin"]["severity"] == "medium" and not found["device_admin"]["active"]
    assert found["sms_read"]["severity"] == "high" and found["overlay"]["severity"] == "medium"
    for finding in found.values():
        assert finding["text"] and "\u2014" not in finding["text"] and "\u2013" not in finding["text"]


def test_a_play_app_with_risky_access_is_shown_but_not_flagged():
    app = _app(special_access=["notification_listener"], capabilities=["notification_listener"])
    findings = app_security.scan(app)
    assert [f["check"] for f in findings] == ["risky_access"]
    assert app_security.flags(app, findings) == []


def test_a_sideloaded_app_with_switched_on_access_is_severe():
    app = _app(installer=None, capabilities=["accessibility", "sms_read"], special_access=["accessibility"])
    flags = app_security.flags(app, app_security.scan(app))
    assert len(flags) == 1
    assert flags[0]["reason"] == "sideloaded_risky" and flags[0]["level"] == "severe"
    assert flags[0]["text"] == "Not from Google Play, and it has accessibility access (switched on), reading text messages"


def test_a_sideloaded_app_that_only_asks_for_risky_access_is_high():
    app = _app(installer="com.sec.android.app.samsungapps", capabilities=["install_apps"])
    flags = app_security.flags(app, app_security.scan(app))
    assert flags[0]["level"] == "high" and flags[0]["text"].startswith("Installed from Galaxy Store, and it has")


def test_a_malwarebazaar_hit_is_severe_and_other_states_are_not_findings():
    app = _app()
    hit = {"state": "malware", "signature": "ExampleFamily"}
    findings = app_security.scan(app, hit)
    assert findings[0]["check"] == "malware" and "ExampleFamily" in findings[0]["text"]
    assert app_security.flags(app, findings)[0]["reason"] == "malware"
    for state in ("not_known", "not_set_up", "error", "checking", "no_hash"):
        assert app_security.scan(app, {"state": state}) == []


def test_jarvis_is_never_scanned():
    jarvis = _app(package=JARVIS, installer=None, special_access=["device_admin", "accessibility"],
                  capabilities=["device_admin", "accessibility"])
    assert app_security.scan(jarvis, {"state": "malware"}, own_package=JARVIS) == []
    verdict = app_vetting.assess(jarvis, None, app_vetting.effective_policy({}, None, _today()), JARVIS)
    assert verdict["security"] == [] and verdict["level"] is None and verdict["malware_check"] is None


def test_the_inventory_keeps_the_scans_inputs_bounded():
    raw = {"package": "com.example.game", "label": "Example", "installer": None, "permissions": ["camera"],
           "capabilities": ["accessibility", "sms_read", "made_up"], "special_access": ["accessibility", "root"],
           "cert_sha1": [LISTED_CERT.lower(), "zz", *["AB" * 20] * 3, "CD" * 20, "EF" * 20, "01" * 20],
           "apk_sha256": "AB" * 32, "apk_hash_state": "ok"}
    app = app_vetting.clean_inventory_app(raw)
    assert app["capabilities"] == ["accessibility", "sms_read"]
    assert app["special_access"] == ["accessibility"]
    assert app["cert_sha1"] == sorted({LISTED_CERT, "AB" * 20, "CD" * 20, "EF" * 20, "01" * 20})[:app_vetting.MAX_CERTS]
    assert app["apk_sha256"] == "ab" * 32 and app["apk_hash_state"] == "ok"
    skipped = app_vetting.clean_inventory_app({**raw, "apk_sha256": None, "apk_hash_state": "too_large"})
    assert skipped["apk_sha256"] is None and skipped["apk_hash_state"] == "too_large"
    bad = app_vetting.clean_inventory_app({**raw, "apk_sha256": "nope", "apk_hash_state": "whatever"})
    assert bad["apk_sha256"] is None and bad["apk_hash_state"] is None
    old = app_vetting.clean_inventory_app({"package": "com.example.game", "label": "Old APK"})
    assert old["capabilities"] == [] and old["apk_sha256"] is None and old["apk_hash_state"] is None


def test_devices_merge_their_findings_and_hashes():
    phone = app_vetting.clean_inventory_app({"package": "com.example.game", "special_access": ["accessibility"],
                                             "apk_sha256": "aa" * 32})
    tablet = app_vetting.clean_inventory_app({"package": "com.example.game", "capabilities": ["vpn"],
                                              "apk_sha256": "bb" * 32})
    merged = app_vetting.merge_inventories([{"device": "p", "apps": [phone]}, {"device": "t", "apps": [tablet]}])[0]
    assert merged["special_access"] == ["accessibility"] and merged["capabilities"] == ["vpn"]
    assert merged["apk_hashes"] == ["aa" * 32, "bb" * 32] and merged["apk_hash_state"] == "ok"


@pytest.mark.parametrize("words", [s["text"] for s in [
    {"text": app_security.RISKY[k][3]} for k in app_security.RISKY]])
def test_sentences_carry_no_dashes(words):
    assert "\u2014" not in words and "\u2013" not in words


def _today():
    from datetime import date

    return date(2026, 11, 16)
