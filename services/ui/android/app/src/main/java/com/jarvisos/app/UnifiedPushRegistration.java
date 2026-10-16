package com.jarvisos.app;

import java.util.HashMap;
import java.util.Map;
import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

/**
 * One app's UnifiedPush registration on this phone: the connector's token,
 * the app it belongs to, and the topic and endpoint the gateway handed out.
 * Kept as JSON in SharedPreferences by {@link UnifiedPushDistributor}.
 */
final class UnifiedPushRegistration {
    final String token;
    final String app;
    final String topic;
    final String endpoint;
    /** What the app said the registration is for, or null. */
    final String description;

    UnifiedPushRegistration(String token, String app, String topic, String endpoint, String description) {
        this.token = token;
        this.app = app;
        this.topic = topic;
        this.endpoint = endpoint;
        this.description = description;
    }

    String toJson() {
        JSONObject o = new JSONObject();
        try {
            o.put("token", token);
            o.put("app", app);
            o.put("topic", topic);
            o.put("endpoint", endpoint);
            if (description != null) o.put("description", description);
        } catch (JSONException e) {
            throw new IllegalStateException(e);
        }
        return o.toString();
    }

    /** Whether {@code other} is the same app and token with the same topic and endpoint. */
    boolean sameDelivery(UnifiedPushRegistration other) {
        return other != null && token.equals(other.token) && app.equals(other.app)
                && topic.equals(other.topic) && endpoint.equals(other.endpoint);
    }

    /** The same registration under the gateway's current topic and endpoint. */
    UnifiedPushRegistration movedTo(UnifiedPushRegistration server) {
        return new UnifiedPushRegistration(token, app, server.topic, server.endpoint, description);
    }

    static String key(String app, String token) {
        return app + "\n" + token;
    }

    /**
     * This device's registrations in a {@code GET /api/push/up} answer (those
     * marked {@code this_device}), keyed by {@link #key}. Null when the answer
     * has no registrations list, so nothing is taken as removed by mistake.
     */
    static Map<String, UnifiedPushRegistration> thisDevice(JSONObject answer) {
        JSONArray list = answer == null ? null : answer.optJSONArray("registrations");
        if (list == null) return null;
        Map<String, UnifiedPushRegistration> out = new HashMap<>();
        for (int i = 0; i < list.length(); i++) {
            JSONObject o = list.optJSONObject(i);
            if (o == null || !o.optBoolean("this_device", false)) continue;
            UnifiedPushRegistration r = fromJson(o.toString());
            if (r != null) out.put(key(r.app, r.token), r);
        }
        return out;
    }

    /** The stored record, or null when it is missing a part and cannot be used. */
    static UnifiedPushRegistration fromJson(String json) {
        if (json == null) return null;
        try {
            JSONObject o = new JSONObject(json);
            String token = PushNotice.str(o, "token");
            String app = PushNotice.str(o, "app");
            String topic = PushNotice.str(o, "topic");
            String endpoint = PushNotice.str(o, "endpoint");
            String description = PushNotice.str(o, "description");
            if (!UnifiedPushRules.validToken(token) || app.isEmpty()
                    || !UnifiedPushRules.isUpTopic(topic) || endpoint.isEmpty()) {
                return null;
            }
            return new UnifiedPushRegistration(token, app, topic, endpoint,
                    description.isEmpty() ? null : description);
        } catch (JSONException e) {
            return null;
        }
    }
}
