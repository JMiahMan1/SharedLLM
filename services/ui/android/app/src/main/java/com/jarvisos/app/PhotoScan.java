package com.jarvisos.app;

import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.media.MediaMetadataRetriever;
import android.net.Uri;
import android.os.Build;
import android.provider.MediaStore;
import android.util.Base64;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.util.HashSet;
import java.util.Set;

/**
 * Finds photos and videos on a protected child's phone and sends a small
 * thumbnail of each to the family's safety scanner, which looks for sexual
 * content, violence, drugs, self-harm and bullying. Three collectors feed it:
 *
 * <ul>
 * <li>the gallery and downloads ({@link PhotoScanWorker}, every 30 minutes),
 *     which carries camera pictures, screenshots and media a chat app saved;</li>
 * <li>picture messages ({@link CommsCollector} calls {@link #scanMms}), which
 *     covers both MMS the child sent and received;</li>
 * <li>incoming chat images that an app puts in its notification
 *     ({@link MediaSessionListenerService} calls {@link #scanNotification}).
 *     An app's private conversation is its own - Android gives no other app
 *     the outgoing side of an end-to-end chat.</li>
 * </ul>
 *
 * Only a downscaled JPEG (about 1024 px, quality 70) leaves the phone; the
 * server scans it and keeps only a verdict, never the image itself. Work is
 * remembered per item ({@code jarvis_photo_scan}), so a scan is not repeated
 * and an upload that failed is tried on the next run.
 */
final class PhotoScan {
    private static final String TAG = "PhotoScan";
    static final String PREFS = "jarvis_photo_scan";
    static final String KEY_CURSOR_AT = "cursor_at";
    static final String KEY_CURSOR_ID = "cursor_id";
    static final String KEY_SEEN = "seen";
    static final String KEY_MMS_SEEN = "mms_seen";

    /** The most images one pass will upload; a backlog drains over the next passes. */
    static final int MAX_UPLOADS = 8;
    /** Rows inspected per pass, so a huge gallery cannot stall a worker. */
    static final int MAX_ROWS = 250;
    /** The first pass starts a week back, not at the phone's first photo. */
    static final long FIRST_RUN_WINDOW_MS = 7L * 24 * 60 * 60 * 1000;
    static final int MAX_DIMENSION = 1024;
    static final int JPEG_QUALITY = 70;
    /** The server takes a downscaled JPEG; refuse anything that is somehow larger. */
    static final int MAX_ENCODED_BYTES = 1_600_000;
    static final int MAX_SEEN = 600;

    private PhotoScan() {}

