package com.jarvisos.app.widgets;

import android.content.Context;
import android.content.SharedPreferences;
import android.util.Log;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.json.JSONObject;

/** Per-appWidgetId configuration for single-action home-screen buttons. */
public final class ActionButtonConfig {
    private static final String TAG = "ActionButtonConfig";
    private static final String PREFS = "jarvis_action_buttons";

    public final String entityId;
    public final String service;
    public final String label;
    public final String icon;

    public ActionButtonConfig(String entityId, String service, String label, String icon) {
        this.entityId = entityId;
        this.service = service;
        this.label = label;
        this.icon = icon;
    }

    private static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    public static ActionButtonConfig load(Context context, int appWidgetId) {
        String raw = prefs(context).getString(key(appWidgetId), null);
        if (raw == null || raw.isEmpty()) return null;
        try {
            JSONObject o = new JSONObject(raw);
            String entity = o.optString("entity_id", "");
            if (entity.isEmpty()) return null;
            return new ActionButtonConfig(
                entity,
                o.optString("service", "turn_on"),
                o.optString("label", ""),
                o.optString("icon", "garage")
            );
        } catch (Exception e) {
            Log.w(TAG, "load failed for " + appWidgetId + ": " + e.getMessage());
            return null;
        }
    }

    public static void save(Context context, int appWidgetId, ActionButtonConfig config) {
        try {
            JSONObject o = new JSONObject();
            o.put("entity_id", config.entityId);
            o.put("service", config.service);
            o.put("label", config.label != null ? config.label : "");
            o.put("icon", config.icon != null ? config.icon : "garage");
            prefs(context).edit().putString(key(appWidgetId), o.toString()).apply();
        } catch (Exception e) {
            Log.w(TAG, "save failed: " + e.getMessage());
        }
    }

    public static void delete(Context context, int appWidgetId) {
        prefs(context).edit().remove(key(appWidgetId)).apply();
    }

    public static List<Integer> allIds(Context context) {
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

    public String displayLabel(String fallbackFriendly) {
        if (label != null && !label.trim().isEmpty()) return label.trim();
        if (fallbackFriendly != null && !fallbackFriendly.trim().isEmpty()) return fallbackFriendly.trim();
        int dot = entityId.indexOf('.');
        return dot >= 0 ? entityId.substring(dot + 1).replace('_', ' ') : entityId;
    }

    public String domain() {
        int dot = entityId.indexOf('.');
        return dot > 0 ? entityId.substring(0, dot) : "";
    }

    public boolean isGarageAction() {
        String e = entityId.toLowerCase();
        boolean garage = e.contains("garage");
        boolean opens = "turn_on".equals(service)
            || "open_cover".equals(service)
            || "toggle".equals(service);
        return garage && opens;
    }

    private static String key(int appWidgetId) {
        return "w_" + appWidgetId;
    }

    /** Ordered map of domain → available HA services for the picker. */
    public static Map<String, String> servicesForDomain(String domain) {
        Map<String, String> m = new LinkedHashMap<>();
        if ("cover".equals(domain)) {
            m.put("open_cover", "Open");
            m.put("close_cover", "Close");
            m.put("toggle", "Toggle");
        } else if ("lock".equals(domain)) {
            m.put("unlock", "Unlock");
            m.put("lock", "Lock");
        } else if ("button".equals(domain) || "input_button".equals(domain)) {
            m.put("press", "Press");
        } else if ("scene".equals(domain)) {
            m.put("turn_on", "Activate");
        } else if ("climate".equals(domain)) {
            m.put("turn_on", "Turn on");
            m.put("turn_off", "Turn off");
        } else {
            m.put("turn_on", "Turn on / Open");
            m.put("turn_off", "Turn off / Close");
            if ("light".equals(domain) || "switch".equals(domain)
                || "fan".equals(domain) || "media_player".equals(domain)) {
                m.put("toggle", "Toggle");
            }
        }
        return m;
    }
}
