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

    static final int[] ICON_IDS = {
        R.id.btn_icon_1, R.id.btn_icon_2, R.id.btn_icon_3, R.id.btn_icon_4
    };

    static RemoteViews build(Context context) {
        RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.widget_device_button);
        views.setOnClickPendingIntent(R.id.widget_root, WidgetUpdater.openAppPendingIntent(context));
        views.setOnClickPendingIntent(R.id.btn_refresh, WidgetUpdater.refreshPendingIntent(context));
        WidgetStyle st = WidgetStyle.of(context);
        st.header(views, R.id.widget_root, R.id.btn_icon, R.id.btn_title, R.id.btn_refresh, st.accent);
        for (int i = 0; i < MAX_PINS; i++) {
            views.setTextViewText(LABEL_IDS[i], "-");
            views.setTextViewText(STATE_IDS[i], "");
            st.tile(views, HIT_IDS[i], false);
            st.icon(views, ICON_IDS[i], st.faint);
            st.text(views, LABEL_IDS[i], st.muted);
            views.setOnClickPendingIntent(HIT_IDS[i], WidgetUpdater.openAppPendingIntent(context));
        }
        try {
            if (WidgetApi.apiKey(context) == null) {
                st.status(views, R.id.btn_status, "Sign in to Jarvis OS");
                return views;
            }
            List<String> pins = WidgetApi.pinnedDevices(context);
            if (pins.isEmpty()) {
                st.status(views, R.id.btn_status, "Pin devices in the app to see them here");
                return views;
            }
            int n = Math.min(MAX_PINS, pins.size());
            JSONObject states = WidgetApi.entityStates(context, pins.subList(0, n));
            for (int i = 0; i < n; i++) {
                String id = pins.get(i);
                JSONObject e = states.optJSONObject(id);
                paintTile(context, views, st, HIT_IDS[i], ICON_IDS[i], LABEL_IDS[i], STATE_IDS[i], id, e);
                String state = e != null ? EntityNames.text(e, "state") : "off";
                String domain = id.contains(".") ? id.substring(0, id.indexOf('.')) : "";
                String service = WidgetApi.toggleService(domain, state);
                boolean garage = id.toLowerCase().contains("garage");
                if (garage && "turn_on".equals(service) && WidgetApi.isAwayFromHome(context, WidgetUpdater.AWAY_THRESHOLD_M)) {
                    views.setOnClickPendingIntent(HIT_IDS[i], WidgetUpdater.confirmGaragePendingIntent(context, id, service));
                } else if (EntityStates.toggleable(id)) {
                    views.setOnClickPendingIntent(HIT_IDS[i], WidgetUpdater.togglePendingIntent(context, id, service));
                }
            }
        } catch (Exception e) {
            st.status(views, R.id.btn_status, e.getMessage() != null ? truncate(e.getMessage(), 60) : "Offline");
        }
        return views;
    }

    /** Paint one control tile for an entity: icon, name, human state, accent wash when on. */
    static void paintTile(Context context, RemoteViews views, WidgetStyle st, int hit, int icon, int label, int stateId,
                          String entityId, JSONObject e) {
        String state = e != null ? EntityNames.text(e, "state") : "";
        JSONObject attrs = e != null ? e.optJSONObject("attributes") : null;
        String dc = EntityStates.field(e, attrs, "device_class");
        String domain = entityId.contains(".") ? entityId.substring(0, entityId.indexOf('.')) : "";
        boolean on = EntityStates.active(state);
        String name = e != null ? WidgetApi.friendlyName(e) : entityId.substring(entityId.indexOf('.') + 1).replace('_', ' ');
        String shown;
        if (e == null) shown = "Not found";
        else if ("sensor".equals(domain)) {
            String unit = EntityStates.field(e, attrs, "unit_of_measurement");
            shown = EntityStates.number(state) + (unit.isEmpty() ? "" : " " + unit);
        } else shown = EntityStates.pretty(domain, state);
        views.setTextViewText(label, name);
        views.setTextViewText(stateId, shown);
        views.setImageViewResource(icon, EntityStates.icon(entityId, dc, state));
        st.tile(views, hit, on);
        st.icon(views, icon, on ? st.accent : st.muted);
        st.text(views, label, st.text);
        st.text(views, stateId, on ? st.accent : st.muted);
        views.setContentDescription(hit, name + ": " + shown);
    }

    static String truncate(String s, int max) {
        if (s == null) return "";
        return s.length() <= max ? s : s.substring(0, max - 1) + "…";
    }
}
