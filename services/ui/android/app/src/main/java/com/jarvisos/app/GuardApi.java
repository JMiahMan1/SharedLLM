package com.jarvisos.app;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Iterator;
import java.util.Map;
import java.util.Set;

/**
 * The screen guard's calls to the Jarvis server, made as the phone's signed-in
 * member (the token TokenBridge mirrors from the web app), and the last answer
 * kept so the guard still knows the rules when the phone is offline.
 */
final class GuardApi {

    static final String KEY_STATUS = "guard_status";
    static final String KEY_UNLOCKED_UNTIL = "guard_unlocked_until";
    /** The parents' offline-code verifiers, from the last online parent unlock. */
    static final String KEY_OFFLINE_CODES = "guard_offline_codes";
    static final String KEY_OFFLINE_FAILS = "guard_offline_fails";
    static final String KEY_OFFLINE_REPORT_DUE = "guard_offline_report_due";
    /** The user apps on the phone when the guard first ran: never held for approval. */
    static final String KEY_INSTALL_BASELINE = "guard_install_baseline";
    /** Tamper reports not yet taken by the server (JSON list of {event, detail}). */
    static final String KEY_TAMPER_PENDING = "guard_tamper_pending";
    static final String KEY_BYPASS_REPORTED = "guard_bypass_reported";
    static final String KEY_FILTER_PROMPT_AT = "guard_filter_prompt_at";
    static final String KEY_GUARD_BOOT = "guard_boot_count";
    static final String KEY_SUSPENDED_PACKAGES = "guard_suspended_packages";
    static final int TAMPER_QUEUE_MAX = 30;
    private static final Object TAMPER_LOCK = new Object();
    private static final java.util.concurrent.atomic.AtomicBoolean FLUSHING = new java.util.concurrent.atomic.AtomicBoolean(false);
    static final int OFFLINE_MAX_FAILS = 5;
    static final long OFFLINE_WAIT_MS = 10 * 60_000L;

    private GuardApi() {}

    static SharedPreferences prefs(Context context) {
        return TokenBridgePlugin.prefs(context);
    }

    /** Fetch the live screen-time status and remember it; false when it could not. */
    static boolean refreshStatus(Context context) {
        SharedPreferences p = prefs(context);
        String user = p.getString(TokenBridgePlugin.KEY_USERNAME, null);
        if (user == null || user.isEmpty()) return false;
        try {
            String body = request(context, "GET", statusPath(p, user), null);
            if (body == null) return false;
            new JSONObject(body); // only remember an answer that parses
            p.edit().putString(KEY_STATUS, body).apply();
            showQueuedNotices(context);
            return true;
        } catch (Exception e) {
            return false;
        }
    }

    /**
     * The status path, naming this phone when it knows its own key so the server
     * can answer with the notices queued for it alone (MDM).
     *
     * Without a key the answer carries no notices at all: a notice aimed at one
     * phone must never be announced on another, and "not device-scoped" must not
     * read as "nothing waiting".
     */
    private static String statusPath(SharedPreferences p, String user) throws Exception {
        String path = "/api/screen-time/" + URLEncoder.encode(user, "UTF-8") + "/status";
        String key = p.getString(TokenBridgePlugin.KEY_DEVICE_KEY, null);
        if (key != null && !key.isEmpty()) {
            path += "?device_key=" + URLEncoder.encode(key, "UTF-8");
        }
        return path;
    }

    static JSONObject lastStatus(Context context) {
        try {
            return new JSONObject(prefs(context).getString(KEY_STATUS, "{}"));
        } catch (Exception e) {
            return new JSONObject();
        }
    }

    /** The device-scoped notices this phone has shown, so a re-poll does not repeat them. */
    static final String KEY_NOTICES_SHOWN = "guard_notices_shown";
    private static final int SHOWN_MAX = 50;

