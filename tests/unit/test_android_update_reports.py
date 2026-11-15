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
