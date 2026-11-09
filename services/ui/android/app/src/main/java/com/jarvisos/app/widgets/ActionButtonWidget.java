package com.jarvisos.app.widgets;

import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.Context;
import android.content.Intent;
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

    /** State line collapses when empty so it never reserves a blank band. */
    private static void setState(RemoteViews views, String text, int color) {
        String value = text == null ? "" : text.trim();
        views.setTextViewText(R.id.action_state, value);
        views.setTextColor(R.id.action_state, color);
        views.setViewVisibility(R.id.action_state,
            value.isEmpty() ? View.GONE : View.VISIBLE);
    }

    private static void setLabel(RemoteViews views, String label, String state) {
        views.setTextViewText(R.id.action_label, label);
        views.setContentDescription(R.id.action_root,
            state.isEmpty() ? label : label + ": " + state);
    }

    static RemoteViews build(Context context, int appWidgetId) {
        RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.widget_action_button);
        WidgetStyle st = WidgetStyle.of(context);
        ActionButtonConfig cfg = ActionButtonConfig.load(context, appWidgetId);
        paintCard(views, st, cfg == null ? 0 : cfg.iconBg);
        st.text(views, R.id.action_label, st.text);
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
     * The card: the theme surface, or the background the person picked when
     * configuring (kept for the transparent and colored choices).
     */
    /** The card is the user's Jarvis theme, like every other widget. */
    private static void paintCard(RemoteViews views, WidgetStyle st, int iconBg) {
        st.card(views, R.id.action_root);
    }

    /** The round button: accent-filled while on, a quiet tile while off. */
    private static void paintButton(RemoteViews views, WidgetStyle st, boolean on) {
        st.fill(views, R.id.action_icon, R.drawable.wg_circle, on ? st.accent : st.tile);
        st.icon(views, R.id.action_icon, on ? st.onAccent : st.text);
    }
}