    /**
     * Show the notices the server queued for this one phone (MDM), then tell the
     * server they were shown so they stop coming.
     *
     * The ids are remembered here because the acknowledgement can fail: a server
     * that is briefly unreachable must not turn one queued notice into a
     * notification on every check-in. A notice whose acknowledgement never
     * arrives is shown once and left in the server's queue (the Devices page
     * still says it is waiting) until it expires.
     */
    private static void showQueuedNotices(Context context) {
        JSONArray notices = lastStatus(context).optJSONArray("notices");
        if (notices == null || notices.length() == 0) return;
        SharedPreferences p = prefs(context);
        Set<String> shown = new HashSet<>(p.getStringSet(KEY_NOTICES_SHOWN, new HashSet<>()));
        JSONArray fresh = new JSONArray();
        for (int i = 0; i < notices.length(); i++) {
            JSONObject n = notices.optJSONObject(i);
            if (n == null) continue;
            String id = n.optString("id", "");
            if (id.isEmpty() || shown.contains(id)) continue;
            GuardNotices.queued(context, n.optString("title", "Jarvis"), n.optString("body", ""),
                    n.optString("url", ""), n.optString("tag", ""));
            fresh.put(id);
        }
        if (fresh.length() == 0) return;
        if (!ackNotices(context, fresh)) return; // tried again on the next check-in
        for (int i = 0; i < fresh.length(); i++) shown.add(fresh.optString(i));
        if (shown.size() > SHOWN_MAX) shown.clear(); // the queue is the truth; this only stops a repeat
        p.edit().putStringSet(KEY_NOTICES_SHOWN, shown).apply();
    }

    /** Tell the server which queued notices this phone has shown. */
    private static boolean ackNotices(Context context, JSONArray ids) {
        String key = prefs(context).getString(TokenBridgePlugin.KEY_DEVICE_KEY, null);
        if (key == null || key.isEmpty()) return false;
        try {
            JSONObject body = new JSONObject().put("device_key", key).put("ids", ids);
            return request(context, "POST", "/api/mdm/devices/notices/ack", body.toString()) != null;
        } catch (Exception e) {
            return false;
        }
    }

    static boolean isProtected(Context context) {
        return lastStatus(context).optBoolean("protected", false);
    }

    /**
     * Whether the server's status has ever been read: it carries whether this is
     * a minor's phone. An answer, not a default, so the device-owner enforcement
     * (DeviceOwnerRules) never lifts protection on a phone that has not been told
     * anything yet.
     */
    static boolean statusKnown(Context context) {
        return lastStatus(context).has("protected");
    }

    /**
     * Locked now: the server said so and the lock has not run out since, or
     * bedtime or school time has started on the clock since the last status.
     */
    static boolean isLocked(Context context) {
        JSONObject s = lastStatus(context);
        if (!s.optBoolean("locked", false)) {
            return LockWarning.lockedByClock(System.currentTimeMillis(), nextLockAt(context), nextLockReason(context));
        }
        long until = epochMs(s.optString("until", ""));
        return until == 0L || System.currentTimeMillis() < until;
    }

    static String lockReason(Context context) {
        JSONObject s = lastStatus(context);
        if (!s.optBoolean("locked", false)
                && LockWarning.lockedByClock(System.currentTimeMillis(), nextLockAt(context), nextLockReason(context))) {
            return nextLockReason(context);
        }
        return s.optString("reason", "");
    }

    /** When the phone next locks on its own (epoch ms), or 0 when nothing is coming. */
    static long nextLockAt(Context context) {
        return epochMs(lastStatus(context).optString("next_lock_at", ""));
    }

    static String nextLockReason(Context context) {
        String reason = lastStatus(context).optString("next_lock_reason", "");
        return "null".equals(reason) ? "" : reason;
    }

    /** The household day the status was for (YYYY-MM-DD). */
    static String statusDay(Context context) {
        return lastStatus(context).optString("day", "");
    }

