"""A nudge is its own kind of notice on the phone: its own channel with the
nudge sound and a heartbeat vibration, and an open Jarvis is told to show it."""
from pathlib import Path

MAIN = Path(__file__).resolve().parents[2] / "services/ui/android/app/src/main"
APP = MAIN / "java/com/jarvisos/app"


def test_nudges_have_their_own_channel_sound_and_vibration():
    cats = (APP / "PushCategories.java").read_text()
    assert 'NUDGE = "nudge"' in cats and "new Category(NUDGE" in cats and "NUDGE_VIBRATION" in cats
    notifier = (APP / "PushNotifier.java").read_text()
    assert "ch.setSound(nudgeSound(context)" in notifier and "ch.setVibrationPattern(PushCategories.NUDGE_VIBRATION)" in notifier
    assert "R.raw.jarvis_nudge" in notifier and (MAIN / "res/raw/jarvis_nudge.wav").stat().st_size > 10_000


def test_an_open_jarvis_hears_about_the_nudge():
    assert "PushPlugin.nudged(n.title, n.body, n.path)" in (APP / "PushNotifier.java").read_text()
    assert 'notifyListeners("nudge", data)' in (APP / "PushPlugin.java").read_text()
