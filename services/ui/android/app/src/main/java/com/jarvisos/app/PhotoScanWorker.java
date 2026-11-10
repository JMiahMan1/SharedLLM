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
 * Checks the gallery for new photos and videos while the app is closed
 * ({@link PhotoScan}); the same pass the WebView can ask for through
 * {@code PhotoScan.scanNow}. A no-op on an adult's phone, when a parent turned
 * photo scanning off, or before the media permission was granted.
 */
public class PhotoScanWorker extends Worker {
    static final String WORK_NAME = "photo_scan";
    static final int PERIOD_MINUTES = 30;

    public PhotoScanWorker(@NonNull Context context, @NonNull WorkerParameters params) {
        super(context, params);
    }

    static void schedule(Context context) {
        Constraints constraints = new Constraints.Builder()
            .setRequiredNetworkType(NetworkType.CONNECTED)
            .build();
        PeriodicWorkRequest request =
            new PeriodicWorkRequest.Builder(PhotoScanWorker.class, PERIOD_MINUTES, TimeUnit.MINUTES)
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
        // The rules ride along with the guard status; a phone whose guard was
        // off may never have learned it is a protected child's phone.
        GuardApi.refreshStatus(getApplicationContext());
        boolean ok = PhotoScan.run(getApplicationContext()).optBoolean("ok", true);
        return ok ? Result.success() : Result.retry();
    }
}