    /** Minutes left today for each app with its own limit. */
    static Map<String, Integer> appMinutesLeft(Context context) {
        Map<String, Integer> out = new HashMap<>();
        JSONObject left = lastStatus(context).optJSONObject("app_minutes_left");
        if (left == null) return out;
        for (Iterator<String> it = left.keys(); it.hasNext(); ) {
            String pkg = it.next();
            out.put(pkg, left.optInt(pkg));
        }
        return out;
    }

    /** New apps wait for a parent's OK (the server's word; off until it has spoken). */
    static boolean installApprovalRequired(Context context) {
        return lastStatus(context).optBoolean("install_approval", false);
    }

    /**
     * Whether a parent queued a remote wipe (Phase 12 MDM). The status carries
     * it; false unless one is live, so a stale flag can never erase the phone.
     */
    static boolean wipeRequested(Context context) {
        return lastStatus(context).optBoolean("wipe_requested", false);
    }

    /** The parents' answers on new apps: package to pending, approved or denied. */
    static Map<String, String> appApprovals(Context context) {
        return stringMap(lastStatus(context).optJSONObject("app_approvals"));
    }

    /** The parents' answers on app permissions: "package|group" to pending, approved or denied. */
    static Map<String, String> permissionApprovals(Context context) {
        return stringMap(lastStatus(context).optJSONObject("permission_approvals"));
    }

    /** The kinds of permission a parent approves first (camera, location, ...). */
    static Set<String> permissionGating(Context context) {
        return strings(lastStatus(context).optJSONArray("permission_gating"));
    }

    private static Map<String, String> stringMap(JSONObject o) {
        Map<String, String> out = new HashMap<>();
        if (o == null) return out;
        for (Iterator<String> it = o.keys(); it.hasNext(); ) {
            String key = it.next();
            out.put(key, o.optString(key));
        }
        return out;
    }

    /**
     * The apps on the phone when the guard first ran on it; set once, so an
     * app installed later is new. Returns the baseline.
     */
    static Set<String> installBaseline(Context context, Set<String> installedNow) {
        SharedPreferences p = prefs(context);
        Set<String> saved = p.getStringSet(KEY_INSTALL_BASELINE, null);
        if (saved != null) return new HashSet<>(saved);
        p.edit().putStringSet(KEY_INSTALL_BASELINE, new HashSet<>(installedNow)).apply();
        return new HashSet<>(installedNow);
    }

    /**
     * Ask a parent first: a new app ("app", package), a blocked app
     * ("app_unblock", package), an app's permission ("permission",
     * "package|group") or a blocked site ("site", domain). Call off the main
     * thread. Returns an error to show, or null.
     */
    static String askParent(Context context, String kind, String target, String label) {
        return askParent(context, kind, target, label, null);
    }

    static String askParent(Context context, String kind, String target, String label, String note) {
        try {
            JSONObject body = new JSONObject();
            body.put("kind", kind);
            body.put("target", target);
            if (label != null && !label.isEmpty()) body.put("label", label);
            if (note != null && !note.trim().isEmpty()) body.put("note", note.trim());
            HttpResult r = send(context, "POST", "/api/guard/requests", body.toString());
            if (r == null) return "No connection to Jarvis. Try again when the phone is online.";
            if (r.status == 200) return null;
            String detail = new JSONObject(r.body.isEmpty() ? "{}" : r.body).optString("detail", "");
            return detail.isEmpty() ? "That did not work." : detail;
        } catch (Exception e) {
            return "That did not work.";
        }
    }

    /**
     * The lock screen's "Ask for more time": a real request a parent answers,
     * with the reason the child typed. Off the main thread. Returns an error to
     * show, or null.
     */
    static String askMoreTime(Context context, int minutes, String note) {
        try {
            String day = statusDay(context);
            if (day == null || day.isEmpty()) day = java.time.LocalDate.now().toString();
            JSONObject body = new JSONObject();
            body.put("minutes", minutes);
            body.put("day", day);
            if (note != null && !note.trim().isEmpty()) body.put("note", note.trim());
            HttpResult r = send(context, "POST", "/api/screen-time/requests", body.toString());
            if (r == null) return "No connection to Jarvis. Try again when the phone is online.";
            if (r.status == 200) return null;
            String detail = new JSONObject(r.body.isEmpty() ? "{}" : r.body).optString("detail", "");
            return detail.isEmpty() ? "That did not work." : detail;
        } catch (Exception e) {
            return "That did not work.";
        }
    }

