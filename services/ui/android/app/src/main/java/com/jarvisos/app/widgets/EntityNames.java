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
        String fn = text(entity, "friendly_name");
        if (!fn.isEmpty()) return fn;
        String id = text(entity, "entity_id");
        int dot = id.indexOf('.');
        return dot >= 0 ? id.substring(dot + 1).replace('_', ' ') : id;
    }

    /**
     * The trimmed string at {@code key}, or "" when it is missing or JSON null.
     *
     * <p>Not {@code optString}: Android's org.json turns a JSON {@code null}
     * into the four-letter string "null" (the desktop org.json used by the JVM
     * tests returns the fallback instead). The media status handler sends
     * {@code null} for every attribute a player does not report, so an idle
     * speaker printed "null" as its title or artist on the widget.
     */
    public static String text(JSONObject o, String key) {
        if (o == null || o.isNull(key)) return "";
        Object value = o.opt(key);
        return value == null ? "" : String.valueOf(value).trim();
    }
}