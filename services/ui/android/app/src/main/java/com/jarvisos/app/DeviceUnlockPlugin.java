package com.jarvisos.app;

import android.app.KeyguardManager;
import android.content.Context;
import android.content.SharedPreferences;
import android.os.Build;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyPermanentlyInvalidatedException;
import android.security.keystore.KeyProperties;
import android.util.Base64;

import androidx.annotation.NonNull;
import androidx.biometric.BiometricManager;
import androidx.biometric.BiometricPrompt;
import androidx.core.content.ContextCompat;
import androidx.fragment.app.FragmentActivity;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

import java.security.KeyStore;
import java.util.Arrays;
import java.util.regex.Pattern;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/**
 * "Unlock with this phone's lock" for the parents' private chat
 * (src/plugins/deviceUnlock.ts).
 *
 * One Android Keystore AES-256-GCM key per chat (alias includes the chat
 * token) that works only for the one operation right after the person passes
 * the phone's lock: fingerprint, face (strong biometrics), PIN, pattern or
 * password. It is destroyed by Android when a new fingerprint is enrolled or
 * the lock screen is removed. The chat's 32-byte data key is kept encrypted
 * under it (IV and ciphertext) in this app's private preferences; the plain
 * key is never written anywhere, and the byte arrays that held it are zeroed.
 *
 * Requires Android 11 (API 30), the app's minimum, for per-use
 * biometric-or-credential keys.
 */
@CapacitorPlugin(name = "JarvisDeviceUnlock")
public class DeviceUnlockPlugin extends Plugin {
    private static final String KEYSTORE = "AndroidKeyStore";
    private static final String ALIAS_PREFIX = "jarvis-private-chat-unlock-";
    private static final String PREFS = "jarvis_private_chat_unlock";
    private static final String TRANSFORM = "AES/GCM/NoPadding";
    private static final int AUTHENTICATORS =
        BiometricManager.Authenticators.BIOMETRIC_STRONG | BiometricManager.Authenticators.DEVICE_CREDENTIAL;
    private static final Pattern TOKEN = Pattern.compile("^[A-Za-z0-9]{4,64}$");
    private static final int KEY_BYTES = 32;
    static final String INVALIDATED =
        "This phone's lock changed (a new fingerprint or face, or the screen lock was removed), so unlocking with it was "
            + "turned off. Use the password, then set it up again with the password.";

    @PluginMethod
    public void available(PluginCall call) {
        JSObject result = new JSObject();
        String reason = unavailableReason();
        result.put("available", reason == null);
        if (reason != null) result.put("reason", reason);
        call.resolve(result);
    }

    @PluginMethod
    public void isEnabled(PluginCall call) {
        String token = token(call);
        if (token == null) return;
        JSObject result = new JSObject();
        result.put("enabled", prefs().contains(token) && hasKey(token));
        call.resolve(result);
    }

    @PluginMethod
    public void enable(PluginCall call) {
        String token = token(call);
        if (token == null) return;
        String reason = unavailableReason();
        if (reason != null) {
            call.reject(reason, "UNAVAILABLE");
            return;
        }
        byte[] decoded;
        try {
            decoded = Base64.decode(call.getString("key", ""), Base64.NO_WRAP);
        } catch (IllegalArgumentException e) {
            call.reject("That is not a chat key.", "BAD_KEY");
            return;
        }
        final byte[] plain = decoded;
        if (plain.length != KEY_BYTES) {
            Arrays.fill(plain, (byte) 0);
            call.reject("That is not a chat key.", "BAD_KEY");
            return;
        }
        Cipher cipher;
        try {
            forget(token);
            SecretKey key = createKey(token);
            cipher = Cipher.getInstance(TRANSFORM);
            cipher.init(Cipher.ENCRYPT_MODE, key);
        } catch (Exception e) {
            Arrays.fill(plain, (byte) 0);
            forget(token);
            call.reject("Could not set up the phone lock: " + e.getClass().getSimpleName(), "KEYSTORE");
            return;
        }
        prompt(call, cipher, "Use phone lock for private chat", new Done() {
            @Override
            public void ok(Cipher authed) {
                try {
                    byte[] ct = authed.doFinal(plain);
                    byte[] iv = authed.getIV();
                    prefs().edit()
                        .putString(token, Base64.encodeToString(iv, Base64.NO_WRAP) + ":" + Base64.encodeToString(ct, Base64.NO_WRAP))
                        .apply();
                    JSObject result = new JSObject();
                    result.put("enabled", true);
                    call.resolve(result);
                } catch (Exception e) {
                    forget(token);
                    call.reject("Could not keep the key on this phone: " + e.getClass().getSimpleName(), "KEYSTORE");
                } finally {
                    Arrays.fill(plain, (byte) 0);
                }
            }

            @Override
            public void failed(String message, String code) {
                Arrays.fill(plain, (byte) 0);
                forget(token);
                call.reject(message, code);
            }
        });
    }

