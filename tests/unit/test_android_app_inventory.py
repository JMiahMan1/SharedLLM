"""App vetting on the phone: the installed-app list is read natively, only
from a minor's device, and in the server's own vocabulary."""
import re
from pathlib import Path

from services.common.screen_time import PERMISSION_GROUPS

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "services/ui/android/app/src/main/java/com/jarvisos/app"
UI = ROOT / "services/ui/src"


def test_the_plugin_lists_apps_off_the_main_thread():
    plugin = (APP / "PhoneUsagePlugin.java").read_text()
    method = plugin[plugin.index("public void getInstalledApps"):]
    method = method[: method.index("@PluginMethod")]
    assert "new Thread(" in method and "AppInventory.collect(context)" in method
    assert 'ret.put("usageAccess"' in method and "call.reject(" in method


def test_the_collector_reads_what_the_server_expects_and_sends_nothing_itself():
    code = (APP / "AppInventory.java").read_text()
    for field in ("package", "label", "installer", "first_install", "last_update", "launchable", "permissions",
                  "last_used", "size_bytes"):
        assert f'row.put("{field}"' in code, field
    assert "AppInventoryRules.listed(" in code and "AppInventoryRules.groups(" in code
    assert "AppInventoryRules.lastUsed(" in code
    assert "queryAndAggregateUsageStats" in code and "getInstallSourceInfo" in code
    assert "GET_PERMISSIONS" in code
    # It only reads: the web app decides whether to send (a minor's device only).
    assert not re.search(r"HttpURLConnection|OkHttp|https?://", code)


def test_the_permission_groups_are_the_servers():
    rules = (APP / "AppInventoryRules.java").read_text()
    groups = set(re.findall(r'group\("([a-z]+)"', rules))
    assert groups == set(PERMISSION_GROUPS)


def test_the_manifest_allows_seeing_apps_and_their_use():
    manifest = (ROOT / "services/ui/android/app/src/main/AndroidManifest.xml").read_text()
    assert "android.permission.QUERY_ALL_PACKAGES" in manifest
    assert "android.permission.PACKAGE_USAGE_STATS" in manifest


def test_only_a_minors_android_device_reports():
    sync = (UI / "components/layout/AppInventorySync.tsx").read_text()
    assert "user?.is_minor" in sync and "Capacitor.getPlatform() !== 'android'" in sync
    lib = (UI / "lib/appInventorySync.ts").read_text()
    assert "/api/apps/inventory" in lib and "getInstalledApps" in lib
    assert "<AppInventorySync />" in (UI / "App.tsx").read_text()
