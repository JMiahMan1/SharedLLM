package com.jarvisos.app.widgets;

import android.app.PendingIntent;
import android.appwidget.AppWidgetManager;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Handler;
import android.os.Looper;
import android.widget.RemoteViews;
import com.jarvisos.app.MainActivity;
import com.jarvisos.app.R;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Fetches gateway state and pushes RemoteViews to all registered widgets. */
public final class WidgetUpdater {
    public static final String ACTION_REFRESH = "com.jarvisos.app.widget.REFRESH";
    public static final String ACTION_TOGGLE = "com.jarvisos.app.widget.TOGGLE";
    public static final String ACTION_MEDIA = "com.jarvisos.app.widget.MEDIA";
    public static final String ACTION_CONFIRM_GARAGE = "com.jarvisos.app.widget.CONFIRM_GARAGE";
    public static final String EXTRA_ENTITY = "entity_id";
    public static final String EXTRA_SERVICE = "service";
    public static final String EXTRA_COMMAND = "command";
    public static final float AWAY_THRESHOLD_M = 200f;
    public static final String GARAGE_HINT = "garage";

    private static final ExecutorService IO = Executors.newFixedThreadPool(2);
    private static final Handler MAIN = new Handler(Looper.getMainLooper());

    private WidgetUpdater() {}

    public static void requestAll(Context context) {
        request(context, DeviceButtonWidget.class);
        request(context, MetricWidget.class);
        request(context, MediaWidget.class);
        request(context, DashboardWidget.class);
    }

    public static void request(Context context, Class<?> cls) {
        AppWidgetManager mgr = AppWidgetManager.getInstance(context);
        int[] ids = mgr.getAppWidgetIds(new ComponentName(context, cls));
        if (ids == null || ids.length == 0) return;
        Intent intent = new Intent(context, cls);
        intent.setAction(AppWidgetManager.ACTION_APPWIDGET_UPDATE);
        intent.putExtra(AppWidgetManager.EXTRA_APPWIDGET_IDS, ids);
        context.sendBroadcast(intent);
    }

    public static PendingIntent refreshPendingIntent(Context context) {
        Intent i = new Intent(context, WidgetActionReceiver.class);
        i.setAction(ACTION_REFRESH);
        return PendingIntent.getBroadcast(context, 1001, i,
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    public static PendingIntent togglePendingIntent(Context context, String entityId, String service) {
        Intent i = new Intent(context, WidgetActionReceiver.class);
        i.setAction(ACTION_TOGGLE);
        i.putExtra(EXTRA_ENTITY, entityId);
        i.putExtra(EXTRA_SERVICE, service);
        return PendingIntent.getBroadcast(context, entityId.hashCode() ^ service.hashCode(), i,
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    public static PendingIntent mediaPendingIntent(Context context, String command) {
        Intent i = new Intent(context, WidgetActionReceiver.class);
        i.setAction(ACTION_MEDIA);
        i.putExtra(EXTRA_COMMAND, command);
        return PendingIntent.getBroadcast(context, 2000 + command.hashCode(), i,
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    public static PendingIntent openAppPendingIntent(Context context) {
        Intent i = new Intent(context, MainActivity.class);
        i.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        i.setData(Uri.parse("jarvis://home"));
        return PendingIntent.getActivity(context, 3001, i,
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    public static void onBackground(Runnable r) {
        IO.execute(r);
    }

    public static void onMain(Runnable r) {
        MAIN.post(r);
    }

    public static void pushDeviceButton(Context context) {
        AppWidgetManager mgr = AppWidgetManager.getInstance(context);
        int[] ids = mgr.getAppWidgetIds(new ComponentName(context, DeviceButtonWidget.class));
        if (ids == null || ids.length == 0) return;
        onBackground(() -> {
            RemoteViews views = DeviceButtonWidget.build(context);
            onMain(() -> {
                for (int id : ids) mgr.updateAppWidget(id, views);
            });
        });
    }

    public static void pushMetric(Context context) {
        AppWidgetManager mgr = AppWidgetManager.getInstance(context);
        int[] ids = mgr.getAppWidgetIds(new ComponentName(context, MetricWidget.class));
        if (ids == null || ids.length == 0) return;
        onBackground(() -> {
            RemoteViews views = MetricWidget.build(context);
            onMain(() -> {
                for (int id : ids) mgr.updateAppWidget(id, views);
            });
        });
    }

    public static void pushMedia(Context context) {
        AppWidgetManager mgr = AppWidgetManager.getInstance(context);
        int[] ids = mgr.getAppWidgetIds(new ComponentName(context, MediaWidget.class));
        if (ids == null || ids.length == 0) return;
        onBackground(() -> {
            RemoteViews views = MediaWidget.build(context);
            onMain(() -> {
                for (int id : ids) mgr.updateAppWidget(id, views);
            });
        });
    }

    public static void pushDashboard(Context context) {
        AppWidgetManager mgr = AppWidgetManager.getInstance(context);
        int[] ids = mgr.getAppWidgetIds(new ComponentName(context, DashboardWidget.class));
        if (ids == null || ids.length == 0) return;
        onBackground(() -> {
            RemoteViews views = DashboardWidget.build(context);
            onMain(() -> {
                for (int id : ids) mgr.updateAppWidget(id, views);
            });
        });
    }
}