    @PluginMethod
    public void unlock(PluginCall call) {
        String token = token(call);
        if (token == null) return;
        String stored = prefs().getString(token, null);
        if (stored == null || !stored.contains(":")) {
            call.reject("The phone lock is not set up for this chat. Use the password.", "NOT_ENABLED");
            return;
        }
        final byte[] iv;
        final byte[] ct;
        try {
            String[] parts = stored.split(":", 2);
            byte[] ivBytes = Base64.decode(parts[0], Base64.NO_WRAP);
            byte[] ctBytes = Base64.decode(parts[1], Base64.NO_WRAP);
            iv = ivBytes;
            ct = ctBytes;
        } catch (IllegalArgumentException e) {
            forget(token);
            call.reject(INVALIDATED, "INVALIDATED");
            return;
        }
        Cipher cipher;
        try {
            SecretKey key = loadKey(token);
            if (key == null) throw new KeyPermanentlyInvalidatedException();
            cipher = Cipher.getInstance(TRANSFORM);
            cipher.init(Cipher.DECRYPT_MODE, key, new GCMParameterSpec(128, iv));
        } catch (KeyPermanentlyInvalidatedException e) {
            forget(token);
            call.reject(INVALIDATED, "INVALIDATED");
            return;
        } catch (Exception e) {
            call.reject("Could not use the phone lock: " + e.getClass().getSimpleName(), "KEYSTORE");
            return;
        }
        prompt(call, cipher, "Unlock private chat", new Done() {
            @Override
            public void ok(Cipher authed) {
                byte[] plain = null;
                try {
                    plain = authed.doFinal(ct);
                    JSObject result = new JSObject();
                    result.put("key", Base64.encodeToString(plain, Base64.NO_WRAP));
                    call.resolve(result);
                } catch (Exception e) {
                    call.reject("Could not open the key on this phone: " + e.getClass().getSimpleName(), "KEYSTORE");
                } finally {
                    if (plain != null) Arrays.fill(plain, (byte) 0);
                }
            }

            @Override
            public void failed(String message, String code) {
                call.reject(message, code);
            }
        });
    }

    @PluginMethod
    public void disable(PluginCall call) {
        String token = token(call);
        if (token == null) return;
        forget(token);
        JSObject result = new JSObject();
        result.put("enabled", false);
        call.resolve(result);
    }

    // --- Helpers -------------------------------------------------------------

    private interface Done {
        void ok(Cipher authed);

        void failed(String message, String code);
    }

    private String token(PluginCall call) {
        String token = call.getString("token", "");
        if (token == null || !TOKEN.matcher(token).matches()) {
            call.reject("No such private chat.", "BAD_TOKEN");
            return null;
        }
        return token;
    }