    /**
     * The lock screen's "Message a parent": a short chat message to them, sent
     * as this child in the family chat. Off the main thread. Returns an error to
     * show, or null.
     */
    static String messageParents(Context context, String text) {
        try {
            JSONObject body = new JSONObject();
            body.put("text", text == null ? "" : text.trim());
            HttpResult r = send(context, "POST", "/api/guard/message", body.toString());
            if (r == null) return "No connection to Jarvis. Try again when the phone is online.";
            if (r.status == 200) return null;
            String detail = new JSONObject(r.body.isEmpty() ? "{}" : r.body).optString("detail", "");
            return detail.isEmpty() ? "That did not work." : detail;
        } catch (Exception e) {
            return "That did not work.";
        }
    }

    static Set<String> alwaysAllowed(Context context) {
        return strings(lastStatus(context).optJSONArray("always_allowed"));
    }
    /** Apps blocked now (a parent switched them off, or their own minutes ran out). */
    static Set<String> blockedApps(Context context) {
        return strings(lastStatus(context).optJSONArray("blocked_apps"));
    }

    /** Apps a parent queued for remote uninstall on this phone. */
    static Set<String> uninstallApps(Context context) {
        return strings(lastStatus(context).optJSONArray("uninstall_apps"));
    }

    /** Apps a parent queued or pre-approved for installation on this phone. */
    static Set<String> installApps(Context context) {
        return strings(lastStatus(context).optJSONArray("install_apps"));
    }

    /** Whether COSU dedicated kiosk mode is enabled for this phone. */
    static boolean kioskMode(Context context) {
        return lastStatus(context).optBoolean("kiosk_mode", false);
    }

    /** Apps allowed alongside Jarvis and always_allowed when kiosk mode is on. */
    static Set<String> kioskApps(Context context) {
        return strings(lastStatus(context).optJSONArray("kiosk_apps"));
    }

    /** Packages Jarvis suspended on the last device-owner sync. */
    static Set<String> suspendedPackages(Context context) {
        Set<String> saved = prefs(context).getStringSet(KEY_SUSPENDED_PACKAGES, null);
        return saved == null ? new HashSet<String>() : new HashSet<>(saved);
    }

    static void setSuspendedPackages(Context context, Set<String> packages) {
        prefs(context).edit().putStringSet(KEY_SUSPENDED_PACKAGES, new HashSet<>(packages)).apply();
    }

    /** A JSON list of strings as a set (empty when missing). */
    static Set<String> strings(JSONArray list) {
        Set<String> out = new HashSet<>();
        if (list != null) {
            for (int i = 0; i < list.length(); i++) out.add(list.optString(i));
        }
        return out;
    }

    /** An ISO-8601 time from the server as epoch ms; 0 when empty or unreadable. */
    static long epochMs(String iso) {
        if (iso == null || iso.isEmpty() || "null".equals(iso)) return 0L;
        try {
            return java.time.OffsetDateTime.parse(iso).toInstant().toEpochMilli();
        } catch (Exception e) {
            return 0L;
        }
    }

    static boolean isUnlocked(Context context) {
        return System.currentTimeMillis() < prefs(context).getLong(KEY_UNLOCKED_UNTIL, 0L);
    }

