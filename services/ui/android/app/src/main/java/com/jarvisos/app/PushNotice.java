package com.jarvisos.app;

import java.net.URI;
import java.net.URISyntaxException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

/**
 * A Jarvis notice as telemetry publishes it (docs/PUSH.md): the JSON carried
 * in the ntfy message's {@code message} field.
 *
 * Free of Android imports so the rules (which conversation a chat notice
 * belongs to, which notification a tag replaces, which actions are usable)
 * run in plain JVM tests.
 */
final class PushNotice {
    static final int MAX_ACTIONS = 3;
    /** Telemetry cuts a view action's url at 500 characters. */
    static final int MAX_URL_LENGTH = 500;

    static final class Action {
        static final String VIEW = "view";
        static final String CALLBACK = "callback";
        static final String REPLY = "reply";

        final String label;
        final String type;
        final String url;
        final String callback;
        /** The callback's params as JSON text, {@code {}} when none. */
        final String params;

        Action(String label, String type, String url, String callback, String params) {
            this.label = label;
            this.type = type;
            this.url = url;
            this.callback = callback;
            this.params = params;
        }

        boolean isView() {
            return VIEW.equals(type);
        }

        boolean isReply() {
            return REPLY.equals(type);
        }

        /** A view button for a web page (directions, say), opened in the browser. */
        boolean isExternal() {
            return isView() && url.startsWith("https://");
        }
    }

    final String id;
    final String category;
    final String kind;
    final String title;
    final String body;
    /** ntfy's scale, 1 (min) to 5 (urgent). */
    final int priority;
    final String tag;
    /** The in-app page the notice is about, already checked by {@link #safePath}. */
    final String path;
    final String dataToken;
    final boolean ringPhone;
    final String sound;
    final String emoji;
    final List<Action> actions;

    private PushNotice(String id, String category, String kind, String title, String body,
                       int priority, String tag, String path, String dataToken,
                       boolean ringPhone, String sound, String emoji, List<Action> actions) {
        this.id = id;
        this.category = category;
        this.kind = kind;
        this.title = title;
        this.body = body;
        this.priority = priority;
        this.tag = tag;
        this.path = path;
        this.dataToken = dataToken;
        this.ringPhone = ringPhone;
        this.sound = sound;
        this.emoji = emoji;
        this.actions = actions;
    }

    /** The notice in a stream message, or null when the text is not a notice. */
    static PushNotice parse(String json) {
        if (json == null) return null;
        String trimmed = json.trim();
        if (trimmed.isEmpty() || trimmed.charAt(0) != '{') return null;
        JSONObject o;
        try {
            o = new JSONObject(trimmed);
        } catch (JSONException e) {
            return null;
        }
        String title = str(o, "title");
        String body = str(o, "body");
        if (title.isEmpty() && body.isEmpty()) return null;
        JSONObject data = o.optJSONObject("data");
        String url = data != null ? str(data, "url") : "";
        String token = data != null ? str(data, "token") : "";
        boolean ringPhone = data != null && data.optBoolean("ring_phone", false);
        String sound = data != null ? str(data, "sound") : "";
        String emoji = data != null ? str(data, "emoji") : "";
        String category = str(o, "category");
        return new PushNotice(
                str(o, "id"),
                category.isEmpty() ? PushCategories.GENERAL : category,
                str(o, "kind"),
                title.isEmpty() ? "Jarvis" : title,
                body,
                clampPriority(o.optInt("priority", 3)),
                str(o, "tag"),
                safePath(url),
                token,
                ringPhone,
                sound,
                emoji,
                actions(o.optJSONArray("actions")));
    }

    /** A message that is not a notice (sent to the topic by hand): shown as it is. */
    static PushNotice plain(String id, String title, String body) {
        return new PushNotice(id == null ? "" : id, PushCategories.GENERAL, "",
                title == null || title.isEmpty() ? "Jarvis" : title, body == null ? "" : body,
                3, "", "", "", false, "", "", Collections.<Action>emptyList());
    }

    private static List<Action> actions(JSONArray list) {
        List<Action> out = new ArrayList<>();
        if (list == null) return out;
        for (int i = 0; i < list.length() && out.size() < MAX_ACTIONS; i++) {
            JSONObject a = list.optJSONObject(i);
            if (a == null) continue;
            String label = str(a, "label");
            String type = str(a, "type");
            if (label.isEmpty()) continue;
            if (Action.VIEW.equals(type)) {
                String url = safeActionUrl(str(a, "url"));
                if (url.isEmpty()) continue;
                out.add(new Action(label, type, url, "", "{}"));
            } else if (Action.CALLBACK.equals(type) || Action.REPLY.equals(type)) {
                String callback = str(a, "callback");
                if (callback.isEmpty()) continue;
                JSONObject params = a.optJSONObject("params");
                out.add(new Action(label, type, "", callback, params == null ? "{}" : params.toString()));
            }
        }
        return out;
    }

    boolean isChat() {
        return chatToken() != null;
    }

    /** The watch asked for this phone to ring (see {@link FindPhoneService}). */
    boolean isFindPhone() {
        return "find_phone".equals(kind) || "find_phone".equals(tag);
    }

    /** An alarm or phone-targeted timer that should ring on the Android alarm stream. */
    boolean isAlarmRing() {
        return "timers".equals(category) && ("alarm".equals(kind) || ringPhone || priority >= 5);
    }

    /**
     * The conversation a chat notice belongs to: {@code data.token}, else the
     * {@code chat:<token>} tag. Null for anything that is not a chat message.
     */
    String chatToken() {
        if (!PushCategories.CHAT.equals(category) && !PushCategories.CHAT.equals(kind)) return null;
        if (!dataToken.isEmpty()) return dataToken;
        if (tag.startsWith("chat:") && tag.length() > 5) return tag.substring(5);
        return null;
    }

    /** Same tag, same notification: a newer notice replaces the older one. */
    int notificationId() {
        if (!tag.isEmpty()) return ("tag:" + tag).hashCode();
        if (!id.isEmpty()) return ("id:" + id).hashCode();
        return ("text:" + title + "\n" + body).hashCode();
    }

    static int chatNotificationId(String token) {
        return ("chat:" + token).hashCode();
    }

    static int clampPriority(int p) {
        return Math.max(1, Math.min(5, p));
    }

    /**
     * Only a page inside the app may be opened: a path starting with one
     * slash. Anything else (a full URL, {@code //host}) becomes empty.
     */
    static String safePath(String url) {
        if (url == null || url.length() < 1 || url.charAt(0) != '/') return "";
        if (url.startsWith("//") || url.startsWith("/\\")) return "";
        return url;
    }

    /**
     * Where a view button may go: a page inside the app ({@link #safePath}),
     * or a web page over https with a host, which the browser opens. Anything
     * else (http, javascript:, intent:, a malformed address) becomes empty.
     */
    static String safeActionUrl(String url) {
        String path = safePath(url);
        if (!path.isEmpty()) return path;
        if (url == null || url.length() > MAX_URL_LENGTH || !url.startsWith("https://")) return "";
        try {
            URI uri = new URI(url);
            String host = uri.getHost();
            if (!"https".equals(uri.getScheme()) || host == null || host.isEmpty() || uri.getUserInfo() != null) {
                return "";
            }
        } catch (URISyntaxException e) {
            return "";
        }
        return url;
    }

    /** A string field, empty when missing or JSON null (org.json differs by platform there). */
    static String str(JSONObject o, String key) {
        if (o == null || !o.has(key) || o.isNull(key)) return "";
        Object v = o.opt(key);
        return v == null ? "" : String.valueOf(v);
    }
}
