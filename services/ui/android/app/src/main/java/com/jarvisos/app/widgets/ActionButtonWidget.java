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

    /**
     * Chip colour configured on the card. The tint belongs to the tile, not to
     * the icon: the icon now takes the leftover height, so a colour behind it
     * would paint a slab over most of the card.
     */
    private static int tileDrawable(int iconBg) {
        if (iconBg == 0) return R.drawable.widget_transparent_bg;
        if (iconBg == 0x331E293B) return R.drawable.widget_tile_slate;
        if (iconBg == 0x660F172A) return R.drawable.widget_tile_navy;
        if (iconBg == 0x4D4ADE80) return R.drawable.widget_tile_green;
        if (iconBg == 0x4D863BFF) return R.drawable.widget_tile_purple;
        if (iconBg == 0x4D38BDF8) return R.drawable.widget_tile_sky;
        return R.drawable.widget_card_bg;
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
            state.isEmpty() ? label : label + " — " + state);
    }

    static RemoteViews build(Context context, int appWidgetId) {
        RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.widget_action_button);
        ActionButtonConfig cfg = ActionButtonConfig.load(context, appWidgetId);
        if (cfg == null) {
            setLabel(views, "Configure", "Tap to set up");
            setState(views, "Tap to set up", 0xFF94A3B8);
            views.setImageViewResource(R.id.action_icon, MaterialIcons.drawableRes("settings"));
            views.setInt(R.id.action_root, "setBackgroundResource", R.drawable.widget_card_bg);
            views.setOnClickPendingIntent(R.id.action_root,
                WidgetUpdater.configureActionButtonPendingIntent(context, appWidgetId));
            return views;
        }

        views.setImageViewResource(R.id.action_icon, MaterialIcons.drawableRes(cfg.icon));
        // "None" means a fully transparent tile; a chip colour keeps the dark card.
        views.setInt(R.id.action_root, "setBackgroundResource", tileDrawable(cfg.iconBg));
        final String label = DeviceButtonWidget.truncate(cfg.displayLabel(null), 18);
        setLabel(views, label, "");
        setState(views, "", 0xFF94A3B8);

        try {
            if (WidgetApi.apiKey(context) == null) {
                setState(views, "Sign in", 0xFF94A3B8);
                setLabel(views, label, "Sign in");
                views.setOnClickPendingIntent(R.id.action_root, WidgetUpdater.openAppPendingIntent(context));
                return views;
            }
            List<String> ids = java.util.Collections.singletonList(cfg.entityId);
            JSONObject states = WidgetApi.entityStates(context, ids);
            JSONObject e = states.optJSONObject(cfg.entityId);
            String state = e != null ? e.optString("state", "") : "";
            setState(views, state,
                WidgetApi.isActiveState(state) ? 0xFF4ADE80 : 0xFF94A3B8);
            setLabel(views, label, state);

            String resolvedState = e != null ? e.optString("state", "off") : "off";
            String service = WidgetApi.resolveService(cfg.service, cfg.domain(), resolvedState);
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
            String message = ex.getMessage() != null
                ? DeviceButtonWidget.truncate(ex.getMessage(), 20) : "offline";
            setState(views, message, 0xFFF87171);
            setLabel(views, label, message);
            views.setOnClickPendingIntent(R.id.action_root,
                WidgetUpdater.actionPendingIntent(context, appWidgetId, cfg.entityId, cfg.service));
        }
        return views;
    }
}
