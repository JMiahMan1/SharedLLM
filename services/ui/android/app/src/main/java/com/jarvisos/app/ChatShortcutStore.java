package com.jarvisos.app;

import android.content.Context;
import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import java.io.File;
import java.io.FileOutputStream;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import org.json.JSONObject;

/**
 * The chats this phone has opened, kept so a launcher can add one from its
 * widget list (ChatShortcutPickerActivity) without the app running. Each
 * entry is only what the shortcut itself shows and opens: a name, a page, a
 * colour, initials and the picture, in the app's private storage. Never a
 * message, and nothing that unlocks a private chat.
 */
final class ChatShortcutStore {
    private static final String PREFS = "jarvis_chat_shortcuts";
    private static final String ICON_DIR = "chat-shortcuts";
    /** The picker lists the chats opened most recently, up to this many. */
    static final int MAX_ENTRIES = 12;

    static final class Entry {
        final String id;
        final String label;
        final String path;
        final int colour;
        final String monogram;
        final long at;

        Entry(String id, String label, String path, int colour, String monogram, long at) {
            this.id = id;
            this.label = label;
            this.path = path;
            this.colour = colour;
            this.monogram = monogram;
            this.at = at;
        }
    }

    private ChatShortcutStore() {}

    /** A file name from an id ("chat:<token>:beloved"): letters, digits, '-' and '_' only. */
    static String fileNameFor(String id) {
        return id.replaceAll("[^A-Za-z0-9_-]", "_") + ".png";
    }

    static void put(Context ctx, String id, String label, String path, int colour, String monogram, Bitmap picture) {
        try {
            JSONObject o = new JSONObject();
            o.put("label", label);
            o.put("path", path);
            o.put("colour", colour);
            o.put("monogram", monogram == null ? "" : monogram);
            o.put("at", System.currentTimeMillis());
            prefs(ctx).edit().putString(id, o.toString()).apply();
        } catch (Exception ignored) {
            return;
        }
        File icon = iconFile(ctx, id);
        if (picture == null) {
            //noinspection ResultOfMethodCallIgnored
            icon.delete();
        } else {
            try (FileOutputStream out = new FileOutputStream(icon)) {
                picture.compress(Bitmap.CompressFormat.PNG, 100, out);
            } catch (Exception ignored) {
                // The monogram stands in.
            }
        }
        trim(ctx);
    }

    static void remove(Context ctx, String id) {
        prefs(ctx).edit().remove(id).apply();
        //noinspection ResultOfMethodCallIgnored
        iconFile(ctx, id).delete();
    }

    /** Newest first. */
    static List<Entry> list(Context ctx) {
        List<Entry> out = new ArrayList<>();
        for (Map.Entry<String, ?> e : prefs(ctx).getAll().entrySet()) {
            if (!(e.getValue() instanceof String)) continue;
            String id = e.getKey();
            try {
                JSONObject o = new JSONObject((String) e.getValue());
                String label = o.optString("label", "");
                String path = o.optString("path", "");
                if (!ChatShortcutPlugin.validId(id) || !ChatShortcutPlugin.validPath(path) || label.trim().isEmpty()) continue;
                out.add(new Entry(id, label, path, o.optInt("colour", 0xFF6D28D9), o.optString("monogram", ""), o.optLong("at", 0)));
            } catch (Exception ignored) {
                // A damaged entry is skipped.
            }
        }
        Collections.sort(out, (a, b) -> Long.compare(b.at, a.at));
        return out;
    }

    static Bitmap picture(Context ctx, String id) {
        File f = iconFile(ctx, id);
        return f.isFile() ? BitmapFactory.decodeFile(f.getAbsolutePath()) : null;
    }

    private static void trim(Context ctx) {
        List<Entry> all = list(ctx);
        for (int i = MAX_ENTRIES; i < all.size(); i++) remove(ctx, all.get(i).id);
    }

    private static File iconFile(Context ctx, String id) {
        File dir = new File(ctx.getFilesDir(), ICON_DIR);
        //noinspection ResultOfMethodCallIgnored
        dir.mkdirs();
        return new File(dir, fileNameFor(id));
    }

    private static SharedPreferences prefs(Context ctx) {
        return ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }
}
