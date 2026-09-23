package com.jarvisos.app.widgets;

import android.content.Context;
import android.content.SharedPreferences;
import android.util.Log;
import java.util.ArrayList;
import java.util.List;
import org.json.JSONArray;

/** Per-appWidgetId entity pins for the 2×2 Dashboard home-screen widget. */
public final class DashboardConfig {
    private static final String TAG = "DashboardConfig";
    private static final String PREFS = "jarvis_dashboard_widgets";
    public static final int MAX_CELLS = 4;

    private DashboardConfig() {}

    private static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    public static List<String> loadCells(Context context, int appWidgetId) {
        List<String> out = new ArrayList<>();
        try {
            String raw = prefs(context).getString(key(appWidgetId), null);
            if (raw == null || raw.isEmpty()) return out;
            JSONArray arr = new JSONArray(raw);
            for (int i = 0; i < arr.length() && out.size() < MAX_CELLS; i++) {
                String id = arr.optString(i, "");
                if (!id.isEmpty()) out.add(id);
            }
        } catch (Exception e) {
            Log.w(TAG, "loadCells failed: " + e.getMessage());
        }
        return out;
    }

    public static void saveCells(Context context, int appWidgetId, List<String> cells) {
        try {
            JSONArray arr = new JSONArray();
            if (cells != null) {
                for (String id : cells) {
                    if (id != null && !id.isEmpty()) arr.put(id);
                }
            }
            prefs(context).edit().putString(key(appWidgetId), arr.toString()).apply();
        } catch (Exception e) {
            Log.w(TAG, "saveCells failed: " + e.getMessage());
        }
    }

    public static void delete(Context context, int appWidgetId) {
        prefs(context).edit().remove(key(appWidgetId)).apply();
    }

    private static String key(int appWidgetId) {
        return "w_" + appWidgetId;
    }
}
