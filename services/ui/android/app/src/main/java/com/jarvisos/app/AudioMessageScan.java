package com.jarvisos.app;

import android.content.Context;
import android.content.SharedPreferences;
import android.database.Cursor;
import android.net.Uri;
import android.os.Bundle;
import android.provider.OpenableColumns;
import android.util.Base64;
import android.util.Log;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;

/**
 * Recorded voice messages: transcribed on the household server, never kept.
 *
 * A spoken message is invisible to every text filter - a threat dictated into a
 * voice note reads as nothing at all - so a child's phone sends the clip to the
 * household's own Whisper (services/telemetry, which asks the execution service
 * on this LAN). The audio is never written to disk by the app beyond the bytes
 * in memory, and the server keeps only what it says.
 *
 * Two ways in, and one rule for both: only a protected (child) device sends
 * anything. An adult's voice notes are never read.
 *
 * <ul>
 * <li>a picture/voice message (MMS) part with an audio type, read from the
 *     phone's own message store, one transcription per part;</li>
 * <li>an audio attachment a messaging app put on its notification, which exists
 *     only while the notification does - so it is read at once and, when it
 *     cannot be sent yet, kept in an app-private outbox and drained later
 *     ({@link MediaScanOutbox}, the same on-disk shape the photo checks use).</li>
 * </ul>
 *
 * Nothing here may break a message upload: every path is caught and logged.
 */
final class AudioMessageScan {
    static final String TAG = "JarvisAudioScan";

    /** The server refuses more than 8 MiB; half a minute of speech is far less. */
    static final int MAX_AUDIO_BYTES = 6 * 1024 * 1024;

    static final String PREFS = "jarvis_audio_scan";
    /** Notification clips already sent (or refused for good). */
    static final String KEY_SEEN = "audio_seen";
    /** MMS parts already sent, so an update does not re-transcribe them. */
    static final String KEY_MMS_AUDIO_SEEN = "mms_audio_seen";
    static final int MAX_SEEN = 400;

    /** Audio the server's Whisper can decode, by type... */
    private static final Set<String> MIME_TYPES = new HashSet<>(java.util.Arrays.asList(
            "audio/amr", "audio/3gpp", "audio/3gpp2", "audio/mp4", "audio/x-m4a", "audio/aac",
            "audio/mpeg", "audio/mp3", "audio/ogg", "application/ogg", "audio/opus",
            "audio/wav", "audio/x-wav", "audio/wave", "audio/webm"));
    /** ...and by extension, for the apps that put a bare file name on a notification. */
    private static final Set<String> EXTENSIONS = new HashSet<>(java.util.Arrays.asList(
            // No "mp4": a bare .mp4 is far more often a video than a voice note,
            // and an audio-only one arrives with an audio/ type anyway.
            "amr", "3gp", "3g2", "m4a", "aac", "mp3", "ogg", "opus", "wav", "webm"));

    private static final java.util.Map<String, String> EXTENSION_OF = new java.util.HashMap<>();

    static {
        EXTENSION_OF.put("audio/amr", "amr");
        EXTENSION_OF.put("audio/3gpp", "3gp");
        EXTENSION_OF.put("audio/3gpp2", "3g2");
        EXTENSION_OF.put("audio/mp4", "m4a");
        EXTENSION_OF.put("audio/x-m4a", "m4a");
        EXTENSION_OF.put("audio/aac", "aac");
        EXTENSION_OF.put("audio/mpeg", "mp3");
        EXTENSION_OF.put("audio/mp3", "mp3");
        EXTENSION_OF.put("audio/ogg", "ogg");
        EXTENSION_OF.put("application/ogg", "ogg");
        EXTENSION_OF.put("audio/opus", "opus");
        EXTENSION_OF.put("audio/wav", "wav");
        EXTENSION_OF.put("audio/x-wav", "wav");
        EXTENSION_OF.put("audio/wave", "wav");
        EXTENSION_OF.put("audio/webm", "webm");
    }

    private AudioMessageScan() {}

    /** The bare type, lowercased, without parameters ("audio/amr; codecs=x"). */
    static String baseType(String mime) {
        if (mime == null) return "";
        int semicolon = mime.indexOf(';');
        String bare = semicolon >= 0 ? mime.substring(0, semicolon) : mime;
        return bare.trim().toLowerCase(Locale.ROOT);
    }

    /** The extension of a name, lowercased, or "" when it has none. */
    static String extension(String name) {
        if (name == null) return "";
        int slash = Math.max(name.lastIndexOf('/'), name.lastIndexOf('\\'));
        String leaf = slash >= 0 ? name.substring(slash + 1) : name;
        int dot = leaf.lastIndexOf('.');
        if (dot <= 0 || dot == leaf.length() - 1) return "";
        return leaf.substring(dot + 1).toLowerCase(Locale.ROOT);
    }

