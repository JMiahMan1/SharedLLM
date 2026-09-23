package com.jarvisos.app.widgets;

import android.content.Context;
import android.content.res.Resources;
import android.graphics.drawable.Drawable;
import android.util.TypedValue;
import androidx.core.content.ContextCompat;
import com.jarvisos.app.R;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Curated Material icon set for action-button widgets.
 * Names match drawable resource suffixes (mi_*).
 */
public final class MaterialIcons {
    private static final Map<String, Integer> ICONS = new LinkedHashMap<>();

    static {
        ICONS.put("garage", R.drawable.mi_garage);
        ICONS.put("garage_closed", R.drawable.mi_garage_closed);
        ICONS.put("garage_open", R.drawable.mi_garage_open);
        ICONS.put("car", R.drawable.mi_car);
        ICONS.put("lightbulb", R.drawable.mi_lightbulb);
        ICONS.put("power", R.drawable.mi_power);
        ICONS.put("lock", R.drawable.mi_lock);
        ICONS.put("lock_open", R.drawable.mi_lock_open);
        ICONS.put("home", R.drawable.mi_home);
        ICONS.put("camera", R.drawable.mi_camera);
        ICONS.put("tv", R.drawable.mi_tv);
        ICONS.put("volume_up", R.drawable.mi_volume_up);
        ICONS.put("fan", R.drawable.mi_fan);
        ICONS.put("thermostat", R.drawable.mi_thermostat);
        ICONS.put("bolt", R.drawable.mi_bolt);
        ICONS.put("shield", R.drawable.mi_shield);
        ICONS.put("key", R.drawable.mi_key);
        ICONS.put("curtains", R.drawable.mi_curtains);
        ICONS.put("vacuum", R.drawable.mi_vacuum);
        ICONS.put("speaker", R.drawable.mi_speaker);
        ICONS.put("router", R.drawable.mi_router);
        ICONS.put("drop", R.drawable.mi_drop);
        ICONS.put("thermostat_ac", R.drawable.mi_ac);
        ICONS.put("light_mode", R.drawable.mi_light_mode);
        ICONS.put("dark_mode", R.drawable.mi_dark_mode);
        ICONS.put("notifications", R.drawable.mi_notifications);
        ICONS.put("security", R.drawable.mi_security);
        ICONS.put("settings", R.drawable.mi_settings);
        ICONS.put("open_in_full", R.drawable.mi_open_in_full);
        ICONS.put("close_fullscreen", R.drawable.mi_close_fullscreen);
        ICONS.put("play", R.drawable.mi_play);
        ICONS.put("pause", R.drawable.mi_pause);
        ICONS.put("music_note", R.drawable.mi_music_note);
        ICONS.put("radio", R.drawable.mi_radio);
        ICONS.put("nightlight", R.drawable.mi_nightlight);
        ICONS.put("bed", R.drawable.mi_bed);
        ICONS.put("kitchen", R.drawable.mi_kitchen);
        ICONS.put("local_laundry", R.drawable.mi_laundry);
        ICONS.put("evil", R.drawable.mi_sensor);
        ICONS.put("call", R.drawable.mi_call);
        ICONS.put("chat", R.drawable.mi_chat);
        ICONS.put("star", R.drawable.mi_star);
        ICONS.put("favorite", R.drawable.mi_favorite);
        ICONS.put("bookmark", R.drawable.mi_bookmark);
        ICONS.put("flag", R.drawable.mi_flag);
    }

    private MaterialIcons() {}

    public static String defaultIcon() {
        return "garage";
    }

    public static List<String> names() {
        return new ArrayList<>(ICONS.keySet());
    }

    public static int drawableRes(String name) {
        Integer id = ICONS.get(name);
        return id != null ? id : R.drawable.mi_garage;
    }

    public static Drawable drawable(Context context, String name) {
        return ContextCompat.getDrawable(context, drawableRes(name));
    }

    public static void applyTint(Drawable d, int color) {
        if (d == null) return;
        d.setTint(color);
    }

    /** dp → px for icon grid cells. */
    public static int dp(Resources res, float dp) {
        return Math.round(TypedValue.applyDimension(
            TypedValue.COMPLEX_UNIT_DIP, dp, res.getDisplayMetrics()));
    }
}
