package com.jarvisos.app.widgets;

import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.view.View;
import android.widget.RemoteViews;
import com.jarvisos.app.R;
import java.util.List;
import org.json.JSONObject;

/** Single-action home-screen button (e.g. open garage). */
public class ActionButtonWidget extends AppWidgetProvider {
    @Override
    public void onUpdate(Context context, AppWidgetManager mgr, int[] ids) {
        WidgetUpdater.pushActionButtons(context);
        WidgetAlarm.schedule(context);
    }

    @Override
    public void onReceive(Context context, Intent intent) {
        super.onReceive(context, intent);
        if (AppWidgetManager.ACTION_APPWIDGET_UPDATE.equals(intent.getAction())) {
            WidgetUpdater.pushActionButtons(context);
        }
    }

    @Override
    public void onDeleted(Context context, int[] appWidgetIds) {
        if (appWidgetIds == null) return;
        for (int id : appWidgetIds) ActionButtonConfig.delete(context, id);
        super.onDeleted(context, appWidgetIds);
    }

    /** The name and state last set while building, for sizing (one build at a time per thread). */
    private static final ThreadLocal<String[]> SHOWN = new ThreadLocal<>();

    /** State line collapses when empty so it never reserves a blank band. */
    private static void setState(RemoteViews views, String text, int color) {
        String value = text == null ? "" : text.trim();
        String[] shown = SHOWN.get();
        if (shown != null) shown[1] = value;
        views.setTextViewText(R.id.action_state, value);
        views.setTextColor(R.id.action_state, color);
        views.setViewVisibility(R.id.action_state,
            value.isEmpty() ? View.GONE : View.VISIBLE);
    }

    private static void setLabel(RemoteViews views, String label, String state) {
        String[] shown = SHOWN.get();
        if (shown != null) shown[0] = label;
        views.setTextViewText(R.id.action_label, label);
        views.setContentDescription(R.id.action_root,
            state.isEmpty() ? label : label + ": " + state);
    }

    /**
     * The widget sizes (dp) a layout is made for. Launchers pick the largest
     * that fits, so one-cell sizes are close together (the Pixel launcher's
     * 1x1 is 82x96, One UI's about 80x100): a coarse step left a third of the
     * cell unused. Android allows at most 16.
     */
    private static final float[][] SIZES = {
        {40, 40}, {56, 64}, {64, 76}, {72, 88}, {76, 90}, {80, 94}, {82, 96}, {86, 100}, {90, 106},
        {96, 114}, {100, 120}, {124, 150}, {160, 190}, {200, 240},
    };

    static RemoteViews build(Context context, int appWidgetId) {
        String[] shown = new String[2];
        RemoteViews base = buildBase(context, appWidgetId, shown);
        ActionButtonConfig cfg = ActionButtonConfig.load(context, appWidgetId);
        final boolean tile = cfg != null && cfg.iconBg != 0;
        // Before Android 12 there are no sizes: a typical 1x1 cell.
        WidgetFit fit = WidgetFit.of(base).fallback(v -> apply(v, ActionButtonFit.of(72, 88, shown[0], shown[1]), tile));
        for (float[] size : SIZES) {
            ActionButtonFit f = ActionButtonFit.of(size[0], size[1], shown[0], shown[1]);
            fit.at(size[0], size[1], base, v -> apply(v, f, tile));
        }
        return fit.build();
    }