    private String unavailableReason() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) return "Needs Android 11 or newer.";
        Context ctx = getContext();
        KeyguardManager keyguard = (KeyguardManager) ctx.getSystemService(Context.KEYGUARD_SERVICE);
        if (keyguard == null || !keyguard.isDeviceSecure()) return "Set a screen lock on this phone first.";
        int can = BiometricManager.from(ctx).canAuthenticate(AUTHENTICATORS);
        if (can != BiometricManager.BIOMETRIC_SUCCESS) return "This phone cannot confirm its lock for an app.";
        if (!(getActivity() instanceof FragmentActivity)) return "The app cannot show the phone's lock prompt.";
        return null;
    }

    private SharedPreferences prefs() {
        return getContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    private static String alias(String token) {
        return ALIAS_PREFIX + token;
    }

    private static SecretKey createKey(String token) throws Exception {
        KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, KEYSTORE);
        KeyGenParameterSpec spec = new KeyGenParameterSpec.Builder(
                alias(token), KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setKeySize(256)
            // Every use needs the phone's lock right then (timeout 0 = per operation).
            .setUserAuthenticationRequired(true)
            .setUserAuthenticationParameters(0, KeyProperties.AUTH_BIOMETRIC_STRONG | KeyProperties.AUTH_DEVICE_CREDENTIAL)
            // A new fingerprint or face destroys the key: someone who adds theirs cannot open the chat.
            .setInvalidatedByBiometricEnrollment(true)
            .build();
        generator.init(spec);
        return generator.generateKey();
    }

    private static SecretKey loadKey(String token) throws Exception {
        KeyStore store = KeyStore.getInstance(KEYSTORE);
        store.load(null);
        return (SecretKey) store.getKey(alias(token), null);
    }

    private static boolean hasKey(String token) {
        try {
            KeyStore store = KeyStore.getInstance(KEYSTORE);
            store.load(null);
            return store.containsAlias(alias(token));
        } catch (Exception e) {
            return false;
        }
    }

    /** Delete the Keystore key and the stored ciphertext for a chat. */
    private void forget(String token) {
        prefs().edit().remove(token).apply();
        try {
            KeyStore store = KeyStore.getInstance(KEYSTORE);
            store.load(null);
            if (store.containsAlias(alias(token))) store.deleteEntry(alias(token));
        } catch (Exception ignored) {
            // Nothing to delete.
        }
    }

    private void prompt(PluginCall call, Cipher cipher, String title, Done done) {
        FragmentActivity activity = (FragmentActivity) getActivity();
        activity.runOnUiThread(() -> {
            BiometricPrompt.PromptInfo info = new BiometricPrompt.PromptInfo.Builder()
                .setTitle(title)
                .setSubtitle("Fingerprint, face, PIN, pattern or password")
                .setAllowedAuthenticators(AUTHENTICATORS)
                .setConfirmationRequired(false)
                .build();
            BiometricPrompt prompt = new BiometricPrompt(activity, ContextCompat.getMainExecutor(activity),
                new BiometricPrompt.AuthenticationCallback() {
                    @Override
                    public void onAuthenticationSucceeded(@NonNull BiometricPrompt.AuthenticationResult result) {
                        BiometricPrompt.CryptoObject crypto = result.getCryptoObject();
                        if (crypto == null || crypto.getCipher() == null) {
                            done.failed("The phone lock did not unlock the key.", "KEYSTORE");
                            return;
                        }
                        done.ok(crypto.getCipher());
                    }

                    @Override
                    public void onAuthenticationError(int code, @NonNull CharSequence message) {
                        boolean cancelled = code == BiometricPrompt.ERROR_USER_CANCELED
                            || code == BiometricPrompt.ERROR_NEGATIVE_BUTTON
                            || code == BiometricPrompt.ERROR_CANCELED;
                        done.failed(cancelled ? "Cancelled. Use the password, or try the phone lock again." : message.toString(),
                            cancelled ? "CANCELLED" : "AUTH_ERROR");
                    }
                    // onAuthenticationFailed (a finger not recognised) leaves the prompt up for another try.
                });
            try {
                prompt.authenticate(info, new BiometricPrompt.CryptoObject(cipher));
            } catch (Exception e) {
                done.failed("Could not show the phone's lock: " + e.getClass().getSimpleName(), "AUTH_ERROR");
            }
        });
    }
}