    /** One gallery pass. Returns {ok, scanned, uploaded}; ok=false means retry later. */
    static JSONObject run(Context context) {
        Context app = context.getApplicationContext();
        int scanned = 0;
        int uploaded = 0;
        boolean ok = true;
        try {
            if (!GuardApi.isProtected(app) || !GuardApi.photoScanEnabled(app) || !hasMediaAccess(app)) {
                return result(true, 0, 0);
            }
            SharedPreferences prefs = prefs(app);
            long lastAt = prefs.getLong(KEY_CURSOR_AT, 0L);
            long lastId = prefs.getLong(KEY_CURSOR_ID, 0L);
            if (lastAt == 0L) lastAt = System.currentTimeMillis() / 1000L - FIRST_RUN_WINDOW_MS / 1000L;
            Set<String> seen = seen(prefs, KEY_SEEN);
            long cursorAt = lastAt;
            long cursorId = lastId;
            int rows = 0;
            int uploads = 0;
            String[] projection = {
                MediaStore.Files.FileColumns._ID,
                MediaStore.Files.FileColumns.MEDIA_TYPE,
                MediaStore.Files.FileColumns.DISPLAY_NAME,
                MediaStore.Files.FileColumns.MIME_TYPE,
                MediaStore.Files.FileColumns.SIZE,
                MediaStore.Files.FileColumns.DATE_ADDED,
            };
            String selection = "(" + MediaStore.Files.FileColumns.MEDIA_TYPE + "=? OR "
                    + MediaStore.Files.FileColumns.MEDIA_TYPE + "=?) AND ("
                    + MediaStore.Files.FileColumns.DATE_ADDED + " > ? OR ("
                    + MediaStore.Files.FileColumns.DATE_ADDED + " = ? AND "
                    + MediaStore.Files.FileColumns._ID + " > ?))";
            String[] args = {
                String.valueOf(MediaStore.Files.FileColumns.MEDIA_TYPE_IMAGE),
                String.valueOf(MediaStore.Files.FileColumns.MEDIA_TYPE_VIDEO),
                String.valueOf(lastAt), String.valueOf(lastAt), String.valueOf(lastId),
            };
            try (android.database.Cursor c = app.getContentResolver().query(
                    filesUri(), projection, selection, args,
                    MediaStore.Files.FileColumns.DATE_ADDED + " ASC, " + MediaStore.Files.FileColumns._ID + " ASC")) {
                while (c != null && c.moveToNext() && rows < MAX_ROWS && uploads < MAX_UPLOADS) {
                    rows++;
                    long id = c.getLong(c.getColumnIndexOrThrow(MediaStore.Files.FileColumns._ID));
                    long at = c.getLong(c.getColumnIndexOrThrow(MediaStore.Files.FileColumns.DATE_ADDED));
                    int kind = c.getInt(c.getColumnIndexOrThrow(MediaStore.Files.FileColumns.MEDIA_TYPE));
                    String mime = c.getString(c.getColumnIndexOrThrow(MediaStore.Files.FileColumns.MIME_TYPE));
                    String name = c.getString(c.getColumnIndexOrThrow(MediaStore.Files.FileColumns.DISPLAY_NAME));
                    long size = c.getLong(c.getColumnIndexOrThrow(MediaStore.Files.FileColumns.SIZE));
                    String key = "media:" + id;
                    if (size <= 0) {
                        // A camera or download still being written: not ready,
                        // so stop here and pick it up next pass.
                        break;
                    }
                    if (!PhotoScanMonitor.shouldScan(true, true, mime, name, size, seen, key)) {
                        cursorAt = at;
                        cursorId = id;
                        continue;
                    }
                    scanned++;
                    String b64 = mediaThumbnail(app, mediaUri(id, kind), mime);
                    if (b64 == null) {
                        // Unreadable or unsupported after all: pass over it rather
                        // than retrying the same broken file forever.
                        remember(prefs, KEY_SEEN, seen, key);
                        cursorAt = at;
                        cursorId = id;
                        continue;
                    }
                    int status = GuardApi.uploadMediaScan(app,
                            PhotoScanMonitor.mediaType(mime, name), name == null ? "" : name, b64);
                    if (status >= 200 && status < 300) {
                        uploaded++;
                        uploads++;
                        remember(prefs, KEY_SEEN, seen, key);
                        cursorAt = at;
                        cursorId = id;
                    } else if (status >= 400 && status < 500 && status != 429) {
                        // The server will never take this one (not a monitored
                        // child, scanning off): pass over it.
                        remember(prefs, KEY_SEEN, seen, key);
                        cursorAt = at;
                        cursorId = id;
                    } else {
                        // Offline or a server trouble: leave the cursor on this
                        // media so the next pass starts here.
                        ok = false;
                        break;
                    }
                }
            }
            prefs.edit().putLong(KEY_CURSOR_AT, cursorAt).putLong(KEY_CURSOR_ID, cursorId).apply();
        } catch (Exception e) {
            ok = false;
            Log.w(TAG, "gallery scan stopped: " + e.getMessage());
        }
        return result(ok, scanned, uploaded);
    }

    private static JSONObject result(boolean ok, int scanned, int uploaded) {
        JSONObject out = new JSONObject();
        try {
            out.put("ok", ok);
            out.put("scanned", scanned);
            out.put("uploaded", uploaded);
        } catch (Exception ignored) {
            // Plain keys.
        }
        return out;
    }

    /**
     * Picture and video parts of one MMS. Covers what the child sends as well
     * as what arrives; the part is opened through the message provider, so no
     * gallery permission is needed. A failed upload is retried on the next
     * comms pass.
     */
    static void scanMms(Context context, long mmsId) {
        Context app = context.getApplicationContext();
        try {
            if (!GuardApi.isProtected(app) || !GuardApi.photoScanEnabled(app)) return;
            SharedPreferences prefs = prefs(app);
            Set<String> seen = seen(prefs, KEY_MMS_SEEN);
            String[] projection = {"_id", "ct", "name"};
            try (android.database.Cursor c = app.getContentResolver().query(
                    Uri.parse("content://mms/part"), projection, "mid = ?",
                    new String[] {String.valueOf(mmsId)}, "_id ASC")) {
                while (c != null && c.moveToNext()) {
                    long partId = c.getLong(c.getColumnIndexOrThrow("_id"));
                    String mime = c.getString(c.getColumnIndexOrThrow("ct"));
                    String name = c.getString(c.getColumnIndexOrThrow("name"));
                    String mediaType = PhotoScanMonitor.mediaType(mime, name);
                    if (!PhotoScanMonitor.isSupportedMedia(mime, name)) continue;
                    String key = "mms:" + mmsId + ":" + partId;
                    if (seen.contains(key)) continue;
                    Uri uri = Uri.parse("content://mms/part/" + partId);
                    String b64 = mediaThumbnail(app, uri, mime);
                    if (b64 == null) {
                        remember(prefs, KEY_MMS_SEEN, seen, key);
                        continue;
                    }
                    int status = GuardApi.uploadMediaScan(app, mediaType,
                            name == null || name.isEmpty() ? "mms-" + partId : name, b64);
                    if ((status >= 200 && status < 300) || (status >= 400 && status < 500 && status != 429)) {
                        remember(prefs, KEY_MMS_SEEN, seen, key);
                    }
                }
            }
        } catch (Exception e) {
            Log.w(TAG, "mms media unreadable: " + e.getMessage());
        }
    }

