package com.jarvisos.app.widgets;

import android.content.Context;
import android.content.res.ColorStateList;
import android.os.Build;
import android.widget.RemoteViews;
import com.jarvisos.app.JarvisTheme;
import com.jarvisos.app.R;

/**
 * One look for every Jarvis widget, painted in the user's selected Jarvis
 * theme (the same one the app and the lock screen use).
 *
 * <p>Android 12+ tints the card, tiles, pills and bars at runtime; older
 * Android swaps between the dark and light drawables. Text and icon colors are
 * set on every version.
 */
public final class WidgetStyle {


    public final JarvisTheme theme;
    /** Card fill (the theme surface, nearly opaque). */
    public final int card;
    /** A tile inside the card. */
    public final int tile;
    /** A tile carrying the accent (selected, active). */
    public final int tileAccent;
    public final int text;
    public final int muted;
    /** Faint text: captions, separators. */
    public final int faint;
    public final int accent;
    public final int accentAlt;
    public final int onAccent;
    /** The theme's secondary roles: accentAlt, ring2 and ring3 (second, third, fourth color). */
    public final int second;
    public final int third;
    public final int fourth;
    /** Progress bars and rings, and the track behind them. */
    public final int progress;
    public final int track;

    private WidgetStyle(JarvisTheme t) {
        this.theme = t;
        this.card = JarvisTheme.withAlpha(t.surface, 0.96f);
        this.tile = JarvisTheme.blend(t.surface, t.text, t.dark ? 0.07f : 0.05f);
        this.tileAccent = JarvisTheme.blend(t.surface, t.accent, t.dark ? 0.20f : 0.12f);
        this.text = t.text;
        this.muted = t.textMuted;
        this.faint = JarvisTheme.withAlpha(t.textMuted, 0.75f);
        this.accent = t.accent;
        this.accentAlt = t.accentAlt;
        this.onAccent = t.onAccent;
        this.second = t.accentAlt;
        this.third = t.ring2;
        this.fourth = t.ring3;
        this.progress = t.progress;
        // The theme's track, made visible on the card when it matches it.
        this.track = Math.abs(JarvisTheme.luminance(t.progressTrack) - JarvisTheme.luminance(t.surface)) < 0.04
                ? JarvisTheme.blend(t.surface, t.text, 0.16f) : t.progressTrack;
    }

    public static WidgetStyle of(Context context) {
        return new WidgetStyle(JarvisTheme.resolve(context));
    }

    private static boolean tintable() {
        return Build.VERSION.SDK_INT >= Build.VERSION_CODES.S;
    }

    /** The card behind the whole widget. */
    public void card(RemoteViews v, int id) {
        if (tintable()) {
            v.setInt(id, "setBackgroundResource", R.drawable.wg_card_solid);
            v.setColorStateList(id, "setBackgroundTintList", ColorStateList.valueOf(card));
        } else {
            v.setInt(id, "setBackgroundResource", theme.dark ? R.drawable.wg_card : R.drawable.wg_card_light);
        }
    }

    /** A rounded tile inside the card; {@code active} uses the accent wash. */
    public void tile(RemoteViews v, int id, boolean active) {
        if (tintable()) {
            v.setInt(id, "setBackgroundResource", R.drawable.wg_tile_solid);
            v.setColorStateList(id, "setBackgroundTintList", ColorStateList.valueOf(active ? tileAccent : tile));
        } else {
            v.setInt(id, "setBackgroundResource", theme.dark ? R.drawable.wg_tile : R.drawable.wg_tile_light);
        }
    }

    /** A tile washed in any color (a status: playing, open, locked). */
    public void tileTint(RemoteViews v, int id, int color) {
        if (tintable()) {
            v.setInt(id, "setBackgroundResource", R.drawable.wg_tile_solid);
            v.setColorStateList(id, "setBackgroundTintList",
                    ColorStateList.valueOf(JarvisTheme.blend(theme.surface, color, theme.dark ? 0.24f : 0.14f)));
        } else {
            tile(v, id, true);
        }
    }

    /** A filled pill or circle (buttons, chips). */
    public void fill(RemoteViews v, int id, int drawable, int color) {
        if (tintable()) {
            int solid = drawable == R.drawable.wg_circle ? R.drawable.wg_circle_solid
                    : drawable == R.drawable.wg_pill ? R.drawable.wg_pill_solid
                    : drawable == R.drawable.wg_tile ? R.drawable.wg_tile_solid : drawable;
            v.setInt(id, "setBackgroundResource", solid);
            v.setColorStateList(id, "setBackgroundTintList", ColorStateList.valueOf(color));
        } else {
            v.setInt(id, "setBackgroundResource", drawable);
        }
    }

    public void text(RemoteViews v, int id, int color) {
        v.setTextColor(id, color);
    }

    /** Tint a monochrome icon. */
    public void icon(RemoteViews v, int id, int color) {
        v.setInt(id, "setColorFilter", color);
    }

    /** A horizontal bar: value of max, in {@code color}. */
    public void progress(RemoteViews v, int id, int max, int value, int color) {
        v.setProgressBar(id, Math.max(1, max), Math.max(0, Math.min(value, max)), false);
        if (tintable()) {
            v.setColorStateList(id, "setProgressTintList", ColorStateList.valueOf(color));
            v.setColorStateList(id, "setProgressBackgroundTintList", ColorStateList.valueOf(track));
        }
    }

    /** A stat tile: glyph, small label, big value and caption, all in theme colors. */
    public void stat(RemoteViews v, int tileId, int iconId, int labelId, int valueId, int captionId,
                     int glyphColor, boolean active) {
        tile(v, tileId, active);
        icon(v, iconId, glyphColor);
        text(v, labelId, muted);
        text(v, valueId, text);
        text(v, captionId, muted);
    }

    /** The status line under a widget: hidden when there is nothing to say. */
    public void status(RemoteViews v, int id, String message) {
        boolean show = message != null && !message.isEmpty();
        v.setViewVisibility(id, show ? android.view.View.VISIBLE : android.view.View.GONE);
        v.setTextViewText(id, show ? message : "");
        text(v, id, faint);
    }

    /**
     * The standard header: card, tinted glyph, title and the refresh button.
     * Every widget layout names these four views the same way.
     */
    public void header(RemoteViews v, int rootId, int iconId, int titleId, int refreshId, int glyphColor) {
        card(v, rootId);
        if (iconId != 0) icon(v, iconId, glyphColor);
        if (titleId != 0) text(v, titleId, muted);
        if (refreshId != 0) icon(v, refreshId, faint);
    }
}