    /** Size the icon and show the name and state as {@code f} says. */
    private static void apply(RemoteViews v, ActionButtonFit f, boolean tile) {
        v.setViewVisibility(R.id.action_label, f.label ? View.VISIBLE : View.GONE);
        if (!f.state) v.setViewVisibility(R.id.action_state, View.GONE);
        if (f.smallText) v.setTextViewTextSize(R.id.action_label, android.util.TypedValue.COMPLEX_UNIT_SP, 10);
        v.setInt(R.id.action_label, "setMaxLines", Math.max(2, f.lines));
        int pad = px(tile ? f.glyphPad : f.bareGlyphPad);
        v.setViewPadding(R.id.action_icon, pad, pad, pad, pad);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            WidgetFit.sizeDp(v, R.id.action_button, f.button, f.button);
        }
    }

    private static int px(float dp) {
        return Math.round(dp * android.content.res.Resources.getSystem().getDisplayMetrics().density);
    }

    /** The views at the default size; {@code shown} gets the name and state text the sizes are worked out from. */
    static RemoteViews buildBase(Context context, int appWidgetId, String[] shown) {
        SHOWN.set(shown);
        try {
            return fill(context, appWidgetId);
        } finally {
            SHOWN.remove();
        }
    }

    private static RemoteViews fill(Context context, int appWidgetId) {
        RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.widget_action_button);
        WidgetStyle st = WidgetStyle.of(context);
        ActionButtonConfig cfg = ActionButtonConfig.load(context, appWidgetId);
        int background = cfg == null ? 0 : cfg.iconBg;
        paintTile(views, st, background);
        // The name sits on the wallpaper like an app's: white with the layout's shadow.
        st.text(views, R.id.action_label, 0xFFFFFFFF);
        if (cfg == null) {
            setLabel(views, "Set up", "Tap to choose a device");
            setState(views, "Tap to choose", STATE_TEXT);
            paintGlyph(context, views, st, "settings", background, false);
            views.setOnClickPendingIntent(R.id.action_root,
                WidgetUpdater.configureActionButtonPendingIntent(context, appWidgetId));
            return views;
        }
        final String label = cfg.displayLabel(null);
        setLabel(views, label, "");
        setState(views, "", STATE_TEXT);
        paintGlyph(context, views, st, cfg.icon, background, false);
        try {
            if (WidgetApi.apiKey(context) == null) {
                setState(views, "Sign in", STATE_TEXT);
                setLabel(views, label, "Sign in");
                views.setOnClickPendingIntent(R.id.action_root, WidgetUpdater.openAppPendingIntent(context));
                return views;
            }
            JSONObject states = WidgetApi.entityStates(context, java.util.Collections.singletonList(cfg.entityId));
            JSONObject e = states.optJSONObject(cfg.entityId);
            String state = e != null ? EntityNames.text(e, "state") : "";
            boolean on = EntityStates.active(state);
            String shown = e == null ? "Not found" : EntityStates.pretty(cfg.domain(), state);
            setState(views, shown, on ? st.accent : STATE_TEXT);
            setLabel(views, label, shown);
            paintGlyph(context, views, st, cfg.icon, background, on);
            String resolvedState = e != null ? EntityNames.text(e, "state") : "off";
            String service = WidgetApi.resolveService(cfg.service, cfg.domain(), resolvedState.isEmpty() ? "off" : resolvedState);
            android.app.PendingIntent pi;
            boolean wouldOpenGarage = cfg.entityId.toLowerCase().contains("garage")
                && ("turn_on".equals(service) || "open_cover".equals(service))
                && WidgetApi.isAwayFromHome(context, WidgetUpdater.AWAY_THRESHOLD_M);
            if (wouldOpenGarage) {
                pi = WidgetUpdater.confirmGaragePendingIntent(context, cfg.entityId, service);
            } else {
                pi = WidgetUpdater.actionPendingIntent(context, appWidgetId, cfg.entityId, service);
            }
            views.setOnClickPendingIntent(R.id.action_root, pi);
        } catch (Exception ex) {
            String message = ex.getMessage() != null ? DeviceButtonWidget.truncate(ex.getMessage(), 20) : "Offline";
            setState(views, message, st.accent);
            setLabel(views, label, message);
            views.setOnClickPendingIntent(R.id.action_root,
                WidgetUpdater.actionPendingIntent(context, appWidgetId, cfg.entityId, cfg.service));
        }
        return views;
    }

    /** The state line on the wallpaper: white, a little softer than the name. */
    private static final int STATE_TEXT = 0xE6FFFFFF;

    /**
     * The tile behind the glyph: none for "None" (0), else a rounded square in
     * the chosen colour, like a launcher icon. The widget itself draws nothing.
     */
    private static void paintTile(RemoteViews views, WidgetStyle st, int iconBg) {
        views.setInt(R.id.action_root, "setBackgroundResource", 0);
        if (iconBg == 0) {
            views.setInt(R.id.action_button, "setBackgroundResource", 0);
            return;
        }
        views.setInt(R.id.action_button, "setBackgroundResource", R.drawable.wg_icon_tile);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            views.setColorStateList(R.id.action_button, "setBackgroundTintList",
                    android.content.res.ColorStateList.valueOf(iconBg));
        } else {
            st.fill(views, R.id.action_button, R.drawable.wg_tile, st.tile);
        }
    }

    /**
     * The glyph: white while off and the theme's accent while on. It is always
     * drawn as a bitmap centred on what the icon actually draws, because many
     * Material icons do not sit in the middle of their own 24dp box (the garage
     * sat 6% low in its tile). On the bare wallpaper it also gets a soft shadow,
     * as launchers give icon labels, since a white glyph alone vanishes on a light
     * wallpaper and RemoteViews cannot give an ImageView a shadow.
     */
    private static void paintGlyph(Context context, RemoteViews views, WidgetStyle st, String icon, int iconBg, boolean on) {
        int color = on ? st.accent : 0xFFFFFFFF;
        android.graphics.Bitmap glyph = centredGlyph(context, icon, color, iconBg == 0);
        if (glyph == null) {
            views.setImageViewResource(R.id.action_icon, MaterialIcons.drawableRes(icon));
            st.icon(views, R.id.action_icon, color);
            return;
        }
        views.setInt(R.id.action_icon, "setColorFilter", 0);
        views.setImageViewBitmap(R.id.action_icon, glyph);
    }

    /** The bitmap side, in px; fitCenter scales it to the icon's size. */
    private static final int GLYPH_PX = 192;

    /**
     * ``icon`` in ``color``, scaled to fill a square (less a margin for the
     * shadow) and centred on its drawn pixels, not on its artwork box.
     */
    static android.graphics.Bitmap centredGlyph(Context context, String icon, int color, boolean shadowed) {
        android.graphics.drawable.Drawable d = MaterialIcons.drawable(context, icon);
        if (d == null) return null;
        d = d.mutate();
        d.setTint(color);
        // 1. Draw the icon as it comes, then find what it really covers.
        android.graphics.Bitmap raw = android.graphics.Bitmap.createBitmap(GLYPH_PX, GLYPH_PX, android.graphics.Bitmap.Config.ARGB_8888);
        d.setBounds(0, 0, GLYPH_PX, GLYPH_PX);
        d.draw(new android.graphics.Canvas(raw));
        android.graphics.Rect ink = GlyphBounds.of(raw);
        if (ink == null) {
            raw.recycle();
            return null;
        }
        // 2. Redraw that ink, centred and as large as fits, leaving room for a shadow.
        int margin = shadowed ? Math.round(GLYPH_PX * 0.08f) : Math.round(GLYPH_PX * 0.02f);
        android.graphics.RectF box = GlyphBounds.fit(ink.width(), ink.height(), GLYPH_PX, margin);
        android.graphics.Bitmap glyph = android.graphics.Bitmap.createBitmap(GLYPH_PX, GLYPH_PX, android.graphics.Bitmap.Config.ARGB_8888);
        android.graphics.Paint smooth = new android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG | android.graphics.Paint.FILTER_BITMAP_FLAG);
        new android.graphics.Canvas(glyph).drawBitmap(raw, ink, box, smooth);
        raw.recycle();
        if (!shadowed) return glyph;
        // 3. A soft shadow under it, a little below.
        android.graphics.Paint blur = new android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG);
        blur.setMaskFilter(new android.graphics.BlurMaskFilter(GLYPH_PX * 0.035f, android.graphics.BlurMaskFilter.Blur.NORMAL));
        int[] offset = new int[2];
        android.graphics.Bitmap alpha = glyph.extractAlpha(blur, offset);
        android.graphics.Bitmap out = android.graphics.Bitmap.createBitmap(GLYPH_PX, GLYPH_PX, android.graphics.Bitmap.Config.ARGB_8888);
        android.graphics.Canvas oc = new android.graphics.Canvas(out);
        android.graphics.Paint shadow = new android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG);
        shadow.setColor(0x80000000);
        oc.drawBitmap(alpha, offset[0], offset[1] + GLYPH_PX * 0.012f, shadow);
        oc.drawBitmap(glyph, 0, 0, null);
        alpha.recycle();
        glyph.recycle();
        return out;
    }
}
