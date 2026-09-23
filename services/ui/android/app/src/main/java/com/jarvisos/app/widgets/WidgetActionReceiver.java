package com.jarvisos.app.widgets;

import android.app.AlertDialog;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Handler;
import android.os.Looper;
import android.widget.Toast;
import com.jarvisos.app.MainActivity;
import org.json.JSONObject;

public class WidgetActionReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null || intent.getAction() == null) return;
        final String action = intent.getAction();
        final Context app = context.getApplicationContext();

        if (WidgetUpdater.ACTION_REFRESH.equals(action)) {
            WidgetUpdater.requestAll(app);
            return;
        }

        if (WidgetUpdater.ACTION_TOGGLE.equals(action)) {
            final String entity = intent.getStringExtra(WidgetUpdater.EXTRA_ENTITY);
            final String service = intent.getStringExtra(WidgetUpdater.EXTRA_SERVICE);
            if (entity == null || service == null) return;
            // Garage open while away → confirm on UI thread
            boolean opensGarage = "turn_on".equals(service) || "open_cover".equals(service)
                || "toggle".equals(service);
            if (entity.toLowerCase().contains("garage") && opensGarage
                && WidgetApi.isAwayFromHome(app, WidgetUpdater.AWAY_THRESHOLD_M)) {
                if ("toggle".equals(service)) {
                    // Resolve first; only confirm if it would actually open
                    WidgetUpdater.onBackground(() -> {
                        try {
                            String domain = entity.substring(0, entity.indexOf('.'));
                            JSONObject states = WidgetApi.entityStates(app,
                                java.util.Collections.singletonList(entity));
                            JSONObject e = states.optJSONObject(entity);
                            String state = e != null ? e.optString("state", "off") : "off";
                            String resolved = WidgetApi.toggleService(domain, state);
                            if ("turn_on".equals(resolved) || "open_cover".equals(resolved)) {
                                confirmGarage(app, entity, resolved, true);
                            } else {
                                executeToggle(app, entity, "toggle");
                            }
                        } catch (Exception ex) {
                            postToast(app, ex.getMessage() != null ? ex.getMessage() : "Command failed");
                        }
                    });
                    return;
                }
                confirmGarage(app, entity, service, true);
                return;
            }
            executeToggle(app, entity, service);
            return;
        }

        if (WidgetUpdater.ACTION_CONFIRM_GARAGE.equals(action)) {
            final String entity = intent.getStringExtra(WidgetUpdater.EXTRA_ENTITY);
            final String service = intent.getStringExtra(WidgetUpdater.EXTRA_SERVICE);
            if (entity == null || service == null) return;
            confirmGarage(app, entity, service, true);
            return;
        }

        if (WidgetUpdater.ACTION_MEDIA.equals(action)) {
            final String command = intent.getStringExtra(WidgetUpdater.EXTRA_COMMAND);
            if (command == null) return;
            WidgetUpdater.onBackground(() -> {
                try {
                    WidgetApi.mediaTransport(app, command);
                    WidgetUpdater.pushMedia(app);
                } catch (Exception e) {
                    postToast(app, "Media: " + e.getMessage());
                }
            });
        }
    }

    private void executeToggle(Context app, String entity, String service) {
        WidgetUpdater.onBackground(() -> {
            try {
                String domain = entity.substring(0, entity.indexOf('.'));
                String resolved = service;
                if ("toggle".equals(service)) {
                    JSONObject states = WidgetApi.entityStates(app,
                        java.util.Collections.singletonList(entity));
                    JSONObject e = states.optJSONObject(entity);
                    String state = e != null ? e.optString("state", "off") : "off";
                    resolved = WidgetApi.toggleService(domain, state);
                }
                WidgetApi.haService(app, domain, resolved, entity);
                WidgetUpdater.requestAll(app);
            } catch (Exception e) {
                postToast(app, e.getMessage() != null ? e.getMessage() : "Command failed");
            }
        });
    }

    private void confirmGarage(Context app, String entity, String service, boolean forceConfirm) {
        if (!forceConfirm) {
            executeToggle(app, entity, service);
            return;
        }
        Handler h = new Handler(Looper.getMainLooper());
        h.post(() -> {
            try {
                AlertDialog d = new AlertDialog.Builder(app)
                    .setTitle("Open garage?")
                    .setMessage("You appear to be away from home. Open the garage door anyway?")
                    .setPositiveButton("Open", (dialog, which) -> executeToggle(app, entity, service))
                    .setNegativeButton("Cancel", null)
                    .create();
                // TYPE_SYSTEM_DIALOG requires system UID — use application overlay / activity window instead
                try {
                    d.getWindow().setType(android.view.WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY);
                } catch (Exception ignored) {
                    // fall through to default window type
                }
                d.show();
            } catch (Exception e) {
                // No window token (background) — fall back to toast + require second path
                postToast(app, "Away from home — open Jarvis OS to confirm garage");
            }
        });
    }

    private static void postToast(Context app, String msg) {
        Handler h = new Handler(Looper.getMainLooper());
        h.post(() -> Toast.makeText(app, msg, Toast.LENGTH_SHORT).show());
    }
}
