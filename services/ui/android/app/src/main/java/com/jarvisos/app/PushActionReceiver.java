package com.jarvisos.app;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.util.Log;
import androidx.core.app.RemoteInput;
import org.json.JSONObject;

/**
 * A callback or reply button on a Jarvis notification: calls
 * {@code POST /api/push/action} with the callback, its params and, for a
 * reply, the text typed inline, then updates the notification
 * ({@link PushNotifier#actionFinished}).
 */
public class PushActionReceiver extends BroadcastReceiver {
    private static final String TAG = "PushAction";
    private static final String ACTION = "com.jarvisos.app.PUSH_ACTION";
    static final String KEY_REPLY = "jarvis_reply";
    static final String EXTRA_CALLBACK = "callback";
    static final String EXTRA_PARAMS = "params";
    static final String EXTRA_IS_REPLY = "is_reply";
    static final String EXTRA_NOTIFICATION_ID = "notification_id";
    static final String EXTRA_CHAT_TOKEN = "chat_token";
    static final String EXTRA_TITLE = "title";
    static final String EXTRA_CATEGORY = "category";
    static final String EXTRA_PATH = "path";

    static Intent intent(Context context, PushNotice.Action a, PushNotice n, int notificationId, int index,
                         String chatToken) {
        return new Intent(context, PushActionReceiver.class)
                .setAction(ACTION)
                // One data URI per button, so two buttons' PendingIntents never merge.
                .setData(Uri.parse("jarvis-action://" + notificationId + "/" + index))
                .putExtra(EXTRA_CALLBACK, a.callback)
                .putExtra(EXTRA_PARAMS, a.params)
                .putExtra(EXTRA_IS_REPLY, a.isReply())
                .putExtra(EXTRA_NOTIFICATION_ID, notificationId)
                .putExtra(EXTRA_CHAT_TOKEN, chatToken)
                .putExtra(EXTRA_TITLE, n.title)
                .putExtra(EXTRA_CATEGORY, n.category)
                .putExtra(EXTRA_PATH, n.path);
    }

    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null || !ACTION.equals(intent.getAction())) return;
        final String callback = intent.getStringExtra(EXTRA_CALLBACK);
        if (callback == null || callback.isEmpty()) return;
        final boolean isReply = intent.getBooleanExtra(EXTRA_IS_REPLY, false);
        CharSequence typed = null;
        if (isReply) {
            Bundle results = RemoteInput.getResultsFromIntent(intent);
            typed = results != null ? results.getCharSequence(KEY_REPLY) : null;
        }
        final String reply = typed == null ? "" : typed.toString().trim();
        final String params = intent.getStringExtra(EXTRA_PARAMS);
        final Context app = context.getApplicationContext();
        if (callback.startsWith("timer_")) {
            FindPhoneService.stop(app);
        }
        final PendingResult pending = goAsync();
        new Thread(() -> {
            boolean ok = false;
            try {
                if (isReply && reply.isEmpty()) return;
                JSONObject body = new JSONObject();
                body.put("callback", callback);
                body.put("params", new JSONObject(params == null || params.isEmpty() ? "{}" : params));
                if (isReply) body.put("reply", reply);
                PushGateway.post(app, PushGateway.ACTION, body);
                ok = true;
            } catch (Exception e) {
                Log.w(TAG, callback + " failed: " + e.getMessage());
            } finally {
                try {
                    PushNotifier.actionFinished(app, intent, ok, reply);
                } finally {
                    pending.finish();
                }
            }
        }, "push-action").start();
    }
}
