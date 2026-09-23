package com.jarvisos.app.widgets;

import android.app.AlertDialog;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Handler;
import android.os.Looper;
import android.widget.Toast;
import com.jarvisos.app.MainActivity;

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
            if (entity.toLowerCase().contains("garage") && "turn_on".equals(service)
                && WidgetApi.isAwayFromHome(app, WidgetUpdater.AWAY_THRESHOLD_M)) {
                confirmGarage(app, entity, service, false);
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
                WidgetApi.haService(app, domain, service, entity);
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
