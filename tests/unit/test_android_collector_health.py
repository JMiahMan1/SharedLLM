"""The phone keeps collecting after a sign-out and sign-in, a reboot or an
update, and tells the parents when it cannot (services/telemetry/collector_health.py).

The Android app cannot be built in the Python suite, so these read the source:
every place that can see the app is signed in re-arms the collecting workers,
the notification listener drives collection, and the health report carries
only states.
"""
import re
from pathlib import Path

from services.telemetry import collector_health

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "services/ui/android/app/src/main/java/com/jarvisos/app"


def _read(name: str) -> str:
    return (APP / name).read_text()


def _method(code: str, signature: str) -> str:
    body = code[code.index(signature):]
    depth = 0
    for i, ch in enumerate(body):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return body[: i + 1]
    raise AssertionError(f"no body for {signature}")


def test_one_method_schedules_all_three_workers_only_when_signed_in():
    method = _method(_read("BackgroundWork.java"), "static boolean ensureScheduled(")
    assert "WidgetApi.apiKey(app)" in method and "return false" in method
    for worker in ("UsageSyncWorker", "CommsSyncWorker", "PhotoScanWorker"):
        assert f"{worker}.schedule(app)" in method, worker
        # KEEP: scheduling again is a no-op, so the re-arm is safe to call often.
        assert "ExistingPeriodicWorkPolicy.KEEP" in _read(f"{worker}.java")


def test_every_place_that_sees_a_signed_in_app_rearms_them():
    main = _method(_read("MainActivity.java"), "public void onCreate(")
    assert "BackgroundWork.ensureScheduled(this)" in main
    assert not re.search(r"(UsageSyncWorker|CommsSyncWorker|PhotoScanWorker)\.schedule\(", main)

    token = _method(_read("TokenBridgePlugin.java"), "public void setCredentials(")
    assert "BackgroundWork.ensureScheduled(getContext())" in token

    boot = _read("BootReceiver.java")
    assert "ACTION_MY_PACKAGE_REPLACED" in boot and "ACTION_BOOT_COMPLETED" in boot
    assert "BackgroundWork.ensureScheduled(app)" in boot

    update = _method(_read("AppUpdateWorker.java"), "public Result doWork(")
    assert update.index("BackgroundWork.ensureScheduled(") < update.index("NativeUpdater.maybeCheck(")


def test_a_real_sign_out_still_cancels_them():
    stop = _method(_read("TokenBridgePlugin.java"), "private static void stopBackgroundServices(")
    for worker in ("UsageSyncWorker", "CommsSyncWorker", "PhotoScanWorker"):
        assert f"{worker}.cancel(context)" in stop


def test_the_notification_listener_drives_collection():
    listener = _read("MediaSessionListenerService.java")
    connected = _method(listener, "public void onListenerConnected(")
    assert "BackgroundWork.ensureScheduled(this)" in connected
    posted = _method(listener, "public void onNotificationPosted(")
    assert "BackgroundWork.ensureScheduledHourly(this)" in posted
    assert "isCommsSource(pkg)" in posted and "BackgroundWork.collectCommsSoon(this)" in posted
    source = _method(listener, "private boolean isCommsSource(")
    assert "getDefaultSmsPackage" in source and "getDefaultDialerPackage" in source
    assert "ChatMessageLog.MESSAGING_APPS" in source

    work = _read("BackgroundWork.java")
    soon = _method(work, "static void collectCommsSoon(")
    # Off the main thread, gated, and always released.
    assert "LISTENER_RUN.tryStart(" in soon and "new Thread(" in soon and "LISTENER_RUN.finish()" in soon
    assert 'CollectorStatus.record(app, "listener"' in soon
    rules = _read("CollectRules.java")
    assert "LISTENER_RUN_GAP_MS = 30_000L" in rules and "REARM_GAP_MS = 60L * 60 * 1000" in rules


def test_the_collectors_record_how_each_run_went():
    comms = _method(_read("CommsCollector.java"), "static synchronized JSONObject run(")
    for outcome in ("signed_out", "no_permission", "upload_failed"):
        assert f'"{outcome}"' in comms
    assert 'CollectorStatus.record(app, "comms"' in comms
    usage = _read("UsageSyncWorker.java")
    assert usage.count('CollectorStatus.record(app, "usage"') == 4


def test_the_health_report_matches_what_the_server_keeps():
    health = _read("DeviceHealth.java")
    assert 'PATH = "/api/telemetry/device_health"' in health
    for name in collector_health.PERMISSIONS:
        assert f'perms.put("{name}"' in health, name
    for name in collector_health.WORKERS:
        assert f'workers.put("{name}"' in health, name
    names = re.search(r"NAMES = \{([^}]*)\}", _read("CollectorStatus.java")).group(1)
    assert set(re.findall(r'"([a-z]+)"', names)) == set(collector_health.COLLECTORS)
    for worker in ("CommsSyncWorker.java", "UsageSyncWorker.java"):
        assert "DeviceHealth.reportIfDue(" in _read(worker)


def test_the_health_report_never_reads_message_content():
    for name in ("DeviceHealth.java", "CollectorStatus.java"):
        code = _read(name)
        assert not re.search(r"content://|getContentResolver\(\)\.query|EXTRA_TEXT|\"body\"", code), name
