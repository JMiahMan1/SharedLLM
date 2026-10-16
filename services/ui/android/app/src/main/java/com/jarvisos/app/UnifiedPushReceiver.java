package com.jarvisos.app;

import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.util.Log;
import androidx.core.content.IntentCompat;

/**
 * The registration receiver of a UnifiedPush distributor: REGISTER,
 * UNREGISTER and MESSAGE_ACK from apps that use Jarvis for push.
 *
 * The calling app is identified by the identity Android shares on SDK 34 and
 * later, or by the creator of the {@code pi} PendingIntent it attaches.
 * Requests with invalid extras, or from an app that cannot be identified,
 * are ignored, as the spec says. Disabled while nobody is signed in
 * ({@link UnifiedPushDistributor#setEnabled}).
 */
public class UnifiedPushReceiver extends BroadcastReceiver {
    private static final String TAG = "UnifiedPush";

    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null || intent.getAction() == null) return;
        String action = intent.getAction();
        String token = intent.getStringExtra(UnifiedPushDistributor.EXTRA_TOKEN);
        if (!UnifiedPushRules.validToken(token)) return;
        final Context app = context.getApplicationContext();

        if (UnifiedPushDistributor.ACTION_MESSAGE_ACK.equals(action)) {
            // Messages are not resent, so an acknowledgement needs no bookkeeping.
            String id = intent.getStringExtra(UnifiedPushDistributor.EXTRA_ID);
            if (UnifiedPushRules.validMessageId(id)) Log.d(TAG, "ack " + id);
            return;
        }

        final String caller = UnifiedPushRules.callerPackage(sharedIdentity(), pendingIntentCreator(intent));
        if (UnifiedPushDistributor.ACTION_REGISTER.equals(action)) {
            if (caller == null) {
                Log.w(TAG, "REGISTER from an app that did not identify itself; ignored");
                return;
            }
            if (!UnifiedPushRules.validVapid(intent.getStringExtra(UnifiedPushDistributor.EXTRA_VAPID))) return;
            final String description = UnifiedPushRules.description(
                    intent.getStringExtra(UnifiedPushDistributor.EXTRA_MESSAGE));
            final PendingResult pending = goAsync();
            UnifiedPushDistributor.run(() -> {
                try {
                    UnifiedPushDistributor.register(app, caller, token, description);
                } finally {
                    pending.finish();
                }
            });
        } else if (UnifiedPushDistributor.ACTION_UNREGISTER.equals(action)) {
            final PendingResult pending = goAsync();
            UnifiedPushDistributor.run(() -> {
                try {
                    UnifiedPushDistributor.unregister(app, token, caller);
                } finally {
                    pending.finish();
                }
            });
        }
    }

    private String sharedIdentity() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.UPSIDE_DOWN_CAKE) return null;
        return getSentFromPackage();
    }

    private static String pendingIntentCreator(Intent intent) {
        PendingIntent pi = IntentCompat.getParcelableExtra(intent, UnifiedPushDistributor.EXTRA_PI, PendingIntent.class);
        return pi == null ? null : pi.getCreatorPackage();
    }
}
