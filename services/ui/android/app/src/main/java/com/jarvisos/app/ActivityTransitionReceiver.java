package com.jarvisos.app;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import com.google.android.gms.location.ActivityTransition;
import com.google.android.gms.location.ActivityTransitionEvent;
import com.google.android.gms.location.ActivityTransitionResult;

/**
 * Activity Recognition's transitions (into and out of a vehicle, a bike,
 * walking, running, still), which {@link LocationTrackingService} registers
 * for. They are handed to the service, oldest first, to set how often it asks
 * for a fix.
 */
public class ActivityTransitionReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null || !ActivityTransitionResult.hasResult(intent)) return;
        ActivityTransitionResult result = ActivityTransitionResult.extractResult(intent);
        if (result == null) return;
        for (ActivityTransitionEvent event : result.getTransitionEvents()) {
            LocationTrackingService.onActivityTransition(context, event.getActivityType(),
                    event.getTransitionType() == ActivityTransition.ACTIVITY_TRANSITION_ENTER);
        }
    }
}