    /** A parent's own login lifts the guard for the window the server grants. Returns an error to show, or null. */
    static String parentUnlock(Context context, String username, String password) {
        try {
            JSONObject body = new JSONObject();
            body.put("username", username);
            body.put("password", password);
            HttpResult r = send(context, "POST", "/api/guard/parent-unlock", body.toString());
            if (r == null) return "No connection to Jarvis. Try again when the phone is online.";
            if (r.status == 200) {
                long until = epochMs(new JSONObject(r.body).optString("until", ""));
                long ms = until == 0L ? System.currentTimeMillis() + 10 * 60_000L : until;
                JSONObject answer = new JSONObject(r.body);
                SharedPreferences.Editor edit = prefs(context).edit().putLong(KEY_UNLOCKED_UNTIL, ms);
                JSONArray codes = answer.optJSONArray("offline_codes");
                if (codes != null) edit.putString(KEY_OFFLINE_CODES, codes.toString());
                edit.apply();
                return null;
            }
            String detail = new JSONObject(r.body.isEmpty() ? "{}" : r.body).optString("detail", "");
            return detail.isEmpty() ? "That did not work." : detail;
        } catch (Exception e) {
            return "That did not work.";
        }
    }

    /**
     * A parent's offline code, typed with no connection. Five wrong codes wait
     * ten minutes, like the online unlock. A good code lifts the guard for ten
     * minutes and the parents are told once the phone is back online.
     * Returns an error to show, or null.
     */
    static String offlineUnlock(Context context, String code) {
        SharedPreferences p = prefs(context);
        long now = System.currentTimeMillis();
        java.util.List<Long> fails = new java.util.ArrayList<>();
        for (String t : p.getString(KEY_OFFLINE_FAILS, "").split(",")) {
            try {
                long at = Long.parseLong(t.trim());
                if (now - at < OFFLINE_WAIT_MS) fails.add(at);
            } catch (NumberFormatException ignored) {
                // empty or damaged entry
            }
        }
        if (fails.size() >= OFFLINE_MAX_FAILS) return "Too many tries. Wait ten minutes, then try again.";
        JSONArray codes;
        try {
            codes = new JSONArray(p.getString(KEY_OFFLINE_CODES, "[]"));
        } catch (Exception e) {
            codes = new JSONArray();
        }
        if (codes.length() == 0) {
            return "No offline code is set up on this phone yet. A parent signs in here once while online to set it up.";
        }
        for (int i = 0; i < codes.length(); i++) {
            JSONObject c = codes.optJSONObject(i);
            if (c != null && OfflineCode.matches(code, c.optString("salt"), c.optInt("iterations"), c.optString("hash"))) {
                p.edit()
                        .putLong(KEY_UNLOCKED_UNTIL, now + 10 * 60_000L)
                        .putString(KEY_OFFLINE_FAILS, "")
                        .putBoolean(KEY_OFFLINE_REPORT_DUE, true)
                        .apply();
                reportOfflineUnlockIfDue(context);
                return null;
            }
        }
        fails.add(now);
        StringBuilder joined = new StringBuilder();
        for (Long at : fails) joined.append(joined.length() == 0 ? "" : ",").append(at);
        p.edit().putString(KEY_OFFLINE_FAILS, joined.toString()).apply();
        return "That code is not right.";
    }

    /** An offline unlock is never silent: tell the parents as soon as the server answers. */
    static void reportOfflineUnlockIfDue(Context context) {
        if (!prefs(context).getBoolean(KEY_OFFLINE_REPORT_DUE, false)) return;
        new Thread(() -> {
            try {
                JSONObject body = new JSONObject();
                body.put("event", "offline_unlock");
                HttpResult r = send(context, "POST", "/api/guard/tamper", body.toString());
                if (r != null && r.status == 200) {
                    prefs(context).edit().putBoolean(KEY_OFFLINE_REPORT_DUE, false).apply();
                }
            } catch (Exception ignored) {
                // Still offline: the next status refresh tries again.
            }
        }).start();
    }

    /** Sites visited and blocked since the last report, onto the activity feed (parents only). */
    static void reportWeb(Context context, java.util.Map<String, int[]> counts) {
        new Thread(() -> {
            for (java.util.Map.Entry<String, int[]> e : counts.entrySet()) {
                try {
                    JSONObject body = new JSONObject();
                    body.put("feature", "web");
                    body.put("item", e.getKey());
                    body.put("event", e.getValue()[1] > 0 ? "blocked" : "visit");
                    JSONObject detail = new JSONObject();
                    detail.put("visits", e.getValue()[0]);
                    detail.put("blocked", e.getValue()[1]);
                    body.put("detail", detail);
                    send(context, "POST", "/api/activity", body.toString());
                } catch (Exception ignored) {
                    // Best effort: a missed batch is not retried.
                }
            }
        }).start();
    }

