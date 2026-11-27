"""A hardware button has to be able to reach Jarvis, and to look like Jarvis.

Four things were wrong at once, and each one alone made the trigger look dead:

* the Quick Settings tile carried the full-colour launcher icon, which the shade
  masks to one colour - an unrecognisable white blob;
* tapping the tile, the widget or long-press-home started an invisible
  microphone service and closed, so with no permission or no session nothing at
  all happened;
* the assistant screen opened nothing, because `/?voice=1` is not a page;
* the app declared only `ACTION_ASSIST`, so Android never listed it under
  Settings -> Default digital assistant and a side button could not be assigned.
"""
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "services/ui/android/app/src/main/java/com/jarvisos/app"
MAIN = ROOT / "services/ui/android/app/src/main"
MANIFEST = (MAIN / "AndroidManifest.xml").read_text()
TILE_SERVICE = (APP / "AssistTileService.java").read_text()
ASSIST_ACTIVITY = (APP / "AssistActivity.java").read_text()
SERVICE = (APP / "AssistService.java").read_text()
PLUGIN = (APP / "JarvisAssistPlugin.java").read_text()
MAIN_ACTIVITY = (APP / "MainActivity.java").read_text()
UI = ROOT / "services/ui/src"

ANDROID = "{http://schemas.android.com/apk/res/android}"


def _manifest() -> ET.Element:
    return ET.fromstring(MANIFEST)


def _node(name: str) -> ET.Element:
    for node in _manifest().iter():
        if node.get(ANDROID + "name") == name:
            return node
    raise AssertionError(f"{name} is not declared in AndroidManifest.xml")


def _actions(node: ET.Element) -> set[str]:
    return {a.get(ANDROID + "name") or "" for a in node.iter("action")}


def test_the_tile_has_a_glyph_the_shade_can_draw():
    icon = MAIN / "res/drawable/ic_jarvis_tile.xml"
    assert icon.exists(), "the tile needs a monochrome vector, not the launcher icon"
    tree = ET.parse(icon).getroot()
    assert tree.get(ANDROID + "width") == "24dp"
    sizes = {v.attrib.get(ANDROID + "fillColor") for v in tree.iter("path")}
    # One flat colour, no gradient: the shade masks the whole drawable anyway.
    assert "#FFFFFFFF" in sizes
    assert not list(tree.iter("gradient"))

    tile = _node(".AssistTileService")
    assert tile.get(ANDROID + "icon") == "@drawable/ic_jarvis_tile"
    assert tile.get(ANDROID + "label") == "Ask Jarvis"
    assert "R.drawable.ic_jarvis_tile" in TILE_SERVICE
    assert "getApplicationInfo().icon" not in TILE_SERVICE


def test_every_trigger_opens_the_assistant_screen_instead_of_a_service():
    assert 'ASSIST_PATH = "/?voice=1"' in ASSIST_ACTIVITY
    assert "MainActivity.class" in ASSIST_ACTIVITY
    assert "PushNotifier.EXTRA_OPEN_PATH" in ASSIST_ACTIVITY
    # The old behaviour: an invisible turn, then finish(), which did nothing visible.
    assert "AssistService.turn" not in ASSIST_ACTIVITY


def test_the_listening_notification_opens_the_assistant_too():
    assert 'open.putExtra("open"' not in SERVICE
    assert "AssistActivity.askJarvis(this)" in SERVICE


def test_the_app_can_be_assigned_as_the_phones_voice_input():
    assist = _node(".AssistActivity")
    assert assist.get(ANDROID + "label") == "Ask Jarvis"
    actions = _actions(assist)
    assert "android.intent.action.ASSIST" in actions
    # Bluetooth headsets, car voice buttons, hardware mappers and lock-screen triggers.
    assert "android.intent.action.VOICE_COMMAND" in actions
    assert "android.speech.action.VOICE_SEARCH_HANDS_FREE" in actions

    service = _node(".JarvisVoiceInteractionService")
    assert service.get(ANDROID + "permission") == "android.permission.BIND_VOICE_INTERACTION"
    assert service.get(ANDROID + "exported") == "true"
    assert "android.service.voice.VoiceInteractionService" in _actions(service)
    meta = {m.get(ANDROID + "name"): m.get(ANDROID + "resource") for m in service.iter("meta-data")}
    assert meta.get("android.voice_interaction") == "@xml/voice_interaction_service"

    session = _node(".JarvisVoiceInteractionSessionService")
    assert session.get(ANDROID + "permission") == "android.permission.BIND_VOICE_INTERACTION"

    xml = ET.parse(MAIN / "res/xml/voice_interaction_service.xml").getroot()
    assert xml.get(ANDROID + "sessionService") == "com.jarvisos.app.JarvisVoiceInteractionSessionService"
    assert xml.get(ANDROID + "supportsAssist") == "true"
    # The attribute really is supportsLaunchVoiceAssistFromKeyguard; the shorter
    # name aapt rejects, which is how it was found.
    assert xml.get(ANDROID + "supportsLaunchVoiceAssistFromKeyguard") == "true"
    # No recognitionService: Jarvis opens its own screen rather than hosting a recogniser.
    assert xml.get(ANDROID + "recognitionService") is None


def test_the_session_opens_the_real_assistant_and_leaves():
    session = (APP / "JarvisVoiceInteractionSessionService.java").read_text()
    assert "AssistActivity.askJarvis(getContext())" in session
    assert "onShow(" in session
    assert "hide();" in session


def test_the_role_dialog_really_resolves_and_has_a_way_out():
    assert "@com.getcapacitor.annotation.ActivityCallback" in PLUGIN
    assert "public void roleResult(PluginCall call, androidx.activity.result.ActivityResult result)" in PLUGIN
    # One UI often has no role dialog at all: land on the Digital Assistant picker.
    assert "android.provider.Settings.ACTION_VOICE_INPUT_SETTINGS" in PLUGIN
    assert PLUGIN.count("openAssistantSettings(call);") >= 2


def test_the_tile_is_offered_only_after_signing_in():
    body = MAIN_ACTIVITY[MAIN_ACTIVITY.index("private void offerAssistTile()") :]
    body = body[: body.index("private void offerTimeTile()")]
    key = body.index('p.getString("jarvis_api_key"')
    offered = body.index('p.getBoolean("assist_tile_offered"')
    assert key < offered, "the sign-in check must come before the once-only flag"
    assert 'R.drawable.ic_jarvis_tile' in body
    assert '"Ask Jarvis"' in body


def test_the_web_app_opens_the_sheet_for_a_voice_trigger():
    launch = (UI / "lib/voiceLaunch.ts").read_text()
    assert "export function isVoicePath" in launch
    assert "export function openVoiceAssistant" in launch
    assert "export function onVoiceAssistantRequested" in launch
    # A button pressed on a page with no sheet has to survive the navigation.
    assert "export function takePendingVoiceRequest" in launch

    bridge = (UI / "components/NativeBridge.tsx").read_text()
    assert "isVoicePath(path)" in bridge
    assert "openVoiceAssistant()" in bridge

    dashboard = (UI / "pages/Dashboard.tsx").read_text()
    assert "onVoiceAssistantRequested(open)" in dashboard
    assert "voiceRequestedHere(" in dashboard
    assert "takePendingVoiceRequest()" in dashboard


def test_ask_jarvis_is_reachable_from_every_page():
    header = (UI / "components/layout/Header.tsx").read_text()
    assert 'aria-label="Ask Jarvis"' in header
    assert "openVoiceAssistant()" in header
    # The sheet renders on `/`, so the header sends the person there.
    assert "navigate('/')" in header
