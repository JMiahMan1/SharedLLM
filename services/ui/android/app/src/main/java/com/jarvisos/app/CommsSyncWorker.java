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

/**
 * Calls and texts while the app is closed: {@link CommsCollector} every 15
 * minutes (WorkManager's shortest period), and whatever {@link ActivityOutbox}
 * and {@link LocationOutbox} still hold. Without call-log and SMS permission,
 * or signed out, a run sends no calls or texts. WorkManager keeps the schedule
 * across reboots and app updates.
 */
public class CommsSyncWorker extends Worker {
    static final String WORK_NAME = "comms_sync";

    public CommsSyncWorker(@NonNull Context context, @NonNull WorkerParameters params) {
        super(context, params);
    }

    static void schedule(Context context) {
        Constraints constraints = new Constraints.Builder()
            .setRequiredNetworkType(NetworkType.CONNECTED)
            .build();
        PeriodicWorkRequest request =
            new PeriodicWorkRequest.Builder(CommsSyncWorker.class, 15, TimeUnit.MINUTES)
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
        // Whether this is a child's phone (and its rules): the guard service
        // fetched it only while the screen guard was on, so a teen's phone with
        // the guard off never learned it was protected, and nothing that
        // depends on that (message notifications, app approvals) ran.
        GuardApi.refreshStatus(getApplicationContext());
        // A stopped run kept its place; retrying soon beats waiting a period.
        boolean ok = CommsCollector.run(getApplicationContext()).optBoolean("ok", true);
        // Chat notifications and media plays the phone could not send when they happened.
        ok &= ActivityOutbox.flush(getApplicationContext());
        // Location fixes from a stretch without signal, in case the tracking
        // service is not running (or not moving) to send them itself.
        ok &= LocationOutbox.flush(getApplicationContext());
        return ok ? Result.success() : Result.retry();
    }
}
