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
    notifier = (APP / "PushNotifier.java").read_text()
    # A real nudge plays its sound and tells an open Jarvis, with its kind...
    nudge_block = notifier.split(
        "if (PushCategories.NUDGE.equals(n.category) && !PushCategories.PRIVATE_REMINDER.equals(n.kind)) {", 1
    )
    assert len(nudge_block) == 2, "the nudge branch must exclude private reminders"
    branch = nudge_block[1].split("}", 1)[0]
    assert "b.setSound(nudgeSound(context)).setVibrate(PushCategories.NUDGE_VIBRATION)" in branch
    assert "PushPlugin.nudged(n.title, n.body, n.path, n.kind);" in branch
    # ...and that branch is the only place the in-app nudge is raised, so a
    # private reminder (posted on the nudge channel) never triggers it.
    assert notifier.count("PushPlugin.nudged(") == 1
    assert 'PRIVATE_REMINDER = "private_reminder"' in (APP / "PushCategories.java").read_text()
    plugin = (APP / "PushPlugin.java").read_text()
    assert "static void nudged(String title, String body, String path, String kind)" in plugin
    assert 'notifyListeners("nudge", data)' in plugin