    /** Whether the parents switched the web filter on for this phone. */
    static boolean webFilterWanted(Context context) {
        JSONObject rules = lastStatus(context).optJSONObject("rules");
        JSONObject wf = rules == null ? null : rules.optJSONObject("web_filter");
        return wf != null && wf.optBoolean("enabled", false);
    }

    /** The parent's content-monitoring mode for this phone ("all", "severe" or "off"). */
    static String monitoringMode(Context context) {
        JSONObject rules = lastStatus(context).optJSONObject("rules");
        String mode = rules == null ? "" : rules.optString("monitoring", "");
        return mode.isEmpty() ? "severe" : mode;
    }

    /** Whether photo and video safety scanning is enabled (Bark-style). */
    static boolean photoScanEnabled(Context context) {
        JSONObject rules = lastStatus(context).optJSONObject("rules");
        if (rules == null) return true;
        return rules.optBoolean("photo_scan", true);
    }

    /** Configured OS update policy type: default, automatic, windowed, postpone. */
    static String osUpdatePolicyType(Context context) {
        JSONObject status = lastStatus(context);
        JSONObject oup = status.optJSONObject("os_update_policy");
        if (oup == null) {
            JSONObject rules = status.optJSONObject("rules");
            if (rules != null) oup = rules.optJSONObject("os_update_policy");
        }
        return oup != null ? oup.optString("policy", DeviceOwnerRules.UPDATE_POLICY_DEFAULT) : DeviceOwnerRules.UPDATE_POLICY_DEFAULT;
    }

    /** Maintenance window start minute from midnight (0-1440), or null if not windowed. */
    static Integer osUpdateWindowStart(Context context) {
        JSONObject status = lastStatus(context);
        JSONObject oup = status.optJSONObject("os_update_policy");
        if (oup == null) {
            JSONObject rules = status.optJSONObject("rules");
            if (rules != null) oup = rules.optJSONObject("os_update_policy");
        }
        if (oup != null && oup.has("window_start_min") && !oup.isNull("window_start_min")) {
            return oup.optInt("window_start_min");
        }
        return null;
    }

    /** Maintenance window end minute from midnight (0-1440), or null if not windowed. */
    static Integer osUpdateWindowEnd(Context context) {
        JSONObject status = lastStatus(context);
        JSONObject oup = status.optJSONObject("os_update_policy");
        if (oup == null) {
            JSONObject rules = status.optJSONObject("rules");
            if (rules != null) oup = rules.optJSONObject("os_update_policy");
        }
        if (oup != null && oup.has("window_end_min") && !oup.isNull("window_end_min")) {
            return oup.optInt("window_end_min");
        }
        return null;
    }

    /** Whether always-on VPN lockdown is requested. */
    static boolean vpnLockdown(Context context) {
        JSONObject status = lastStatus(context);
        if (status.optBoolean("vpn_lockdown", false)) return true;
        JSONObject rules = status.optJSONObject("rules");
        if (rules != null && rules.optBoolean("vpn_lockdown", false)) return true;
        JSONObject wf = rules == null ? null : rules.optJSONObject("web_filter");
        return wf != null && wf.optBoolean("lockdown", false);
    }

    /** Configured Wi-Fi networks for household child devices. */
    static JSONArray wifiNetworks(Context context) {
        JSONObject status = lastStatus(context);
        JSONArray arr = status.optJSONArray("wifi_networks");
        if (arr != null) return arr;
        JSONObject rules = status.optJSONObject("rules");
        return rules != null ? rules.optJSONArray("wifi_networks") : null;
    }

