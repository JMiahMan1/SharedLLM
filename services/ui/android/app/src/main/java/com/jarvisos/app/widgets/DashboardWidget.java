package com.jarvisos.app.widgets;

import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.Context;
import android.content.Intent;
import android.widget.RemoteViews;
import com.jarvisos.app.R;
import java.util.Arrays;
import java.util.List;
import org.json.JSONObject;

public class DashboardWidget extends AppWidgetProvider {
    static final List<String> DEFAULT_CELLS = Arrays.asList(
        "person.jeremiah",
        "binary_sensor.garage_door_sensor",
        "switch.garage_door_2",
        "light.garage_door_state"
    );
    static final int[] CELL_LABEL_IDS = {
        R.id.cell_label_1, R.id.cell_label_2, R.id.cell_label_3, R.id.cell_label_4
    };
    static final int[] CELL_VALUE_IDS = {
        R.id.cell_value_1, R.id.cell_value_2, R.id.cell_value_3, R.id.cell_value_4
    };
    static final int[] CELL_HIT_IDS = {
        R.id.cell_hit_1, R.id.cell_hit_2, R.id.cell_hit_3, R.id.cell_hit_4
    };

    @Override
    public void onUpdate(Context context, AppWidgetManager mgr, int[] ids) {
        WidgetUpdater.pushDashboard(context);
        WidgetAlarm.schedule(context);
    }

    @Override
    public void onReceive(Context context, Intent intent) {
        super.onReceive(context, intent);
        if (AppWidgetManager.ACTION_APPWIDGET_UPDATE.equals(intent.getAction())) {
            WidgetUpdater.pushDashboard(context);
        }
    }

    @Override
    public void onDeleted(Context context, int[] appWidgetIds) {
        if (appWidgetIds == null) return;
        for (int id : appWidgetIds) DashboardConfig.delete(context, id);
        super.onDeleted(context, appWidgetIds);
    }

    static RemoteViews build(Context context) {
        return build(context, -1);
    }

    static RemoteViews build(Context context, int appWidgetId) {
        RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.widget_dashboard);
        views.setOnClickPendingIntent(R.id.dash_root, WidgetUpdater.openAppPendingIntent(context));
        views.setOnClickPendingIntent(R.id.dash_refresh, WidgetUpdater.refreshPendingIntent(context));

        List<String> cells = appWidgetId >= 0
            ? DashboardConfig.loadCells(context, appWidgetId)
            : java.util.Collections.emptyList();
        if (cells.isEmpty()) {
            List<String> pins = WidgetApi.apiKey(context) != null
                ? WidgetApi.pinnedDevices(context)
                : java.util.Collections.emptyList();
            cells = pins.isEmpty() || pins.size() < 2 ? DEFAULT_CELLS
                : pins.subList(0, Math.min(4, pins.size()));
        }
        final List<String> cellIds = cells;

        try {
            JSONObject states = WidgetApi.apiKey(context) != null
                ? WidgetApi.entityStates(context, cellIds)
                : new JSONObject();
            for (int i = 0; i < 4; i++) {
                if (i >= cellIds.size()) {
                    views.setTextViewText(CELL_LABEL_IDS[i], "—");
                    views.setTextViewText(CELL_VALUE_IDS[i], "");
                    views.setOnClickPendingIntent(CELL_HIT_IDS[i], WidgetUpdater.refreshPendingIntent(context));
                    continue;
                }
                String id = cellIds.get(i);
                JSONObject e = states.optJSONObject(id);
                String label = e != null ? WidgetApi.friendlyName(e)
                    : id.substring(id.indexOf('.') + 1).replace('_', ' ');
                String value = e != null ? e.optString("state", "") : "";
                views.setTextViewText(CELL_LABEL_IDS[i], DeviceButtonWidget.truncate(label, 14));
                views.setTextViewText(CELL_VALUE_IDS[i], value);
                String domain = id.substring(0, id.indexOf('.'));
                if (e != null && (domain.equals("light") || domain.equals("switch")
                    || domain.equals("cover") || domain.equals("lock") || domain.equals("fan"))) {
                    String svc = WidgetApi.toggleService(domain, value);
                    boolean garage = id.toLowerCase().contains("garage") && "turn_on".equals(svc);
                    if (garage && WidgetApi.isAwayFromHome(context, WidgetUpdater.AWAY_THRESHOLD_M)) {
                        android.content.Intent conf = new android.content.Intent(context, WidgetActionReceiver.class);
                        conf.setAction(WidgetUpdater.ACTION_CONFIRM_GARAGE);
                        conf.putExtra(WidgetUpdater.EXTRA_ENTITY, id);
                        conf.putExtra(WidgetUpdater.EXTRA_SERVICE, svc);
                        android.app.PendingIntent pi = android.app.PendingIntent.getBroadcast(
                            context, id.hashCode() + 88, conf,
                            android.app.PendingIntent.FLAG_UPDATE_CURRENT | android.app.PendingIntent.FLAG_IMMUTABLE);
                        views.setOnClickPendingIntent(CELL_HIT_IDS[i], pi);
                    } else {
                        views.setOnClickPendingIntent(CELL_HIT_IDS[i],
                            WidgetUpdater.togglePendingIntent(context, id, svc));
                    }
                } else {
                    views.setOnClickPendingIntent(CELL_HIT_IDS[i], WidgetUpdater.openAppPendingIntent(context));
                }
            }
        } catch (Exception e) {
            views.setTextViewText(CELL_LABEL_IDS[0], "Error");
            views.setTextViewText(CELL_VALUE_IDS[0], e.getMessage() != null ? e.getMessage() : "offline");
        }
        return views;
    }
}
