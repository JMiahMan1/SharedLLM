package com.jarvisos.app;

import org.json.JSONException;
import org.json.JSONObject;

/**
 * One line of the push stream ({@code GET /api/push/stream}): ntfy's
 * line-delimited JSON passed through by the gateway, one event per line
 * ({@code open}, {@code keepalive}, {@code message}, {@code poll_request}).
 *
 * Free of Android imports so the parsing runs in plain JVM tests; the service
 * owns the connection and what happens to each message.
 */
final class PushEvent {
    static final String OPEN = "open";
    static final String KEEPALIVE = "keepalive";
    static final String MESSAGE = "message";

    final String event;
    /** ntfy's message id; sent back as {@code since} after a reconnect. */
    final String id;
    /** Empty for the person's own topic, which the gateway does not reveal. */
    final String topic;
    final String title;
    final String message;
    /** ntfy base64-encodes a body that is not UTF-8 text (UnifiedPush payloads). */
    final boolean base64;

    private PushEvent(String event, String id, String topic, String title, String message, boolean base64) {
        this.event = event;
        this.id = id;
        this.topic = topic;
        this.title = title;
        this.message = message;
        this.base64 = base64;
    }

    /** The event on one stream line, or null for a blank or unreadable line. */
    static PushEvent parse(String line) {
        if (line == null) return null;
        String trimmed = line.trim();
        if (trimmed.isEmpty() || trimmed.charAt(0) != '{') return null;
        JSONObject o;
        try {
            o = new JSONObject(trimmed);
        } catch (JSONException e) {
            return null;
        }
        String event = PushNotice.str(o, "event");
        if (event.isEmpty()) return null;
        return new PushEvent(
                event,
                PushNotice.str(o, "id"),
                PushNotice.str(o, "topic"),
                PushNotice.str(o, "title"),
                PushNotice.str(o, "message"),
                "base64".equalsIgnoreCase(PushNotice.str(o, "encoding")));
    }

    boolean isMessage() {
        return MESSAGE.equals(event);
    }

    /** Proof the connection is alive: open, keepalive or a message. */
    boolean isLiveness() {
        return OPEN.equals(event) || KEEPALIVE.equals(event) || MESSAGE.equals(event);
    }

    /** A message for another app on this phone rather than a Jarvis notice. */
    boolean isUnifiedPush() {
        return isMessage() && UnifiedPushRules.isUpTopic(topic);
    }
}
