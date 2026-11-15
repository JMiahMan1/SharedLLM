package com.jarvisos.app;

import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.Base64;
import javax.crypto.Cipher;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;
import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

/**
 * Opens one private-chat message for a notification preview: the same sealed
 * envelope as services/ui/src/lib/privateChatCrypto.ts (a fenced
 * {@code jarvis-envelope} JSON with a 12-byte IV and an AES-GCM ciphertext of
 * the payload JSON). Free of Android imports so it runs in plain JVM tests.
 *
 * Only ever used when the person turned on message previews for the chat on
 * this phone ({@link PrivatePreviewPlugin}); any failure returns null and the
 * notice stays content-free.
 */
final class PrivateEnvelope {
    static final String FENCE = "```jarvis-envelope";
    /** Longest preview line, so a long message does not fill the shade. */
    static final int MAX_PREVIEW = 300;

    private PrivateEnvelope() {}

    /** The raw Talk text of message {@code id} in a /talk/messages answer, or null. */
    static String findMessage(String json, long id) {
        if (json == null || id <= 0) return null;
        try {
            JSONObject root = new JSONObject(json);
            JSONObject detail = root.optJSONObject("detail");
            JSONArray messages = detail != null ? detail.optJSONArray("messages") : null;
            if (messages == null) return null;
            for (int i = 0; i < messages.length(); i++) {
                JSONObject m = messages.optJSONObject(i);
                if (m != null && m.optLong("id", -1) == id) {
                    return m.isNull("message") ? null : m.optString("message", null);
                }
            }
        } catch (JSONException e) {
            return null;
        }
        return null;
    }

    /** The preview line for a sealed message, or null when it cannot be opened with {@code key}. */
    static String previewText(String raw, byte[] key) {
        if (raw == null || key == null || key.length != 32) return null;
        int start = raw.indexOf(FENCE);
        if (start < 0) return null;
        int end = raw.indexOf(FENCE, start + FENCE.length());
        if (end < 0) return null;
        byte[] plain = null;
        try {
            JSONObject env = new JSONObject(raw.substring(start + FENCE.length(), end).trim());
            if (!"sealed".equals(env.optString("kind"))) return null;
            byte[] iv = Base64.getDecoder().decode(env.getString("iv"));
            byte[] ct = Base64.getDecoder().decode(env.getString("ct"));
            if (iv.length != 12) return null;
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE, new SecretKeySpec(key, "AES"), new GCMParameterSpec(128, iv));
            plain = cipher.doFinal(ct);
            JSONObject payload = new JSONObject(new String(plain, StandardCharsets.UTF_8));
            return line(payload);
        } catch (Exception e) {
            return null;
        } finally {
            if (plain != null) Arrays.fill(plain, (byte) 0);
        }
    }

    static String line(JSONObject payload) {
        String type = payload.optString("t");
        String text;
        if ("text".equals(type)) {
            text = payload.optString("text", "");
        } else if ("image".equals(type)) {
            String caption = payload.optString("caption", "");
            text = caption.isEmpty() ? "Photo" : "Photo: " + caption;
        } else if ("comment".equals(type)) {
            text = "On a photo: " + payload.optString("text", "");
        } else {
            return null;
        }
        text = text.trim();
        if (text.isEmpty()) return null;
        return text.length() > MAX_PREVIEW ? text.substring(0, MAX_PREVIEW - 1) + "…" : text;
    }
}
