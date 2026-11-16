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

    /** The widget sizes (dp) a layout is made for: a 1x1 cell on small and large launchers up to 3x3. */
    private static final float[][] SIZES = {
        {40, 40}, {56, 64}, {64, 76}, {72, 88}, {84, 100}, {100, 120}, {124, 150}, {160, 190}, {200, 240},
    };

    static RemoteViews build(Context context, int appWidgetId) {
        String[] shown = new String[2];
        RemoteViews base = buildBase(context, appWidgetId, shown);
        // Before Android 12 there are no sizes: a typical 1x1 cell.
        WidgetFit fit = WidgetFit.of(base).fallback(v -> apply(v, ActionButtonFit.of(72, 88, shown[0], shown[1])));
        for (float[] size : SIZES) {
            ActionButtonFit f = ActionButtonFit.of(size[0], size[1], shown[0], shown[1]);
            fit.at(size[0], size[1], base, v -> apply(v, f));
        }
        return fit.build();
    }

    /** Size the button and show the name and state as {@code f} says. */
    private static void apply(RemoteViews v, ActionButtonFit f) {
        v.setViewVisibility(R.id.action_label, f.label ? View.VISIBLE : View.GONE);
        if (!f.state) v.setViewVisibility(R.id.action_state, View.GONE);
        if (f.smallText) v.setTextViewTextSize(R.id.action_label, android.util.TypedValue.COMPLEX_UNIT_SP, 10);
        v.setInt(R.id.action_label, "setMaxLines", Math.max(2, f.lines));
        int pad = px(f.glyphPad);
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
        paintCard(views, st, background);
        // On the bare wallpaper the name is white (with the layout's shadow); on a card, the theme's ink.
        st.text(views, R.id.action_label, background == 0 ? 0xFFFFFFFF : st.text);
        if (cfg == null) {
            setLabel(views, "Set up", "Tap to choose a device");
            setState(views, "Tap to choose", st.muted);
            views.setImageViewResource(R.id.action_icon, MaterialIcons.drawableRes("settings"));
            paintButton(views, st, false);
            views.setOnClickPendingIntent(R.id.action_root,
                WidgetUpdater.configureActionButtonPendingIntent(context, appWidgetId));
            return views;
        }
        views.setImageViewResource(R.id.action_icon, MaterialIcons.drawableRes(cfg.icon));
        final String label = cfg.displayLabel(null);
        setLabel(views, label, "");
        setState(views, "", st.muted);
        paintButton(views, st, false);
        try {
            if (WidgetApi.apiKey(context) == null) {
                setState(views, "Sign in", st.muted);
                setLabel(views, label, "Sign in");
                views.setOnClickPendingIntent(R.id.action_root, WidgetUpdater.openAppPendingIntent(context));
                return views;
            }
            JSONObject states = WidgetApi.entityStates(context, java.util.Collections.singletonList(cfg.entityId));
            JSONObject e = states.optJSONObject(cfg.entityId);
            String state = e != null ? EntityNames.text(e, "state") : "";
            boolean on = EntityStates.active(state);
            String shown = e == null ? "Not found" : EntityStates.pretty(cfg.domain(), state);
            setState(views, shown, on ? st.accent : st.muted);
            setLabel(views, label, shown);
            paintButton(views, st, on);
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

    /**
     * The card the person picked when configuring: "None" (0) is no card at
     * all, the button and its name on the wallpaper like an app icon; a colour
     * is a card of that colour.
     */
    private static void paintCard(RemoteViews views, WidgetStyle st, int iconBg) {
        if (iconBg == 0) {
            views.setInt(R.id.action_root, "setBackgroundResource", 0);
            return;
        }
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) {
            st.card(views, R.id.action_root); // no runtime tint before Android 12: the theme's card
            return;
        }
        views.setInt(R.id.action_root, "setBackgroundResource", R.drawable.wg_card_solid);
        views.setColorStateList(R.id.action_root, "setBackgroundTintList", android.content.res.ColorStateList.valueOf(iconBg));
    }

    /** The round button: accent-filled while on, a quiet tile while off. */
    private static void paintButton(RemoteViews views, WidgetStyle st, boolean on) {
        st.fill(views, R.id.action_icon, R.drawable.wg_circle, on ? st.accent : st.tile);
        st.icon(views, R.id.action_icon, on ? st.onAccent : st.text);
    }
}
