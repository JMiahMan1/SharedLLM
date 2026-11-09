package com.jarvisos.app.widgets;

import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.Context;
import android.content.Intent;
import android.widget.RemoteViews;
import com.jarvisos.app.R;
import java.util.List;
import org.json.JSONObject;

public class DashboardWidget extends AppWidgetProvider {
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

    static final int[] CELL_ICON_IDS = {
        R.id.cell_icon_1, R.id.cell_icon_2, R.id.cell_icon_3, R.id.cell_icon_4
    };

    static RemoteViews build(Context context, int appWidgetId) {
        RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.widget_dashboard);
        views.setOnClickPendingIntent(R.id.dash_root, WidgetUpdater.openAppPendingIntent(context));
        views.setOnClickPendingIntent(R.id.dash_refresh, WidgetUpdater.refreshPendingIntent(context));
        WidgetStyle st = WidgetStyle.of(context);
        st.header(views, R.id.dash_root, R.id.dash_icon, R.id.dash_title, R.id.dash_refresh, st.accent);
        List<String> cells = appWidgetId >= 0
            ? DashboardConfig.loadCells(context, appWidgetId)
            : java.util.Collections.emptyList();
        if (cells.isEmpty()) {
            // Unconfigured: the pinned devices (never someone's hard-coded entities).
            List<String> pins = WidgetApi.apiKey(context) != null
                ? WidgetApi.pinnedDevices(context)
                : java.util.Collections.emptyList();
            cells = pins.subList(0, Math.min(4, pins.size()));
        }
        for (int i = 0; i < 4; i++) {
            views.setTextViewText(CELL_LABEL_IDS[i], "-");
            views.setTextViewText(CELL_VALUE_IDS[i], "");
            st.tile(views, CELL_HIT_IDS[i], false);
            st.icon(views, CELL_ICON_IDS[i], st.faint);
            st.text(views, CELL_LABEL_IDS[i], st.muted);
            views.setOnClickPendingIntent(CELL_HIT_IDS[i], WidgetUpdater.openAppPendingIntent(context));
        }
        if (WidgetApi.apiKey(context) == null) {
            st.status(views, R.id.dash_status, "Sign in to Jarvis OS");
            return views;
        }
        if (cells.isEmpty()) {
            st.status(views, R.id.dash_status, "Long-press this widget to choose what it shows");
            return views;
        }
        try {
            JSONObject states = WidgetApi.entityStates(context, cells);
            for (int i = 0; i < 4 && i < cells.size(); i++) {
                String id = cells.get(i);
                JSONObject e = states.optJSONObject(id);
                DeviceButtonWidget.paintTile(context, views, st, CELL_HIT_IDS[i], CELL_ICON_IDS[i],
                        CELL_LABEL_IDS[i], CELL_VALUE_IDS[i], id, e);
                if (e != null && EntityStates.toggleable(id)) {
                    String domain = id.substring(0, id.indexOf('.'));
                    String svc = WidgetApi.toggleService(domain, EntityNames.text(e, "state"));
                    boolean garage = id.toLowerCase().contains("garage") && "turn_on".equals(svc);
                    if (garage && WidgetApi.isAwayFromHome(context, WidgetUpdater.AWAY_THRESHOLD_M)) {
                        views.setOnClickPendingIntent(CELL_HIT_IDS[i], WidgetUpdater.confirmGaragePendingIntent(context, id, svc));
                    } else {
                        views.setOnClickPendingIntent(CELL_HIT_IDS[i], WidgetUpdater.togglePendingIntent(context, id, svc));
                    }
                }
            }
        } catch (Exception e) {
            st.status(views, R.id.dash_status, e.getMessage() != null ? e.getMessage() : "Offline");
        }
        return views;
    }
}
