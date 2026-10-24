package com.jarvisos.app;

import android.content.Context;
import androidx.annotation.NonNull;
import androidx.work.Constraints;
import androidx.work.ExistingPeriodicWorkPolicy;
import androidx.work.NetworkType;
import androidx.work.PeriodicWorkRequest;
import androidx.work.WorkManager;
import androidx.work.Worker;
import androidx.work.WorkerParameters;
import java.util.concurrent.TimeUnit;
import org.json.JSONObject;

/**
 * Hourly Health Connect import while the app is closed. WorkManager keeps the
 * schedule across reboots and app updates, so no boot hook is needed.
 *
 * Reads in the background only work once the person granted Health
 * Connect's "read in background" permission; without it the run returns
 * {@code waiting_for_app} and the next app open syncs instead.
 */
public class HealthConnectSyncWorker extends Worker {
    static final String WORK_NAME = "health_connect_sync";

    public HealthConnectSyncWorker(@NonNull Context context, @NonNull WorkerParameters params) {
        super(context, params);
    }

    static void schedule(Context context) {
        Constraints constraints = new Constraints.Builder()
            .setRequiredNetworkType(NetworkType.CONNECTED)
            .build();
        PeriodicWorkRequest request =
            new PeriodicWorkRequest.Builder(HealthConnectSyncWorker.class, 1, TimeUnit.HOURS)
                .setConstraints(constraints)
                .build();
        WorkManager.getInstance(context.getApplicationContext())
            .enqueueUniquePeriodicWork(WORK_NAME, ExistingPeriodicWorkPolicy.KEEP, request);
    }

    static void cancel(Context context) {
        WorkManager.getInstance(context.getApplicationContext()).cancelUniqueWork(WORK_NAME);
    }

    @NonNull
    @Override
    public Result doWork() {
        JSONObject result = HealthConnectSync.run(getApplicationContext(), true);
        if (result.optBoolean("ok", false)) return Result.success();
        // Offline or a server error: try again soon. Everything else (not
        // connected, waiting for the app, another account) waits for the next
        // period rather than spinning.
        String error = result.optString("error", "");
        return "post_failed".equals(error) ? Result.retry() : Result.success();
    }
}
