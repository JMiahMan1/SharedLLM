package com.jarvisos.app.widgets;

import org.json.JSONObject;

/**
 * How a widget names an entity.
 *
 * <p>Split out of {@link WidgetApi} so the naming rule has no Android imports
 * and can be exercised by JVM tests: {@link MediaStatus} needs it, and pulling
 * {@code WidgetApi} into a test drags TokenBridgePlugin and the whole Capacitor
 * plugin graph along with it.
 */
public final class EntityNames {

    private EntityNames() {}

    /**
     * The entity's friendly_name, falling back to a readable form of its id
     * ({@code media_player.kitchen_speaker} → {@code kitchen speaker}).
     */
    public static String friendlyName(JSONObject entity) {
        if (entity == null) return "";
        String fn = entity.optString("friendly_name");
        if (fn != null && !fn.isEmpty()) return fn;
        String id = entity.optString("entity_id", "");
        int dot = id.indexOf('.');
        return dot >= 0 ? id.substring(dot + 1).replace('_', ' ') : id;
    }
}