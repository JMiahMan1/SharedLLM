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
 * Checks for a new APK while the app is closed ({@link NativeUpdater}), for
 * phones where the push service is not running to check every 15 minutes.
 * WorkManager's shortest period is 15 minutes, and it survives reboots.
 */
public class AppUpdateWorker extends Worker {
    static final String WORK_NAME = "app_update";

    public AppUpdateWorker(@NonNull Context context, @NonNull WorkerParameters params) {
        super(context, params);
    }

    static void schedule(Context context) {
        Constraints constraints = new Constraints.Builder()
            .setRequiredNetworkType(NetworkType.CONNECTED)
            .build();
        PeriodicWorkRequest request =
            new PeriodicWorkRequest.Builder(AppUpdateWorker.class, 15, TimeUnit.MINUTES)
                .setConstraints(constraints)
                .build();
        WorkManager.getInstance(context.getApplicationContext())
            .enqueueUniquePeriodicWork(WORK_NAME, ExistingPeriodicWorkPolicy.KEEP, request);
    }

    @NonNull
    @Override
    public Result doWork() {
        // This worker is never cancelled by a sign-out, so it puts back the
        // collecting workers a sign-out (and a later sign-in that did not
        // schedule them) left off: a phone recovers by itself, without anyone
        // opening the app. KEEP policy: a no-op when they are scheduled.
        BackgroundWork.ensureScheduled(getApplicationContext());
        NativeUpdater.maybeCheck(getApplicationContext(), 5 * 60_000L);
        return Result.success();
    }
}
