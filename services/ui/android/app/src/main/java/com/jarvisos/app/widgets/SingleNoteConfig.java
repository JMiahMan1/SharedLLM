package com.jarvisos.app.widgets;

import android.content.Context;
import android.content.SharedPreferences;
import android.util.Log;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import org.json.JSONObject;

/** Per-appWidgetId choice for the single-note home-screen widget. */
final class SingleNoteConfig {
    private static final String TAG = "SingleNoteConfig";
    private static final String PREFS = "jarvis_single_note";

    final String path;
    final String title;

    SingleNoteConfig(String path, String title) {
        this.path = path;
        this.title = title != null ? title : "";
    }

    private static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    static SingleNoteConfig load(Context context, int appWidgetId) {
        String raw = prefs(context).getString(key(appWidgetId), null);
        if (raw == null || raw.isEmpty()) return null;
        try {
            JSONObject o = new JSONObject(raw);
            String path = o.optString("path", "");
            if (path.isEmpty()) return null;
            return new SingleNoteConfig(path, o.optString("title", ""));
        } catch (Exception e) {
            Log.w(TAG, "load failed for " + appWidgetId + ": " + e.getMessage());
            return null;
        }
    }

    static void save(Context context, int appWidgetId, SingleNoteConfig config) {
        try {
            JSONObject o = new JSONObject();
            o.put("path", config.path);
            o.put("title", config.title);
            prefs(context).edit().putString(key(appWidgetId), o.toString()).apply();
        } catch (Exception e) {
            Log.w(TAG, "save failed: " + e.getMessage());
        }
    }

    static void delete(Context context, int appWidgetId) {
        prefs(context).edit().remove(key(appWidgetId)).apply();
    }

    static List<Integer> allIds(Context context) {
        List<Integer> out = new ArrayList<>();
        Map<String, ?> all = prefs(context).getAll();
        for (String k : all.keySet()) {
            if (k.startsWith("w_")) {
                try {
                    out.add(Integer.parseInt(k.substring(2)));
                } catch (NumberFormatException ignored) {
                }
            }
        }
        return out;
    }

    private static String key(int appWidgetId) {
        return "w_" + appWidgetId;
    }
}
