package com.jarvisos.app.widgets;

import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.Context;
import android.content.Intent;
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

    static RemoteViews build(Context context, int appWidgetId) {
        RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.widget_action_button);
        ActionButtonConfig cfg = ActionButtonConfig.load(context, appWidgetId);
        if (cfg == null) {
            views.setTextViewText(R.id.action_label, "Configure");
            views.setTextViewText(R.id.action_state, "Tap to set up");
            views.setImageViewResource(R.id.action_icon, MaterialIcons.drawableRes("settings"));
            views.setInt(R.id.action_icon, "setBackgroundColor", 0);
            views.setOnClickPendingIntent(R.id.action_root,
                WidgetUpdater.configureActionButtonPendingIntent(context, appWidgetId));
            return views;
        }

        int iconRes = MaterialIcons.drawableRes(cfg.icon);
        views.setImageViewResource(R.id.action_icon, iconRes);
        // Transparent by default; optional per-widget chip color from configure
        views.setInt(R.id.action_icon, "setBackgroundColor", cfg.iconBg);
        views.setTextViewText(R.id.action_label, DeviceButtonWidget.truncate(
            cfg.displayLabel(null), 18));
        views.setTextViewText(R.id.action_state, "");

        try {
            if (WidgetApi.apiKey(context) == null) {
                views.setTextViewText(R.id.action_state, "Sign in");
                views.setOnClickPendingIntent(R.id.action_root, WidgetUpdater.openAppPendingIntent(context));
                return views;
            }
            List<String> ids = java.util.Collections.singletonList(cfg.entityId);
            JSONObject states = WidgetApi.entityStates(context, ids);
            JSONObject e = states.optJSONObject(cfg.entityId);
            String state = e != null ? e.optString("state", "") : "";
            views.setTextViewText(R.id.action_state, state);
            views.setTextColor(R.id.action_state,
                WidgetApi.isActiveState(state) ? 0xFF4ADE80 : 0xFF94A3B8);

            String resolvedState = e != null ? e.optString("state", "off") : "off";
            String service = WidgetApi.resolveService(cfg.service, cfg.domain(), resolvedState);
            android.app.PendingIntent pi;
            boolean wouldOpenGarage = cfg.entityId.toLowerCase().contains("garage")
                && ("turn_on".equals(service) || "open_cover".equals(service))
                && WidgetApi.isAwayFromHome(context, WidgetUpdater.AWAY_THRESHOLD_M);
            if (wouldOpenGarage) {
                android.content.Intent conf = new android.content.Intent(context, WidgetActionReceiver.class);
                conf.setAction(WidgetUpdater.ACTION_CONFIRM_GARAGE);
                conf.putExtra(WidgetUpdater.EXTRA_ENTITY, cfg.entityId);
                conf.putExtra(WidgetUpdater.EXTRA_SERVICE, service);
                pi = android.app.PendingIntent.getBroadcast(
                    context, appWidgetId * 31 + 7, conf,
                    android.app.PendingIntent.FLAG_UPDATE_CURRENT | android.app.PendingIntent.FLAG_IMMUTABLE);
            } else {
                pi = WidgetUpdater.actionPendingIntent(context, appWidgetId, cfg.entityId, service);
            }
            views.setOnClickPendingIntent(R.id.action_root, pi);
            // Tap the icon to re-open settings without losing the widget
            views.setOnClickPendingIntent(R.id.action_icon,
                WidgetUpdater.configureActionButtonPendingIntent(context, appWidgetId));
        } catch (Exception ex) {
            views.setTextViewText(R.id.action_state,
                ex.getMessage() != null ? DeviceButtonWidget.truncate(ex.getMessage(), 20) : "offline");
            views.setOnClickPendingIntent(R.id.action_root,
                WidgetUpdater.actionPendingIntent(context, appWidgetId, cfg.entityId, cfg.service));
            views.setOnClickPendingIntent(R.id.action_icon,
                WidgetUpdater.configureActionButtonPendingIntent(context, appWidgetId));
        }
        return views;
    }
}
