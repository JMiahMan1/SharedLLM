"""Jarvis updates itself with no tap: Android 12+ only honours
USER_ACTION_NOT_REQUIRED when the app holds UPDATE_PACKAGES_WITHOUT_USER_ACTION.
Without it every update showed Android's install screen and asked for the
phone's password."""
from pathlib import Path

ANDROID = Path(__file__).resolve().parents[2] / "services/ui/android/app/src/main"


def test_the_manifest_asks_to_update_without_user_action():
    manifest = (ANDROID / "AndroidManifest.xml").read_text()
    assert 'android.permission.UPDATE_PACKAGES_WITHOUT_USER_ACTION' in manifest


def test_the_session_asks_for_no_user_action():
    code = (ANDROID / "java/com/jarvisos/app/SelfUpdate.java").read_text()
    assert "USER_ACTION_NOT_REQUIRED" in code
    # Update ownership needs ENFORCE_UPDATE_OWNERSHIP, which Jarvis does not hold.
    assert "setRequestUpdateOwnership" not in code


def test_an_update_never_brings_jarvis_to_the_front():
    """Michele had to use her fingerprint right after a silent update: the
    receiver relaunched Jarvis on screen. Nothing may appear after an update."""
    code = (ANDROID / "java/com/jarvisos/app/BootReceiver.java").read_text()
    assert "startActivity" not in code and "relaunch" not in code.replace("relaunched", "")
