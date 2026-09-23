package com.jarvisos.app.widgets;

import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.Context;
import android.content.Intent;
import android.widget.RemoteViews;
import com.jarvisos.app.R;
import java.util.List;
import org.json.JSONObject;

public class DeviceButtonWidget extends AppWidgetProvider {
    static final int MAX_PINS = 4;
    static final int[] LABEL_IDS = {
        R.id.btn_label_1, R.id.btn_label_2, R.id.btn_label_3, R.id.btn_label_4
    };
    static final int[] STATE_IDS = {
        R.id.btn_state_1, R.id.btn_state_2, R.id.btn_state_3, R.id.btn_state_4
    };
    static final int[] HIT_IDS = {
        R.id.btn_hit_1, R.id.btn_hit_2, R.id.btn_hit_3, R.id.btn_hit_4
    };

    @Override
    public void onUpdate(Context context, AppWidgetManager mgr, int[] ids) {
        WidgetUpdater.pushDeviceButton(context);
        WidgetAlarm.schedule(context);
    }

    @Override
    public void onReceive(Context context, Intent intent) {
        super.onReceive(context, intent);
        if (AppWidgetManager.ACTION_APPWIDGET_UPDATE.equals(intent.getAction())) {
            WidgetUpdater.pushDeviceButton(context);
        }
    }

    static RemoteViews build(Context context) {
        RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.widget_device_button);
        views.setOnClickPendingIntent(R.id.widget_root, WidgetUpdater.openAppPendingIntent(context));
        views.setOnClickPendingIntent(R.id.btn_refresh, WidgetUpdater.refreshPendingIntent(context));

        for (int i = 0; i < MAX_PINS; i++) {
            views.setTextViewText(LABEL_IDS[i], "—");
            views.setTextViewText(STATE_IDS[i], "");
            views.setOnClickPendingIntent(HIT_IDS[i], WidgetUpdater.refreshPendingIntent(context));
        }

        try {
            if (WidgetApi.apiKey(context) == null) {
                views.setTextViewText(LABEL_IDS[0], "Sign in");
                views.setTextViewText(STATE_IDS[0], "Open Jarvis OS");
                return views;
            }
            List<String> pins = WidgetApi.pinnedDevices(context);
            if (pins.isEmpty()) {
                views.setTextViewText(LABEL_IDS[0], "No devices");
                views.setTextViewText(STATE_IDS[0], "Pin from app");
                return views;
            }
            JSONObject states = WidgetApi.entityStates(context, pins.subList(0, Math.min(MAX_PINS, pins.size())));
            int n = Math.min(MAX_PINS, pins.size());
            for (int i = 0; i < n; i++) {
                String id = pins.get(i);
                JSONObject e = states.optJSONObject(id);
                String label;
                String state = "";
                String domain = id.contains(".") ? id.substring(0, id.indexOf('.')) : "";
                String service;
                if (e != null) {
                    label = WidgetApi.friendlyName(e);
                    state = e.optString("state", "");
                    if (domain.isEmpty() && e.has("domain")) domain = e.optString("domain");
                    service = WidgetApi.toggleService(domain, state);
                } else {
                    label = id.substring(id.indexOf('.') + 1).replace('_', ' ');
                    service = WidgetApi.toggleService(domain, "off");
                }
                boolean garage = label.toLowerCase().contains("garage")
                    || id.toLowerCase().contains("garage");
                if (garage && "turn_on".equals(service) && WidgetApi.isAwayFromHome(context, WidgetUpdater.AWAY_THRESHOLD_M)) {
                    // Route through confirm receiver when opening garage while away
                    android.content.Intent conf = new android.content.Intent(context, WidgetActionReceiver.class);
                    conf.setAction(WidgetUpdater.ACTION_CONFIRM_GARAGE);
                    conf.putExtra(WidgetUpdater.EXTRA_ENTITY, id);
                    conf.putExtra(WidgetUpdater.EXTRA_SERVICE, service);
                    android.app.PendingIntent pi = android.app.PendingIntent.getBroadcast(
                        context, id.hashCode() + 77, conf,
                        android.app.PendingIntent.FLAG_UPDATE_CURRENT | android.app.PendingIntent.FLAG_IMMUTABLE);
                    views.setOnClickPendingIntent(HIT_IDS[i], pi);
                } else {
                    views.setOnClickPendingIntent(HIT_IDS[i],
                        WidgetUpdater.togglePendingIntent(context, id, service));
                }
                views.setTextViewText(LABEL_IDS[i], truncate(label, 18));
                views.setTextViewText(STATE_IDS[i], state);
                // RemoteViews cannot call setSelected on TextView; tint the state text instead.
                views.setTextColor(STATE_IDS[i],
                    WidgetApi.isActiveState(state) ? 0xFF4ADE80 : 0xFF94A3B8);
            }
        } catch (Exception e) {
            views.setTextViewText(LABEL_IDS[0], "Error");
            views.setTextViewText(STATE_IDS[0], e.getMessage() != null ? truncate(e.getMessage(), 24) : "offline");
        }
        return views;
    }

    static String truncate(String s, int max) {
        if (s == null) return "";
        return s.length() <= max ? s : s.substring(0, max - 1) + "…";
    }
}
