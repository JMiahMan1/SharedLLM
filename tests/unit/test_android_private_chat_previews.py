"""Message text in private-chat notifications (PrivatePreviewPlugin, PushNotifier).

Source checks, since the APK is built in CI: the preview key is usable without
the phone's lock (the push service opens it in the background), the plugin is
registered, a private-chat notification is VISIBILITY_PRIVATE with a public
version that names nothing, and any failure keeps the content-free notice.
"""
import re
from pathlib import Path

JAVA = Path(__file__).resolve().parents[2] / "services/ui/android/app/src/main/java/com/jarvisos/app"


def test_the_plugin_is_registered_before_the_bridge_starts():
    main = (JAVA / "MainActivity.java").read_text()
    assert main.index("registerPlugin(PrivatePreviewPlugin.class);") < main.index("super.onCreate(savedInstanceState);")
    assert '@CapacitorPlugin(name = "JarvisPrivatePreview")' in (JAVA / "PrivatePreviewPlugin.java").read_text()


def test_the_preview_key_does_not_need_the_phone_lock_and_the_plain_key_is_never_stored():
    code = (JAVA / "PrivatePreviewPlugin.java").read_text()
    assert ".setUserAuthenticationRequired(false)" in code
    assert "setUserAuthenticationRequired(true)" not in code
    assert '"AndroidKeyStore"' in code and ".setKeySize(256)" in code
    puts = re.findall(r"\.putString\(([^;]+)\);", code)
    assert puts and all("plain" not in p for p in puts)
    assert "Arrays.fill(plain, (byte) 0)" in code and "Arrays.fill(key, (byte) 0)" in code
    # Disabling deletes both the Keystore key and the stored ciphertext.
    forget = code[code.index("static void forget("):]
    assert ".remove(token)" in forget and "deleteEntry" in forget


def test_a_private_notification_is_private_on_the_lock_screen():
    code = (JAVA / "PushNotifier.java").read_text()
    block = code[code.index("if (n.privateChat) {"):]
    block = block[: block.index("if (quiet)")]
    assert "setVisibility(NotificationCompat.VISIBILITY_PRIVATE)" in block
    assert ".setPublicVersion(" in block
    assert '"New private message"' in block


def test_any_failure_keeps_the_content_free_notice():
    code = (JAVA / "PushNotifier.java").read_text()
    assert "text == null ? n : n.withPreview(text)" in code
    assert "PrivatePreviewPlugin.enabled(context, token)" in code
    preview = (JAVA / "PrivatePreviewPlugin.java").read_text()
    body = preview[preview.index("static String preview("):preview.index("private static byte[] dataKey(")]
    assert "catch (Exception e)" in body and "return null;" in body


def test_the_notice_carries_the_private_flag_and_message_id_only():
    code = (JAVA / "PushNotice.java").read_text()
    assert 'data.optBoolean("private", false)' in code
    assert 'data.optLong("message_id", 0)' in code
