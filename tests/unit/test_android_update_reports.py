"""Every way Jarvis installs an update reports what Android answered."""
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "services/ui/android/app/src/main/java/com/jarvisos/app"


def test_session_installs_and_androids_answer_are_reported():
    code = (APP / "SelfUpdate.java").read_text()
    assert 'UpdateReport.send(context, "committed"' in code
    assert 'UpdateReport.send(context, "session_failed"' in code
    assert 'UpdateReport.send(context, "status"' in code


def test_the_fallback_hand_off_is_reported_and_paths_are_named():
    assert 'UpdateReport.send(context, "intent_fallback"' in (APP / "ApkInstallPlugin.java").read_text()
    assert 'SelfUpdate.install(context, apk, "background")' in (APP / "NativeUpdater.java").read_text()
    assert "getInstallSourceInfo" in (APP / "UpdateReport.java").read_text()


def test_a_background_update_never_puts_androids_screen_up():
    """Owner: nothing should come up; anything that needs an OK waits in Settings."""
    code = (APP / "SelfUpdate.java").read_text()
    pending = code[code.index("STATUS_PENDING_USER_ACTION) {"):]
    head = pending[: pending.index("startActivity")]
    assert '!"button".equals(path)' in head and "KEY_NEEDS_OK" in head and "return;" in head
    assert "SelfUpdate.needsOk(context) == code" in (APP / "NativeUpdater.java").read_text()
    assert "updateNeedsOk" in (APP / "ApkInstallPlugin.java").read_text()