    /** Trusted CA certificates provisioned for household child devices. */
    static JSONArray caCerts(Context context) {
        JSONObject status = lastStatus(context);
        JSONArray arr = status.optJSONArray("ca_certs");
        if (arr != null) return arr;
        JSONObject rules = status.optJSONObject("rules");
        return rules != null ? rules.optJSONArray("ca_certs") : null;
    }

    /** Post a social/chat notification from a watched minor's phone onto the activity feed. */
    static void reportSocialNotification(Context context, String pkg, String label, String sender, String text) {
        new Thread(() -> {
            try {
                JSONObject body = new JSONObject();
                body.put("feature", "social");
                body.put("item", label == null || label.isEmpty() ? pkg : label);
                body.put("event", "notification");
                JSONObject detail = new JSONObject();
                detail.put("package", pkg);
                if (sender != null && !sender.isEmpty()) detail.put("sender", sender);
                detail.put("body", text);
                body.put("detail", detail);
                send(context, "POST", "/api/activity", body.toString());
            } catch (Exception ignored) {
                // Best effort: notification monitoring never blocks the phone.
            }
        }).start();
    }

    /** Report a photo or video thumbnail to be scanned for safety concerns. */
    static void reportMediaScan(Context context, String mediaType, String fileName, String thumbnailB64) {
        if (!photoScanEnabled(context)) return;
        new Thread(() -> {
            try {
                JSONObject body = new JSONObject();
                body.put("media_type", mediaType);
                body.put("file_name", fileName);
                body.put("image_b64", thumbnailB64);
                send(context, "POST", "/api/guardian/media/scan", body.toString());
            } catch (Exception ignored) {
                // Best effort: media scanning never blocks the phone.
            }
        }).start();
    }

    /** Tell the parents protection changed. Kept until the server takes it (see {@link #flushTamper}). */
    static void reportTamper(Context context, String event) {
        reportTamper(context, event, null);
    }

    /**
     * Tell the parents protection changed or was worked around, with an
     * optional detail (the app or Private DNS host). The report is kept on the
     * phone and sent now if it can be, else after the next good status: a
     * report from a phone that is offline, or just starting, is not lost.
     */
    @android.annotation.SuppressLint("ApplySharedPref") // the report must be on disk before the guard may stop
    static void reportTamper(Context context, String event, String detail) {
        JSONObject item;
        try {
            item = new JSONObject().put("event", event);
            if (detail != null && !detail.isEmpty()) item.put("detail", detail);
        } catch (Exception e) {
            return; // A report we cannot build is not kept; the guard itself keeps working.
        }
        String key = item.toString();
        synchronized (TAMPER_LOCK) {
            JSONArray pending = pendingTamper(context);
            for (int i = 0; i < pending.length(); i++) {
                JSONObject o = pending.optJSONObject(i);
                if (o != null && key.equals(o.toString())) {
                    pending = null;
                    break;
                }
            }
            if (pending != null) {
                if (pending.length() >= TAMPER_QUEUE_MAX) pending.remove(0);
                pending.put(item);
                // commit, not apply: the guard may be stopping (onUnbind) and the report must outlive it.
                prefs(context).edit().putString(KEY_TAMPER_PENDING, pending.toString()).commit();
            }
        }
        new Thread(() -> flushTamper(context)).start();
    }

