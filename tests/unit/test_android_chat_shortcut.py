"""A chat conversation can be put on the Android home screen: a pinned
shortcut with the other person's name and picture that opens the chat the
same way a notification tap does, and never carries a message or a key."""
from pathlib import Path

MAIN = Path(__file__).resolve().parents[2] / "services/ui/android/app/src/main"
APP = MAIN / "java/com/jarvisos/app"
PLUGIN = (APP / "ChatShortcutPlugin.java").read_text()


def test_the_plugin_is_registered_before_the_bridge_starts():
    main = (APP / "MainActivity.java").read_text()
    register = main.index("registerPlugin(ChatShortcutPlugin.class);")
    assert register < main.index("super.onCreate(savedInstanceState);")
    assert '@CapacitorPlugin(name = "JarvisChatShortcut")' in PLUGIN


def test_a_shortcut_opens_the_chat_like_a_notification_tap():
    assert ".putExtra(PushNotifier.EXTRA_OPEN_PATH, path)" in PLUGIN
    assert "new Intent(ctx, MainActivity.class)" in PLUGIN and ".setAction(Intent.ACTION_VIEW)" in PLUGIN
    assert "Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP" in PLUGIN
    # MainActivity hands that extra to the web app (the private chat's page is its lock screen).
    assert "PushNotifier.EXTRA_OPEN_PATH" in (APP / "MainActivity.java").read_text()


def test_only_chat_pages_and_chat_ids_are_accepted():
    assert 'ID_PREFIX = "chat:"' in PLUGIN and 'PATH_PREFIX = "/family?chat="' in PLUGIN
    assert "!validId(id) || !validPath(path)" in PLUGIN


def test_it_carries_a_name_and_a_picture_only():
    # The plugin reads exactly these fields; nothing like message text exists to pass on.
    fields = sorted({part.split('"')[1] for part in PLUGIN.split('call.getString(')[1:]})
    assert fields == ["color", "icon", "id", "label", "message", "monogram", "path"]
    # "message" is the disabled shortcut's notice, read only in disable().
    disable = PLUGIN[PLUGIN.index("public void disable"):]
    assert 'call.getString("message"' in disable and 'call.getString("message"' not in PLUGIN[:PLUGIN.index("public void disable")]


def test_pinned_and_long_lived_with_one_id_per_conversation():
    assert "ShortcutManagerCompat.requestPinShortcut(ctx, info, null)" in PLUGIN
    assert "ShortcutManagerCompat.isRequestPinShortcutSupported" in PLUGIN
    assert "ShortcutManagerCompat.pushDynamicShortcut(ctx, info)" in PLUGIN
    assert ".setLongLived(true)" in PLUGIN and ".setPerson(person)" in PLUGIN and ".setIsConversation()" in PLUGIN
    assert "ShortcutManagerCompat.updateShortcuts" in PLUGIN  # adding again updates
    assert "getMaxShortcutCountPerActivity" in PLUGIN and "removeDynamicShortcuts" in PLUGIN
    assert "IconCompat.createWithAdaptiveBitmap" in PLUGIN


def test_a_gone_conversation_greys_out_its_shortcut():
    assert "ShortcutManagerCompat.disableShortcuts(getContext(), ids, message)" in PLUGIN
    assert "ShortcutManagerCompat.removeLongLivedShortcuts(getContext(), ids)" in PLUGIN


def test_no_play_services():
    assert "com.google.android.gms" not in PLUGIN
