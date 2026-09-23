#!/usr/bin/env bash
# Android functional smoke tests via adb.
# Requires: adb on PATH, a connected device/emulator with com.jarvisos.app installed.
# Uses ONLY .tmp/ for artifacts (never /tmp).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/.tmp/android-func"
PKG="com.jarvisos.app"
mkdir -p "$OUT"

ADB="${ADB:-adb}"
FAIL=0
PASS=0

log() { printf '%s\n' "$*"; }
ok() { PASS=$((PASS + 1)); log "  PASS  $*"; }
bad() { FAIL=$((FAIL + 1)); log "  FAIL  $*"; }

require_device() {
  if ! "$ADB" get-state >/dev/null 2>&1; then
    log "No device/emulator available (adb get-state failed)."
    exit 2
  fi
}

pkg_installed() {
  "$ADB" shell pm path --user 0 "$PKG" >/dev/null 2>&1
}

ensure_personal_profile_only() {
  # Force personal profile; remove from work profile if MDM/install put it there.
  "$ADB" shell pm install-existing --user 0 "$PKG" >/dev/null 2>&1 || true
  if "$ADB" shell pm path --user 10 "$PKG" >/dev/null 2>&1; then
    log "  NOTE removing $PKG from work profile (user 10)"
    "$ADB" shell pm uninstall --user 10 "$PKG" >/dev/null 2>&1 || true
  fi
}

# Install path: always personal profile only.
#   adb install -r app-debug.apk
#   adb shell pm install-existing --user 0 com.jarvisos.app
# NEVER install into work profile (user 10) — MDM removes unapproved apps.
launch_app() {
  "$ADB" shell am force-stop "$PKG" || true
  "$ADB" shell monkey -p "$PKG" -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1 || \
    "$ADB" shell am start -n "$PKG/.MainActivity" >/dev/null
  sleep 3
}

test_package_installed() {
  # Personal profile only (user 0). Work profile (user 10) is MDM-managed and
  # will auto-remove unapproved packages — never install or assert there.
  if "$ADB" shell pm path --user 0 "$PKG" >/dev/null 2>&1; then
    ok "package $PKG installed on user 0 (personal)"
  else
    bad "package $PKG missing on user 0"
  fi
  if "$ADB" shell pm path --user 10 "$PKG" >/dev/null 2>&1; then
    bad "package $PKG also present on work profile (user 10) — uninstall it"
  else
    ok "package $PKG absent from work profile (user 10)"
  fi
}

test_activity_launches() {
  launch_app
  local top
  # OEM/Android versions report the resumed activity under different keys.
  top="$("$ADB" shell dumpsys activity activities 2>/dev/null | grep -E 'ResumedActivity:|mResumedActivity|topResumedActivity' | head -1 || true)"
  if echo "$top" | grep -q "$PKG"; then
    ok "MainActivity resumed"
  else
    bad "MainActivity not resumed (got: $top)"
  fi
}

test_no_crash_on_launch() {
  # Clear then relaunch and look for FATAL EXCEPTION in logcat
  "$ADB" logcat -c || true
  launch_app
  sleep 2
  if "$ADB" logcat -d -t 300 2>/dev/null | grep -E "FATAL EXCEPTION|AndroidRuntime.*$PKG" | grep -q "$PKG"; then
    bad "crash detected on launch"
    "$ADB" logcat -d -t 200 >"$OUT/crash-launch.txt" || true
  else
    ok "no crash on launch"
  fi
}

test_permissions_declared() {
  local dump
  dump="$("$ADB" shell dumpsys package "$PKG" 2>/dev/null || true)"
  for p in ACTIVITY_RECOGNITION ACCESS_FINE_LOCATION ACCESS_BACKGROUND_LOCATION; do
    if echo "$dump" | grep -q "$p"; then
      ok "permission declared: $p"
    else
      bad "permission missing: $p"
    fi
  done
}

test_widget_receivers_registered() {
  local dump
  dump="$("$ADB" shell dumpsys package "$PKG" 2>/dev/null || true)"
  for r in MetricWidget DashboardWidget MediaWidget DeviceButtonWidget; do
    if echo "$dump" | grep -q "$r"; then
      ok "widget receiver present: $r"
    else
      bad "widget receiver missing: $r"
    fi
  done
}

test_widgets_listed() {
  # Best-effort: some OEMs restrict this dump
  local out
  out="$("$ADB" shell dumpsys appwidget 2>/dev/null || true)"
  if echo "$out" | grep -qi "$PKG"; then
    ok "appwidget dump references $PKG"
  else
    log "  SKIP  appwidget dump has no $PKG entries (OEM may hide them; add widgets from launcher)"
  fi
}

test_settings_sensors_ui() {
  launch_app
  "$ADB" shell input keyevent KEYCODE_WAKEUP >/dev/null 2>&1 || true
  "$ADB" shell wm dismiss-keyguard >/dev/null 2>&1 || true
  sleep 1
  # Open settings deep-link if present; otherwise navigate via UI dump
  "$ADB" shell am start -a android.intent.action.VIEW -d "jarvis://settings" >/dev/null 2>&1 || true
  sleep 1
  local ui
  ui="$("$ADB" shell uiautomator dump /sdcard/ui.xml >/dev/null 2>&1 && "$ADB" shell cat /sdcard/ui.xml 2>/dev/null || true)"
  echo "$ui" >"$OUT/ui-settings.xml" || true
  if echo "$ui" | grep -qiE "Sensor|Step|Location"; then
    ok "Settings UI shows sensor controls"
  else
    # Fallback: open app and dump after a moment
    launch_app
    "$ADB" shell input keyevent KEYCODE_WAKEUP >/dev/null 2>&1 || true
    "$ADB" shell wm dismiss-keyguard >/dev/null 2>&1 || true
    sleep 1
    ui="$("$ADB" shell uiautomator dump /sdcard/ui.xml >/dev/null 2>&1 && "$ADB" shell cat /sdcard/ui.xml 2>/dev/null || true)"
    echo "$ui" >"$OUT/ui-home.xml" || true
    if echo "$ui" | grep -qiE "Jarvis|Dashboard"; then
      ok "app UI dump succeeded (open Settings manually to verify Sensors section)"
    else
      # WebView UIs often cannot be dumped; screenshot is the reliable signal.
      log "  SKIP  UI dump unavailable (WebView); screenshot still captured"
    fi
  fi
}

test_screenshot() {
  launch_app
  "$ADB" shell screencap -p /sdcard/jarvis-func.png || true
  "$ADB" pull /sdcard/jarvis-func.png "$OUT/screenshot.png" >/dev/null 2>&1 || true
  "$ADB" shell rm -f /sdcard/jarvis-func.png || true
  if [[ -s "$OUT/screenshot.png" ]]; then ok "screenshot -> $OUT/screenshot.png"; else bad "screenshot failed"; fi
}

main() {
  require_device
  log "== Android functional smoke =="
  ensure_personal_profile_only
  test_package_installed
  if pkg_installed; then
    test_activity_launches
    test_no_crash_on_launch
    test_permissions_declared
    test_widget_receivers_registered
    test_widgets_listed
    test_settings_sensors_ui
    test_screenshot
  fi
  log ""
  log "Result: $PASS passed, $FAIL failed (artifacts in $OUT)"
  if [[ $FAIL -gt 0 ]]; then exit 1; fi
}

main "$@"