    /**
     * Chat images a messaging app attached to its notification. Only what the
     * app chose to show on the lock screen: an app's private camera roll, and
     * anything sent end-to-end, is not visible to any other app.
     */
    static void scanNotification(Context context, android.os.Bundle extras) {
        if (extras == null) return;
        Context app = context.getApplicationContext();
        try {
            if (!GuardApi.isProtected(app) || !GuardApi.photoScanEnabled(app)) return;
            android.os.Parcelable[] messages =
                    extras.getParcelableArray(android.app.Notification.EXTRA_MESSAGES);
            if (messages == null || messages.length == 0) return;
            // Only the newest couple of messages: an update re-posts the whole
            // conversation, and old pictures were already scanned.
            int from = Math.max(0, messages.length - 2);
            for (int i = from; i < messages.length; i++) {
                if (!(messages[i] instanceof android.os.Bundle)) continue;
                android.os.Bundle message = (android.os.Bundle) messages[i];
                Object raw = message.get("uri");
                String uriText = raw instanceof Uri ? raw.toString() : raw instanceof String ? (String) raw : null;
                if (uriText == null || uriText.isEmpty()) continue;
                String mime = message.getString("type", "image/jpeg");
                if (!PhotoScanMonitor.isSupportedMedia(mime, null)) continue;
                sendNotificationImage(app, Uri.parse(uriText), mime);
            }
        } catch (Exception e) {
            Log.w(TAG, "notification media unreadable: " + e.getMessage());
        }
    }

    private static void sendNotificationImage(Context app, Uri uri, String mime) {
        String key = "notif:" + uri;
        synchronized (PhotoScan.class) {
            SharedPreferences prefs = prefs(app);
            Set<String> seen = seen(prefs, KEY_SEEN);
            if (seen.contains(key)) return;
        }
        new Thread(() -> {
            try {
                String b64 = mediaThumbnail(app, uri, mime);
                if (b64 == null) return;
                int status = GuardApi.uploadMediaScan(app, "image", "notification-image.jpg", b64);
                if ((status >= 200 && status < 300) || (status >= 400 && status < 500 && status != 429)) {
                    SharedPreferences prefs = prefs(app);
                    Set<String> seen = seen(prefs, KEY_SEEN);
                    remember(prefs, KEY_SEEN, seen, key);
                }
            } catch (Exception e) {
                Log.w(TAG, "notification image skipped: " + e.getMessage());
            }
        }, "jarvis-photo-notification").start();
    }