    /** Whether the server's Whisper can read this type or this file name. */
    static boolean isSupported(String mime, String name) {
        String type = baseType(mime);
        // audio/* covers what a new app invents (audio/x-whatever); the list covers
        // the container Ogg type that is not audio/, and the extensions cover a
        // notification that mentions only a file name.
        return type.startsWith("audio/") || MIME_TYPES.contains(type) || EXTENSIONS.contains(extension(name));
    }

    /** A file name the server can pick a decoder from: the app's own, else the type's. */
    static String fileName(String name, String mime) {
        if (name != null && !name.trim().isEmpty() && !extension(name).isEmpty()) return name.trim();
        String base = name == null || name.trim().isEmpty() ? "voice-note" : name.trim();
        String ext = EXTENSION_OF.get(baseType(mime));
        return ext == null ? base + ".m4a" : base + "." + ext;
    }

    /** 2xx, or a 4xx that a retry will not change: the server is done with it. */
    static boolean settled(int status) {
        return (status >= 200 && status < 300) || (status >= 400 && status < 500 && status != 429);
    }

    static File outboxDir(Context app) {
        return new File(app.getFilesDir(), "audio_scan_outbox");
    }

    /**
     * The audio parts of one picture/voice message. Returns how many were sent
     * or queued; a failure is logged and never thrown, because the message
     * itself must still reach the parents.
     */
    static int scanMms(Context context, long mmsId, JSONObject meta) {
        Context app = context.getApplicationContext();
        if (!GuardApi.isProtected(app)) return 0;
        int sent = 0;
        try (Cursor c = app.getContentResolver().query(
                Uri.parse("content://mms/part"), new String[] {"_id", "ct", "name"},
                "mid = ?", new String[] {String.valueOf(mmsId)}, "_id ASC")) {
            while (c != null && c.moveToNext()) {
                long partId = c.getLong(c.getColumnIndexOrThrow("_id"));
                String type = c.getString(c.getColumnIndexOrThrow("ct"));
                String name = c.getString(c.getColumnIndexOrThrow("name"));
                if (!isSupported(type, name)) continue;
                String key = "mms-audio:" + mmsId + ":" + partId;
                if (seen(app, KEY_MMS_AUDIO_SEEN).contains(key)) continue;
                byte[] audio = readUpTo(app, Uri.parse("content://mms/part/" + partId), MAX_AUDIO_BYTES);
                if (audio == null || audio.length == 0) continue;
                String safeName = fileName(name == null || name.isEmpty() ? "mms-audio-" + partId : name, type);
                int status = send(app, safeName, type, audio, withKey(meta, key));
                if (settled(status)) {
                    remember(app, KEY_MMS_AUDIO_SEEN, key);
                    sent++;
                }
                // Not settled (offline, or the transcriber is busy): the MMS part
                // does not expire, so the next pass finds it again.
            }
        } catch (Exception e) {
            Log.w(TAG, "mms audio unreadable: " + e.getMessage());
        }
        return sent;
    }

    /**
     * An audio attachment a messaging app showed on its notification. The bytes
     * are read here and now (the URI dies with the notification), sent, and kept
     * in the outbox when it could not be sent.
     */
    static void scanNotification(Context context, Bundle extras, JSONObject meta) {
        if (extras == null) return;
        Context app = context.getApplicationContext();
        try {
            if (!GuardApi.isProtected(app)) return;
            android.os.Parcelable[] messages =
                    extras.getParcelableArray(android.app.Notification.EXTRA_MESSAGES);
            if (messages == null || messages.length == 0) return;
            int from = Math.max(0, messages.length - 2);
            for (int i = from; i < messages.length; i++) {
                if (!(messages[i] instanceof Bundle)) continue;
                Bundle message = (Bundle) messages[i];
                Object raw = message.get("uri");
                String uriText = raw instanceof Uri ? raw.toString() : raw instanceof String ? (String) raw : null;
                if (uriText == null || uriText.isEmpty()) continue;
                String mime = message.getString("type", "");
                String name = message.getString("name", message.getString("text", ""));
                if (!isSupported(mime, name) && !isSupported(mime, uriText)) continue;
                take(app, Uri.parse(uriText), mime, name, meta);
            }
        } catch (Exception e) {
            Log.w(TAG, "notification audio unreadable: " + e.getMessage());
        }
    }

