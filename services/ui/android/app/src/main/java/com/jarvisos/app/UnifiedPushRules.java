package com.jarvisos.app;

import java.nio.charset.StandardCharsets;

/**
 * The checks UnifiedPush AND_3 asks of a distributor
 * (https://unifiedpush.org/developers/spec/android/): sizes of the extras,
 * which app sent a request, and which topics belong to UnifiedPush.
 *
 * Free of Android imports so the rules run in plain JVM tests;
 * {@link UnifiedPushReceiver} and {@link UnifiedPushDistributor} apply them.
 */
final class UnifiedPushRules {
    static final int MAX_TOKEN_BYTES = 100;
    static final int MAX_ID_BYTES = 100;
    static final int MAX_DESCRIPTION_BYTES = 100;
    static final int MAX_ENDPOINT_BYTES = 1000;
    static final int VAPID_LENGTH = 87;
    static final int MAX_MESSAGE_BYTES = 4096;

    private UnifiedPushRules() {}

    /** A connector token: present, at most 100 bytes of UTF-8. */
    static boolean validToken(String token) {
        return token != null && !token.isEmpty() && utf8Length(token) <= MAX_TOKEN_BYTES;
    }

    static boolean validMessageId(String id) {
        return id != null && !id.isEmpty() && utf8Length(id) <= MAX_ID_BYTES;
    }

    /** Optional on REGISTER; when sent it is a P-256 key, base64url, 87 characters. */
    static boolean validVapid(String vapid) {
        if (vapid == null) return true;
        if (vapid.length() != VAPID_LENGTH) return false;
        for (int i = 0; i < vapid.length(); i++) {
            char c = vapid.charAt(i);
            boolean ok = (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9')
                    || c == '-' || c == '_';
            if (!ok) return false;
        }
        return true;
    }

    /** The app's description of a registration; dropped when longer than allowed. */
    static String description(String message) {
        if (message == null || message.isEmpty() || utf8Length(message) > MAX_DESCRIPTION_BYTES) return null;
        return message;
    }

    static boolean validEndpoint(String endpoint) {
        return endpoint != null && endpoint.startsWith("https://")
                && utf8Length(endpoint) <= MAX_ENDPOINT_BYTES;
    }

    /** A MESSAGE carries 1 to 4096 bytes (already RFC 8291 encrypted by the sender). */
    static boolean validMessage(byte[] body) {
        return body != null && body.length >= 1 && body.length <= MAX_MESSAGE_BYTES;
    }

    /**
     * Which app sent REGISTER or UNREGISTER: the identity Android shares on
     * SDK 34 and later, otherwise the creator of the {@code pi} PendingIntent
     * the connector attaches. Null when neither is there, and the request is
     * then ignored, since a package name in a plain extra could be anyone's.
     */
    static String callerPackage(String sharedIdentity, String pendingIntentCreator) {
        if (sharedIdentity != null && !sharedIdentity.isEmpty()) return sharedIdentity;
        if (pendingIntentCreator != null && !pendingIntentCreator.isEmpty()) return pendingIntentCreator;
        return null;
    }

    /**
     * A token is removed only by the app it was registered for. An unknown
     * caller is allowed: the token is the capability and only that app knows it.
     */
    static boolean mayUnregister(String registeredApp, String caller) {
        return caller == null || caller.equals(registeredApp);
    }

    /** {@code up} plus the random part the gateway hands out (docs/PUSH.md). */
    static boolean isUpTopic(String topic) {
        if (topic == null || topic.length() < 3 || topic.length() > 64 || !topic.startsWith("up")) return false;
        for (int i = 2; i < topic.length(); i++) {
            char c = topic.charAt(i);
            boolean ok = (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9')
                    || c == '-' || c == '_';
            if (!ok) return false;
        }
        return true;
    }

    static int utf8Length(String s) {
        return s.getBytes(StandardCharsets.UTF_8).length;
    }
}