    /**
     * Send the kept tamper reports; each leaves the queue once the server took
     * it. Off the main thread. The queue lock is held only to read and write
     * prefs, never across the network.
     */
    static void flushTamper(Context context) {
        if (!FLUSHING.compareAndSet(false, true)) return;
        try {
            JSONArray pending;
            synchronized (TAMPER_LOCK) {
                pending = pendingTamper(context);
            }
            Set<String> sent = new HashSet<>();
            for (int i = 0; i < pending.length(); i++) {
                JSONObject item = pending.optJSONObject(i);
                if (item == null) continue;
                try {
                    HttpResult r = send(context, "POST", "/api/guard/tamper", item.toString());
                    // 422: the server does not know this event (an older server); dropping it beats retrying forever.
                    if (r != null && (r.status == 200 || r.status == 422)) sent.add(item.toString());
                } catch (Exception ignored) {
                    // Offline: kept for the next try.
                }
            }
            if (sent.isEmpty()) return;
            synchronized (TAMPER_LOCK) {
                JSONArray now = pendingTamper(context);
                JSONArray left = new JSONArray();
                for (int i = 0; i < now.length(); i++) {
                    JSONObject item = now.optJSONObject(i);
                    if (item != null && !sent.contains(item.toString())) left.put(item);
                }
                prefs(context).edit().putString(KEY_TAMPER_PENDING, left.toString()).apply();
            }
        } finally {
            FLUSHING.set(false);
        }
    }

    private static JSONArray pendingTamper(Context context) {
        try {
            return new JSONArray(prefs(context).getString(KEY_TAMPER_PENDING, "[]"));
        } catch (Exception e) {
            return new JSONArray();
        }
    }

    /** The ways around the web filter the parents were last told about (FilterBypass condition keys). */
    static Set<String> bypassReported(Context context) {
        return new HashSet<>(prefs(context).getStringSet(KEY_BYPASS_REPORTED, new HashSet<>()));
    }

    static void setBypassReported(Context context, Set<String> conditions) {
        prefs(context).edit().putStringSet(KEY_BYPASS_REPORTED, new HashSet<>(conditions)).apply();
    }

    /** When the guard last put Android's VPN consent prompt in front of the child (epoch ms). */
    static long lastFilterPrompt(Context context) {
        return prefs(context).getLong(KEY_FILTER_PROMPT_AT, 0L);
    }

    static void setLastFilterPrompt(Context context, long at) {
        prefs(context).edit().putLong(KEY_FILTER_PROMPT_AT, at).apply();
    }

    /** Android's boot counter at the guard's last run on this phone (0: never recorded). */
    static int lastGuardBoot(Context context) {
        return prefs(context).getInt(KEY_GUARD_BOOT, 0);
    }

    static void setLastGuardBoot(Context context, int boot) {
        prefs(context).edit().putInt(KEY_GUARD_BOOT, boot).apply();
    }

    private static String request(Context context, String method, String path, String json) throws Exception {
        HttpResult r = send(context, method, path, json);
        return r != null && r.status == 200 ? r.body : null;
    }

    static final class HttpResult {
        final int status;
        final String body;

        HttpResult(int status, String body) {
            this.status = status;
            this.body = body;
        }
    }

    private static HttpResult send(Context context, String method, String path, String json) throws Exception {
        SharedPreferences p = prefs(context);
        String token = p.getString(TokenBridgePlugin.KEY_API_KEY, null);
        String server = p.getString(TokenBridgePlugin.KEY_SERVER_URL, null);
        if (token == null || server == null) return null;
        HttpURLConnection conn = (HttpURLConnection) new URL(server.replaceAll("/+$", "") + path).openConnection();
        try {
            conn.setRequestMethod(method);
            conn.setConnectTimeout(10_000);
            conn.setReadTimeout(15_000);
            conn.setRequestProperty("Authorization", "Bearer " + token);
            if (json != null) {
                conn.setDoOutput(true);
                conn.setRequestProperty("Content-Type", "application/json");
                try (OutputStream os = conn.getOutputStream()) {
                    os.write(json.getBytes(StandardCharsets.UTF_8));
                }
            }
            int status = conn.getResponseCode();
            InputStream in = status >= 400 ? conn.getErrorStream() : conn.getInputStream();
            String body = "";
            if (in != null) {
                try (InputStream s = in; ByteArrayOutputStream out = new ByteArrayOutputStream()) {
                    byte[] buf = new byte[4096];
                    int n;
                    while ((n = s.read(buf)) > 0) out.write(buf, 0, n);
                    body = out.toString("UTF-8");
                }
            }
            return new HttpResult(status, body);
        } finally {
            conn.disconnect();
        }
    }
}
