package com.jarvisos.app;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Log;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.util.Arrays;
import java.util.Base64;
import java.util.regex.Pattern;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/**
 * "Show message text in notifications" for the parents' private chat
 * (src/plugins/privatePreview.ts), off by default, per chat and per phone.
 *
 * The chat's data key is kept here encrypted under an Android Keystore
 * AES-256-GCM key that deliberately does NOT need the phone's lock, because
 * the push service must open it in the background. That is weaker than the
 * chat itself: anyone holding this phone unlocked can read the previews, and
 * the app says so where it is turned on. The text never reaches the lock
 * screen: {@link PushNotifier} posts it VISIBILITY_PRIVATE with a public
 * version that says only "New private message".
 *
 * {@link #preview} fetches the message the notice names with the phone's own
 * API token, opens it ({@link PrivateEnvelope}) and returns one line; any
 * failure returns null and the notice stays content-free.
 */
@CapacitorPlugin(name = "JarvisPrivatePreview")
public class PrivatePreviewPlugin extends Plugin {
    private static final String TAG = "PrivatePreview";
    private static final String KEYSTORE = "AndroidKeyStore";
    private static final String ALIAS_PREFIX = "jarvis-private-chat-preview-";
    private static final String PREFS = "jarvis_private_chat_preview";
    private static final String TRANSFORM = "AES/GCM/NoPadding";
    private static final Pattern TOKEN = Pattern.compile("^[A-Za-z0-9]{4,64}$");
    private static final int KEY_BYTES = 32;

    @PluginMethod
    public void available(PluginCall call) {
        JSObject result = new JSObject();
        result.put("available", true);
        call.resolve(result);
    }

    @PluginMethod
    public void isEnabled(PluginCall call) {
        String token = token(call);
        if (token == null) return;
        JSObject result = new JSObject();
        result.put("enabled", enabled(getContext(), token));
        call.resolve(result);
    }

    @PluginMethod
    public void enable(PluginCall call) {
        String token = token(call);
        if (token == null) return;
        byte[] plain;
        try {
            plain = Base64.getDecoder().decode(call.getString("key", ""));
        } catch (IllegalArgumentException e) {
            call.reject("That is not a chat key.", "BAD_KEY");
            return;
        }
        try {
            if (plain.length != KEY_BYTES) {
                call.reject("That is not a chat key.", "BAD_KEY");
                return;
            }
            forget(getContext(), token);
            Cipher cipher = Cipher.getInstance(TRANSFORM);
            cipher.init(Cipher.ENCRYPT_MODE, createKey(token));
            byte[] ct = cipher.doFinal(plain);
            prefs(getContext()).edit()
                .putString(token, Base64.getEncoder().encodeToString(cipher.getIV()) + ":" + Base64.getEncoder().encodeToString(ct))
                .apply();
            JSObject result = new JSObject();
            result.put("enabled", true);
            call.resolve(result);
        } catch (Exception e) {
            forget(getContext(), token);
            call.reject("Could not turn on previews: " + e.getClass().getSimpleName(), "KEYSTORE");
        } finally {
            Arrays.fill(plain, (byte) 0);
        }
    }

    @PluginMethod
    public void disable(PluginCall call) {
        String token = token(call);
        if (token == null) return;
        forget(getContext(), token);
        JSObject result = new JSObject();
        result.put("enabled", false);
        call.resolve(result);
    }

    // --- Used by the push path ------------------------------------------------

    static boolean enabled(Context context, String token) {
        if (token == null || !TOKEN.matcher(token).matches()) return false;
        if (!prefs(context).contains(token)) return false;
        try {
            KeyStore store = KeyStore.getInstance(KEYSTORE);
            store.load(null);
            return store.containsAlias(ALIAS_PREFIX + token);
        } catch (Exception e) {
            return false;
        }
    }

    /** One line of the message a private-chat notice names, or null (then the notice stays content-free). */
    static String preview(Context context, String token, long messageId) {
        if (messageId <= 0 || !enabled(context, token)) return null;
        byte[] key = null;
        try {
            GuardApi.HttpResult r = GuardApi.send(context, "GET",
                "/api/communication/talk/messages?token=" + URLEncoder.encode(token, StandardCharsets.UTF_8.name())
                    + "&limit=20", null);
            if (r == null || r.status != 200) return null;
            String raw = PrivateEnvelope.findMessage(r.body, messageId);
            if (raw == null) return null;
            key = dataKey(context, token);
            return PrivateEnvelope.previewText(raw, key);
        } catch (Exception e) {
            Log.w(TAG, "no preview: " + e.getClass().getSimpleName());
            return null;
        } finally {
            if (key != null) Arrays.fill(key, (byte) 0);
        }
    }

    private static byte[] dataKey(Context context, String token) throws Exception {
        String stored = prefs(context).getString(token, null);
        if (stored == null || !stored.contains(":")) return null;
        String[] parts = stored.split(":", 2);
        KeyStore store = KeyStore.getInstance(KEYSTORE);
        store.load(null);
        SecretKey key = (SecretKey) store.getKey(ALIAS_PREFIX + token, null);
        if (key == null) return null;
        Cipher cipher = Cipher.getInstance(TRANSFORM);
        cipher.init(Cipher.DECRYPT_MODE, key, new GCMParameterSpec(128, Base64.getDecoder().decode(parts[0])));
        return cipher.doFinal(Base64.getDecoder().decode(parts[1]));
    }

    // --- Helpers ----------------------------------------------------------------

    private String token(PluginCall call) {
        String token = call.getString("token", "");
        if (token == null || !TOKEN.matcher(token).matches()) {
            call.reject("No such chat.", "BAD_TOKEN");
            return null;
        }
        return token;
    }

    private static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    private static SecretKey createKey(String token) throws Exception {
        KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, KEYSTORE);
        generator.init(new KeyGenParameterSpec.Builder(
                ALIAS_PREFIX + token, KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setKeySize(256)
            // Deliberately usable without the phone's lock: the push service opens it in the background.
            .setUserAuthenticationRequired(false)
            .build());
        return generator.generateKey();
    }

    /** Delete the Keystore key and the stored ciphertext for a chat. */
    static void forget(Context context, String token) {
        prefs(context).edit().remove(token).apply();
        try {
            KeyStore store = KeyStore.getInstance(KEYSTORE);
            store.load(null);
            if (store.containsAlias(ALIAS_PREFIX + token)) store.deleteEntry(ALIAS_PREFIX + token);
        } catch (Exception ignored) {
            // Nothing to delete.
        }
    }
}
