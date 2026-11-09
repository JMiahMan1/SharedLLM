package com.jarvisos.app.widgets;

import com.jarvisos.app.R;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.TimeZone;
import org.json.JSONObject;

/**
 * How a Home Assistant entity reads on a widget: a tidy number, a human state
 * ("Open", "Locked") instead of HA's raw words, how long ago it changed, and
 * an icon for its kind. Pure (no Android views), so it is tested on the JVM.
 */
public final class EntityStates {

    private EntityStates() {}

    /**
     * A field the entity search returns at the top level (device_class,
     * unit_of_measurement), falling back to the HA attributes for other shapes.
     */
    public static String field(JSONObject entity, JSONObject attrs, String key) {
        String v = entity == null ? "" : EntityNames.text(entity, key);
        if (v.isEmpty() && attrs != null) v = EntityNames.text(attrs, key);
        return v;
    }

    /** "72.456" → "72.5", "1500.0" → "1,500"; anything not numeric comes back as is. */
    public static String number(String state) {
        if (state == null) return "-";
        try {
            double v = Double.parseDouble(state.trim());
            if (Math.abs(v) >= 100 || v == Math.rint(v)) {
                return java.text.NumberFormat.getIntegerInstance(Locale.US).format(Math.round(v));
            }
            return String.format(Locale.US, "%.1f", v);
        } catch (NumberFormatException e) {
            return pretty(null, state);
        }
    }

    /** HA's state words as people say them. */
    public static String pretty(String domain, String state) {
        if (state == null || state.isEmpty()) return "-";
        String s = state.toLowerCase(Locale.ROOT);
        switch (s) {
            case "on": return "lock".equals(domain) ? "Unlocked" : "On";
            case "off": return "Off";
            case "open": case "opening": return s.equals("open") ? "Open" : "Opening";
            case "closed": case "closing": return s.equals("closed") ? "Closed" : "Closing";
            case "locked": return "Locked";
            case "unlocked": return "Unlocked";
            case "home": return "Home";
            case "not_home": return "Away";
            case "playing": return "Playing";
            case "paused": return "Paused";
            case "idle": return "Idle";
            case "unavailable": return "Unavailable";
            case "unknown": return "Unknown";
            case "heat": return "Heating";
            case "cool": return "Cooling";
            case "auto": return "Auto";
            case "cleaning": return "Cleaning";
            case "docked": return "Docked";
            default:
                return Character.toUpperCase(state.charAt(0)) + state.substring(1).replace('_', ' ');
        }
    }

    /** "Updated 5 min ago" from last_changed / last_updated (ISO, UTC); "" when unknown. */
    public static String updated(JSONObject entity) {
        String iso = EntityNames.text(entity, "last_changed");
        if (iso.isEmpty()) iso = EntityNames.text(entity, "last_updated");
        long at = parseIso(iso);
        if (at <= 0) return "";
        return "Updated " + ago(System.currentTimeMillis() - at);
    }

    static String ago(long ms) {
        long min = Math.max(0, ms / 60000);
        if (min < 1) return "just now";
        if (min < 60) return min + " min ago";
        long h = min / 60;
        if (h < 24) return h + (h == 1 ? " hour ago" : " hours ago");
        long d = h / 24;
        return d + (d == 1 ? " day ago" : " days ago");
    }

    static long parseIso(String iso) {
        if (iso == null || iso.length() < 19) return 0;
        try {
            SimpleDateFormat f = new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.US);
            f.setTimeZone(TimeZone.getTimeZone("UTC"));
            Date d = f.parse(iso.substring(0, 19));
            long t = d == null ? 0 : d.getTime();
            // An explicit offset (+02:00) moves it back to UTC.
            String tail = iso.substring(19);
            int sign = tail.indexOf('+') >= 0 ? tail.indexOf('+') : tail.lastIndexOf('-');
            if (sign >= 0 && tail.length() >= sign + 6) {
                int hh = Integer.parseInt(tail.substring(sign + 1, sign + 3));
                int mm = Integer.parseInt(tail.substring(sign + 4, sign + 6));
                long off = (hh * 60L + mm) * 60000L;
                t += tail.charAt(sign) == '+' ? -off : off;
            }
            return t;
        } catch (Exception e) {
            return 0;
        }
    }

    /** True when the entity is in its "on / open / unlocked / playing" state. */
    public static boolean active(String state) {
        return WidgetApi.isActiveState(state);
    }

    /** An icon for a sensor by its device class or unit. */
    public static int sensorIcon(String deviceClass, String unit) {
        String dc = deviceClass == null ? "" : deviceClass;
        String u = unit == null ? "" : unit;
        if (dc.equals("temperature") || u.contains("°")) return R.drawable.mi_thermostat;
        if (dc.equals("humidity") || dc.equals("moisture")) return R.drawable.mi_drop;
        if (dc.equals("power") || dc.equals("energy") || dc.equals("voltage") || dc.equals("current")
                || u.equals("W") || u.equals("kWh")) return R.drawable.mi_bolt;
        if (dc.equals("battery")) return R.drawable.mi_power;
        if (dc.equals("illuminance")) return R.drawable.mi_light_mode;
        return R.drawable.mi_sensors;
    }

    /** An icon for an entity by its domain, device class and name. */
    public static int icon(String entityId, String deviceClass, String state) {
        String id = entityId == null ? "" : entityId.toLowerCase(Locale.ROOT);
        String domain = id.contains(".") ? id.substring(0, id.indexOf('.')) : "";
        String dc = deviceClass == null ? "" : deviceClass;
        boolean on = active(state);
        if (id.contains("garage") || dc.equals("garage") || dc.equals("garage_door")) {
            return on ? R.drawable.mi_garage_open : R.drawable.mi_garage;
        }
        switch (domain) {
            case "light": return R.drawable.mi_lightbulb;
            case "switch": case "input_boolean": return R.drawable.mi_power;
            case "lock": return on ? R.drawable.mi_lock_open : R.drawable.mi_lock;
            case "cover": return R.drawable.mi_curtains;
            case "fan": return R.drawable.mi_fan;
            case "climate": return R.drawable.mi_thermostat;
            case "media_player": return R.drawable.mi_speaker;
            case "vacuum": return R.drawable.mi_vacuum;
            case "camera": return R.drawable.mi_camera;
            case "person": case "device_tracker": return R.drawable.mi_place;
            case "binary_sensor":
                if (dc.equals("door") || dc.equals("opening") || dc.equals("window")) return R.drawable.mi_home;
                if (dc.equals("motion") || dc.equals("occupancy")) return R.drawable.mi_sensor;
                return R.drawable.mi_shield;
            case "sensor": return sensorIcon(dc, "");
            default: return R.drawable.mi_home;
        }
    }

    /** Whether a tap can toggle it from the home screen. */
    public static boolean toggleable(String entityId) {
        String d = entityId == null || !entityId.contains(".") ? "" : entityId.substring(0, entityId.indexOf('.'));
        return d.equals("light") || d.equals("switch") || d.equals("cover") || d.equals("lock")
                || d.equals("fan") || d.equals("input_boolean") || d.equals("media_player");
    }
}
