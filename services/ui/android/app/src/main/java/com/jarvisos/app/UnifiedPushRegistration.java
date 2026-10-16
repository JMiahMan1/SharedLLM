package com.jarvisos.app;

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
