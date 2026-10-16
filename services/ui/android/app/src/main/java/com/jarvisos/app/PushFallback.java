package com.jarvisos.app;

import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Date;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.TimeZone;
import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

/**
 * The long poll {@link PushService} falls back to while the push stream is
 * unavailable on the server (ntfy down or not configured): the gateway's
 * {@code GET /api/telemetry/notifications/wait} reads the in-app outbox,
 * which telemetry writes before it tries ntfy, so notices still arrive.
 * The stream is tried again every {@link #STREAM_RETRY_MS} (docs/PUSH.md).
 *
 * Free of Android imports so the rules run in plain JVM tests.
 */
final class PushFallback {
    static final String WAIT = "/api/telemetry/notifications/wait";
    /** The server holds a wait at most 55 s; the phone asks for 50. */
    static final int WAIT_SECONDS = 50;
    static final int WAIT_READ_TIMEOUT_MS = (WAIT_SECONDS + 20) * 1000;
    /** While polling, how often the stream is tried again. */
    static final long STREAM_RETRY_MS = 2 * 60_000L;
    static final int SHOWN_LIMIT = 200;

    private PushFallback() {}

    /**
     * The gateway answers 503 when push is not configured and 502 when it
     * cannot reach ntfy; a proxy in front may say 504. Those mean the stream
     * is down on the server, so the long poll takes over. Anything else
     * (no network, 401) is not helped by polling instead.
     */
    static boolean streamUnavailable(int status) {
        return status == 502 || status == 503 || status == 504;
    }

    /**
     * The outbox cursor for a moment, in the form telemetry stamps notices
     * with (Python's {@code datetime.isoformat()} in UTC), so the gateway's
     * text comparison orders them correctly.
     */
    static String outboxCursor(long epochMillis) {
        SimpleDateFormat f = new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'000+00:00'", Locale.US);
        f.setTimeZone(TimeZone.getTimeZone("UTC"));
        return f.format(new Date(epochMillis));
    }

    /**
     * Ids of the notices the long poll showed, the most recent
     * {@link #SHOWN_LIMIT}. An outbox entry and the ntfy message for the same
     * notice carry the same id, so the stream's replay after polling can skip
     * what was already shown.
     */
    static final class Shown {
        private final Map<String, Boolean> ids = new LinkedHashMap<String, Boolean>() {
            @Override
            protected boolean removeEldestEntry(Map.Entry<String, Boolean> eldest) {
                return size() > SHOWN_LIMIT;
            }
        };

        /** Records {@code id}; false when it was already there. An empty id is never a repeat. */
        synchronized boolean add(String id) {
            if (id == null || id.isEmpty()) return true;
            return ids.put(id, Boolean.TRUE) == null;
        }

        synchronized boolean contains(String id) {
            return id != null && !id.isEmpty() && ids.containsKey(id);
        }
    }

    /** One answer of the long poll: the notices to show and the next cursor. */
    static final class Answer {
        final List<PushNotice> notices;
        final String cursor;

        Answer(List<PushNotice> notices, String cursor) {
            this.notices = notices;
            this.cursor = cursor;
        }
    }

    /**
     * Reads {@code {"notifications": [...], "cursor": "..."}}. Each outbox
     * entry is the same notice telemetry publishes to ntfy, so it is read by
     * {@link PushNotice#parse} like a stream message. The cursor stays
     * {@code cursor} when the answer has none.
     */
    static Answer parse(String json, String cursor) throws JSONException {
        JSONObject body = new JSONObject(json);
        JSONArray items = body.optJSONArray("notifications");
        List<PushNotice> notices = new ArrayList<>();
        if (items != null) {
            for (int i = 0; i < items.length(); i++) {
                JSONObject n = items.optJSONObject(i);
                PushNotice notice = n == null ? null : PushNotice.parse(n.toString());
                if (notice != null) notices.add(notice);
            }
        }
        String next = PushNotice.str(body, "cursor");
        return new Answer(Collections.unmodifiableList(notices), next.isEmpty() ? cursor : next);
    }
}