    /** A downscaled JPEG of an image, or a frame of a video, or null. */
    private static String mediaThumbnail(Context app, Uri uri, String mime) {
        try {
            if (PhotoScanMonitor.mediaType(mime, null).equals("video")) {
                return videoThumbnail(app, uri);
            }
            try (InputStream in = app.getContentResolver().openInputStream(uri)) {
                if (in == null) return null;
                byte[] bytes = readUpTo(in, Math.min(PhotoScanMonitor.MAX_IMAGE_SIZE_BYTES,
                        12L * 1024 * 1024));
                if (bytes == null) return null;
                BitmapFactory.Options bounds = new BitmapFactory.Options();
                bounds.inJustDecodeBounds = true;
                BitmapFactory.decodeByteArray(bytes, 0, bytes.length, bounds);
                if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null;
                BitmapFactory.Options options = new BitmapFactory.Options();
                options.inSampleSize = sampleSize(bounds.outWidth, bounds.outHeight);
                Bitmap bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.length, options);
                return encode(bitmap);
            }
        } catch (Exception | OutOfMemoryError e) {
            Log.w(TAG, "media thumbnail failed: " + e.getMessage());
            return null;
        }
    }

    private static String videoThumbnail(Context app, Uri uri) {
        MediaMetadataRetriever retriever = new MediaMetadataRetriever();
        try {
            retriever.setDataSource(app, uri);
            Bitmap frame = retriever.getFrameAtTime(1_000_000L,
                    MediaMetadataRetriever.OPTION_CLOSEST_SYNC);
            return encode(frame);
        } catch (Exception | OutOfMemoryError e) {
            return null;
        } finally {
            try {
                retriever.release();
            } catch (Exception ignored) {
                // Already gone.
            }
        }
    }

    private static int sampleSize(int width, int height) {
        int sample = 1;
        while (Math.max(width / sample, height / sample) > MAX_DIMENSION) sample *= 2;
        return sample;
    }

    private static String encode(Bitmap bitmap) {
        if (bitmap == null) return null;
        try {
            Bitmap scaled = bitmap;
            int longest = Math.max(bitmap.getWidth(), bitmap.getHeight());
            if (longest > MAX_DIMENSION) {
                float factor = (float) MAX_DIMENSION / longest;
                scaled = Bitmap.createScaledBitmap(bitmap,
                        Math.max(1, Math.round(bitmap.getWidth() * factor)),
                        Math.max(1, Math.round(bitmap.getHeight() * factor)), true);
            }
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            scaled.compress(Bitmap.CompressFormat.JPEG, JPEG_QUALITY, out);
            if (scaled != bitmap) scaled.recycle();
            bitmap.recycle();
            byte[] jpeg = out.toByteArray();
            if (jpeg.length == 0 || jpeg.length > MAX_ENCODED_BYTES) return null;
            return Base64.encodeToString(jpeg, Base64.NO_WRAP);
        } catch (Exception | OutOfMemoryError e) {
            return null;
        }
    }

    private static byte[] readUpTo(InputStream in, long max) throws Exception {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buffer = new byte[16 * 1024];
        long total = 0;
        int n;
        while ((n = in.read(buffer)) > 0) {
            total += n;
            if (total > max) return null;
            out.write(buffer, 0, n);
        }
        return out.toByteArray();
    }

    /** Whether the phone has at least one media permission (photos, or all files on older Android). */
    static boolean hasMediaAccess(Context context) {
        if (Build.VERSION.SDK_INT >= 33) {
            return granted(context, "android.permission.READ_MEDIA_IMAGES")
                    || granted(context, "android.permission.READ_MEDIA_VISUAL_USER_SELECTED")
                    || granted(context, "android.permission.READ_MEDIA_VIDEO");
        }
        return granted(context, android.Manifest.permission.READ_EXTERNAL_STORAGE);
    }

    static boolean hasImageAccess(Context context) {
        if (Build.VERSION.SDK_INT >= 33) {
            return granted(context, "android.permission.READ_MEDIA_IMAGES")
                    || granted(context, "android.permission.READ_MEDIA_VISUAL_USER_SELECTED");
        }
        return granted(context, android.Manifest.permission.READ_EXTERNAL_STORAGE);
    }

    static boolean hasVideoAccess(Context context) {
        if (Build.VERSION.SDK_INT >= 33) {
            return granted(context, "android.permission.READ_MEDIA_VIDEO");
        }
        return granted(context, android.Manifest.permission.READ_EXTERNAL_STORAGE);
    }

    private static boolean granted(Context context, String permission) {
        return context.checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED;
    }

    private static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    /** A bounded set kept as a JSON array in the prefs. */
    private static Set<String> seen(SharedPreferences prefs, String key) {
        Set<String> out = new HashSet<>();
        try {
            JSONArray array = new JSONArray(prefs.getString(key, "[]"));
            for (int i = 0; i < array.length(); i++) out.add(array.getString(i));
        } catch (Exception ignored) {
            // A corrupt list only means an item may be scanned again.
        }
        return out;
    }

    @SuppressWarnings("ApplySharedPref")
    private static void remember(SharedPreferences prefs, String key, Set<String> seen, String value) {
        seen.add(value);
        try {
            JSONArray array = new JSONArray();
            int skip = Math.max(0, seen.size() - MAX_SEEN);
            int i = 0;
            for (String item : seen) {
                if (i++ < skip) continue;
                array.put(item);
            }
            // commit: the next pass must see that this item was taken.
            prefs.edit().putString(key, array.toString()).commit();
        } catch (Exception ignored) {
            // The item may be scanned once more; no harm.
        }
    }

    private static Uri filesUri() {
        if (Build.VERSION.SDK_INT >= 29) {
            return MediaStore.Files.getContentUri(MediaStore.VOLUME_EXTERNAL);
        }
        return MediaStore.Files.getContentUri("external");
    }

    /** The item URI for one gallery row, addressed through its own media collection. */
    private static Uri mediaUri(long id, int mediaType) {
        Uri collection = mediaType == MediaStore.Files.FileColumns.MEDIA_TYPE_VIDEO
                ? MediaStore.Video.Media.EXTERNAL_CONTENT_URI
                : MediaStore.Images.Media.EXTERNAL_CONTENT_URI;
        return android.content.ContentUris.withAppendedId(collection, id);
    }
}
