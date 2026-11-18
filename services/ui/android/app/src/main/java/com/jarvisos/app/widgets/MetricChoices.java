package com.jarvisos.app.widgets;

import java.util.ArrayList;
import java.util.Collections;
import java.util.Iterator;
import java.util.List;
import java.util.Locale;
import org.json.JSONObject;

/**
 * Which entities the metric widget can show: every sensor in the household's own
 * hub that reads a number. Nothing here names a device; the person picks one.
 * Pure, so the desktop JVM tests cover it.
 */
final class MetricChoices {
    private MetricChoices() {}

    /** One sensor the widget can show: its id, what people call it and a sample of its reading. */
    static final class Choice {
        final String entityId;
        final String name;
        final String reading;

        Choice(String entityId, String name, String reading) {
            this.entityId = entityId;
            this.name = name;
            this.reading = reading;
        }
    }

    /** A sensor whose state is a number (a temperature, a power reading, a battery level...). */
    static boolean isMetric(String entityId, JSONObject entity) {
        if (entityId == null || !entityId.startsWith("sensor.") || entity == null) return false;
        String state = entity.optString("state", "").trim();
        if (state.isEmpty()) return false;
        try {
            Double.parseDouble(state);
            return true;
        } catch (NumberFormatException e) {
            return false;
        }
    }

    /** The sensors in an entity-search reply ({@code {entity_id: entity}}), by name. */
    static List<Choice> from(JSONObject byId) {
        List<Choice> out = new ArrayList<>();
        if (byId == null) return out;
        Iterator<String> keys = byId.keys();
        while (keys.hasNext()) {
            String id = keys.next();
            JSONObject e = byId.optJSONObject(id);
            if (!isMetric(id, e)) continue;
            String name = EntityNames.text(e, "friendly_name");
            if (name.isEmpty()) name = id;
            String unit = EntityStates.field(e, e.optJSONObject("attributes"), "unit_of_measurement");
            String reading = EntityStates.number(e.optString("state", "")) + (unit.isEmpty() ? "" : " " + unit);
            out.add(new Choice(id, name, reading));
        }
        Collections.sort(out, (a, b) -> {
            int c = a.name.toLowerCase(Locale.ROOT).compareTo(b.name.toLowerCase(Locale.ROOT));
            return c != 0 ? c : a.entityId.compareTo(b.entityId);
        });
        return out;
    }
}
