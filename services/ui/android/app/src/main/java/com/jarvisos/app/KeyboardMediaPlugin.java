package com.jarvisos.app;

import android.content.ContentResolver;
import android.content.Context;
import android.content.ClipDescription;
import android.net.Uri;
import android.util.Base64;
import android.util.Log;

import androidx.core.view.inputmethod.InputConnectionCompat;
import androidx.core.view.inputmethod.InputContentInfoCompat;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * A GIF, sticker or picture the keyboard inserted into a chat box
 * ({@link JarvisWebView}), handed to the page as a "media" event
 * (src/plugins/keyboardMedia.ts), which pastes it into the focused box: the
 * private chat encrypts it like any picked photo, a family chat attaches it.
 *
 * The keyboard's file is read off the keyboard's thread, at most
 * {@link KeyboardMedia#MAX_BYTES}, and its read permission is released as
 * soon as it has been read. Nothing is kept on the phone.
 */
@CapacitorPlugin(name = "JarvisKeyboardMedia")
public class KeyboardMediaPlugin extends Plugin {
    private static final String TAG = "KeyboardMedia";
    private static final ExecutorService READER = Executors.newSingleThreadExecutor();
    private static volatile KeyboardMediaPlugin instance;

    @Override
    public void load() {
        instance = this;
    }

    @PluginMethod
    public void available(PluginCall call) {
        JSObject result = new JSObject();
        result.put("available", true);
        call.resolve(result);
    }

    /** From the keyboard's commitContent: true when the picture is taken (it then reaches the page). */
    static boolean receive(Context context, InputContentInfoCompat info, int flags) {
        KeyboardMediaPlugin plugin = instance;
        if (plugin == null || info == null) return false;
        ClipDescription description = info.getDescription();
        String[] offered = new String[description.getMimeTypeCount()];
        for (int i = 0; i < offered.length; i++) offered[i] = description.getMimeType(i);
        String mime = KeyboardMedia.choose(offered);
        if (mime == null) return false;
        if ((flags & InputConnectionCompat.INPUT_CONTENT_GRANT_READ_URI_PERMISSION) != 0) {
            try {
                info.requestPermission();
            } catch (Exception e) {
                Log.w(TAG, "The keyboard did not let Jarvis read its picture", e);
                return false;
            }
        }
        Context app = context.getApplicationContext();
        READER.execute(() -> {
            JSObject event = new JSObject();
            try {
                byte[] bytes = read(app.getContentResolver(), info.getContentUri());
                if (bytes == null) {
                    event.put("error", "too_large");
                } else {
                    event.put("mime", mime);
                    event.put("name", KeyboardMedia.fileName(mime));
                    event.put("data", Base64.encodeToString(bytes, Base64.NO_WRAP));
                }
            } catch (IOException | RuntimeException e) {
                Log.w(TAG, "Could not read the keyboard's picture", e);
                event.put("error", "unreadable");
            } finally {
                try {
                    info.releasePermission();
                } catch (Exception ignored) {
                    // Never granted (no flag) or already gone.
                }
            }
            plugin.notifyListeners("media", event);
        });
        return true;
    }

    /** The file's bytes, or null when it is larger than {@link KeyboardMedia#MAX_BYTES}. */
    private static byte[] read(ContentResolver resolver, Uri uri) throws IOException {
        try (InputStream in = resolver.openInputStream(uri)) {
            if (in == null) throw new IOException("No stream for the keyboard's picture");
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buffer = new byte[64 * 1024];
            int total = 0;
            for (int n; (n = in.read(buffer)) != -1; ) {
                total += n;
                if (total > KeyboardMedia.MAX_BYTES) return null;
                out.write(buffer, 0, n);
            }
            return out.toByteArray();
        }
    }
}