    private static void take(Context app, Uri uri, String mime, String name, JSONObject meta) {
        String key = "notif-audio:" + uri;
        synchronized (AudioMessageScan.class) {
            if (seen(app, KEY_SEEN).contains(key)) return;
        }
        byte[] audio = readUpTo(app, uri, MAX_AUDIO_BYTES);
        if (audio == null || audio.length == 0) return;
        String safeName = fileName(name, mime);
        new Thread(() -> {
            try {
                int status = send(app, safeName, mime, audio, withKey(meta, key));
                boolean done = settled(status);
                if (!done) {
                    // Offline, or the transcriber cannot run now. The clip exists
                    // only as long as the notification: keep it and send it later.
                    done = MediaScanOutbox.add(outboxDir(app), mime == null ? "" : mime, safeName,
                            Base64.encodeToString(audio, Base64.NO_WRAP), meta == null ? null : meta.toString());
                }
                if (done) remember(app, KEY_SEEN, key);
            } catch (Exception e) {
                Log.w(TAG, "notification audio skipped: " + e.getMessage());
            }
        }, "jarvis-audio-notification").start();
    }

    /** Send clips kept while the transcriber was unavailable. Returns how many went. */
    static int drain(Context context) {
        Context app = context.getApplicationContext();
        if (!GuardApi.isProtected(app)) return 0;
        int sent = 0;
        for (MediaScanOutbox.Item item : MediaScanOutbox.pending(outboxDir(app), 5)) {
            JSONObject meta = null;
            try {
                if (item.metadata != null && !item.metadata.isEmpty()) meta = new JSONObject(item.metadata);
            } catch (Exception ignored) {
                // Unreadable context still leaves the clip worth reading.
            }
            int status = GuardApi.uploadAudioScan(app, item.fileName, item.mediaType, item.b64, meta);
            if (settled(status)) {
                MediaScanOutbox.done(item);
                sent++;
            } else {
                break; // still not possible: keep the rest, oldest first
            }
        }
        return sent;
    }

    private static int send(Context app, String name, String mime, byte[] audio, JSONObject meta) {
        String b64 = Base64.encodeToString(audio, Base64.NO_WRAP);
        return GuardApi.uploadAudioScan(app, name, mime == null || mime.isEmpty() ? "audio/mp4" : mime, b64, meta);
    }

    /** The message context, plus the key the server deduplicates on. */
    private static JSONObject withKey(JSONObject meta, String key) {
        JSONObject out = new JSONObject();
        try {
            if (meta != null) {
                java.util.Iterator<String> keys = meta.keys();
                while (keys.hasNext()) {
                    String k = keys.next();
                    out.put(k, meta.opt(k));
                }
            }
            out.put("message_key", key);
        } catch (Exception ignored) {
            // A context we cannot copy is still a clip worth reading.
        }
        return out;
    }

    /** At most {@link #MAX_AUDIO_BYTES} of a stream, or null. */
    private static byte[] readUpTo(Context app, Uri uri, int limit) {
        try (InputStream in = app.getContentResolver().openInputStream(uri)) {
            if (in == null) return null;
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buffer = new byte[32 * 1024];
            int read;
            while ((read = in.read(buffer)) > 0) {
                if (out.size() + read > limit) return null; // not a voice note
                out.write(buffer, 0, read);
            }
            return out.toByteArray();
        } catch (Exception e) {
            return null;
        }
    }

    /** A display name for a content URI, when the provider has one. */
    static String displayName(Context app, Uri uri) {
        try (Cursor c = app.getContentResolver().query(uri, new String[] {OpenableColumns.DISPLAY_NAME},
                null, null, null)) {
            if (c != null && c.moveToFirst() && !c.isNull(0)) return c.getString(0);
        } catch (Exception ignored) {
            // fall through: the type's extension is enough
        }
        return null;
    }

    private static Set<String> seen(Context app, String key) {
        Set<String> out = new HashSet<>();
        for (String value : prefs(app).getStringSet(key, new HashSet<>())) out.add(value);
        return out;
    }

    private static void remember(Context app, String key, String value) {
        SharedPreferences prefs = prefs(app);
        Set<String> all = new HashSet<>(prefs.getStringSet(key, new HashSet<>()));
        all.add(value);
        if (all.size() > MAX_SEEN) {
            List<String> ordered = new ArrayList<>(all);
            // A set has no order; dropping the first entries keeps it bounded,
            // which is all that is needed (the shapes are per part and per URI).
            all = new HashSet<>(ordered.subList(ordered.size() - MAX_SEEN, ordered.size()));
        }
        prefs.edit().putStringSet(key, all).apply();
    }

    private static SharedPreferences prefs(Context app) {
        return app.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }
}
