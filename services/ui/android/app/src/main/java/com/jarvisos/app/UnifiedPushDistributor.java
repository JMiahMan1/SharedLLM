package com.jarvisos.app;

import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.ServiceConnection;
import android.content.SharedPreferences;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.util.Log;
import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;
import org.json.JSONObject;

/**
 * Jarvis as a UnifiedPush distributor (AND_3,
 * https://unifiedpush.org/developers/spec/android/): other apps on the phone
 * register here, the gateway gives each registration an {@code up*} topic and
 * a public endpoint, and messages on that topic arrive over the same push
 * stream ({@link PushService}) and are handed to the app as MESSAGE.
 *
 * Registrations are kept per token in their own SharedPreferences file. The
 * registration receiver and link activity are turned off while nobody is
 * signed in, as the spec asks, so apps do not pick a distributor that cannot
 * deliver.
 */
final class UnifiedPushDistributor {
    private static final String TAG = "UnifiedPush";
    private static final String PREFS = "jarvis_unifiedpush";

    static final String ACTION_REGISTER = "org.unifiedpush.android.distributor.REGISTER";
    static final String ACTION_UNREGISTER = "org.unifiedpush.android.distributor.UNREGISTER";
    static final String ACTION_MESSAGE_ACK = "org.unifiedpush.android.distributor.MESSAGE_ACK";
    static final String ACTION_NEW_ENDPOINT = "org.unifiedpush.android.connector.NEW_ENDPOINT";
    static final String ACTION_MESSAGE = "org.unifiedpush.android.connector.MESSAGE";
    static final String ACTION_UNREGISTERED = "org.unifiedpush.android.connector.UNREGISTERED";
    static final String ACTION_REGISTRATION_FAILED = "org.unifiedpush.android.connector.REGISTRATION_FAILED";
    static final String ACTION_RAISE_TO_FOREGROUND = "org.unifiedpush.android.connector.RAISE_TO_FOREGROUND";

    static final String EXTRA_TOKEN = "token";
    static final String EXTRA_PI = "pi";
    static final String EXTRA_VAPID = "vapid";
    static final String EXTRA_MESSAGE = "message";
    static final String EXTRA_ID = "id";
    static final String EXTRA_ENDPOINT = "endpoint";
    static final String EXTRA_REASON = "reason";
    static final String EXTRA_BYTES_MESSAGE = "bytesMessage";

    static final String REASON_INTERNAL_ERROR = "INTERNAL_ERROR";
    static final String REASON_NETWORK = "NETWORK";
    static final String REASON_ACTION_REQUIRED = "ACTION_REQUIRED";

    /** How long the receiving app is kept at foreground importance per message. */
    private static final long RAISE_MS = 5_000L;
    /** Deliver anyway if the app's raise service has not answered by then. */
    private static final long RAISE_WAIT_MS = 1_000L;

    /** One worker, so registrations for the same token never race each other. */
    private static final ExecutorService WORK = Executors.newSingleThreadExecutor();

    private UnifiedPushDistributor() {}

    static void run(Runnable task) {
        WORK.execute(task);
    }

    private static SharedPreferences store(Context context) {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    static synchronized List<UnifiedPushRegistration> all(Context context) {
        List<UnifiedPushRegistration> out = new ArrayList<>();
        for (Map.Entry<String, ?> e : store(context).getAll().entrySet()) {
            UnifiedPushRegistration r = UnifiedPushRegistration.fromJson(String.valueOf(e.getValue()));
            if (r != null) out.add(r);
        }
        return out;
    }

    static synchronized UnifiedPushRegistration get(Context context, String token) {
        return UnifiedPushRegistration.fromJson(store(context).getString(token, null));
    }

    private static synchronized void save(Context context, UnifiedPushRegistration r) {
        store(context).edit().putString(r.token, r.toJson()).apply();
    }

    private static synchronized void forget(Context context, String token) {
        store(context).edit().remove(token).apply();
    }

    static UnifiedPushRegistration byTopic(Context context, String topic) {
        for (UnifiedPushRegistration r : all(context)) {
            if (r.topic.equals(topic)) return r;
        }
        return null;
    }

    /**
     * REGISTER from {@code app}. The gateway is always asked, since the
     * registration may have been removed from the web page or another phone:
     * it answers with the existing topic, or a new one when it had none, and
     * the app gets that endpoint. Only when the gateway cannot be asked (no
     * network, or it is failing) is the endpoint known here sent again; the
     * check when the stream opens ({@link #reconcile}) corrects it later.
     * Runs on the worker thread.
     */
    static void register(Context context, String app, String token, String description) {
        UnifiedPushRegistration existing = get(context, token);
        if (existing != null && !existing.app.equals(app)) {
            Log.w(TAG, app + " sent a token registered to another app; ignored");
            return;
        }
        if (!signedIn(context)) {
            sendFailed(context, app, token, REASON_ACTION_REQUIRED);
            return;
        }
        try {
            UnifiedPushRegistration r = registerWithGateway(context, app, token,
                    description != null ? description : existing != null ? existing.description : null);
            save(context, r);
            sendNewEndpoint(context, r);
            // The gateway picks the topics to stream when the stream opens.
            if (existing == null || !existing.topic.equals(r.topic)) PushService.reconnect(context);
        } catch (Exception e) {
            Log.w(TAG, "register " + app + ": " + e.getMessage());
            if (existing != null) {
                sendNewEndpoint(context, existing);
            } else {
                sendFailed(context, app, token, e instanceof IOException ? REASON_NETWORK : REASON_INTERNAL_ERROR);
            }
        }
    }

    /** {@code POST /api/push/up/register} as the signed-in person; throws when the answer is unusable. */
    private static UnifiedPushRegistration registerWithGateway(Context context, String app, String token,
                                                               String description) throws Exception {
        JSONObject body = new JSONObject();
        body.put("app", app);
        body.put("token", token);
        JSONObject res = PushGateway.post(context, PushGateway.UP_REGISTER, body);
        String endpoint = PushNotice.str(res, "endpoint");
        String topic = PushNotice.str(res, "topic");
        if (!UnifiedPushRules.validEndpoint(endpoint) || !UnifiedPushRules.isUpTopic(topic)) {
            throw new IllegalStateException("gateway answered without a usable endpoint for " + app);
        }
        return new UnifiedPushRegistration(token, app, topic, endpoint, description);
    }

    /**
     * Brings this phone's registrations in line with the gateway's when the
     * stream opens. One the gateway no longer has (removed on the web page or
     * from another phone) is dropped and its app hears UNREGISTERED, as AND_3
     * asks when the distributor ends a registration; the app may register
     * again. One whose topic changed gets its new endpoint. Nothing changes
     * when the gateway cannot be asked.
     */
    static void reconcile(Context context) {
        final Context app = context.getApplicationContext();
        run(() -> {
            if (all(app).isEmpty()) return;
            Map<String, UnifiedPushRegistration> server;
            try {
                server = UnifiedPushRegistration.thisDevice(PushGateway.get(app, PushGateway.UP_LIST));
            } catch (Exception e) {
                Log.w(TAG, "could not check registrations: " + e.getMessage());
                return;
            }
            if (server == null) return;
            boolean moved = false;
            for (UnifiedPushRegistration r : all(app)) {
                UnifiedPushRegistration there = server.get(UnifiedPushRegistration.key(r.app, r.token));
                if (there == null) {
                    Log.i(TAG, r.app + " is no longer registered on the server; telling it");
                    forget(app, r.token);
                    sendUnregistered(app, r.app, r.token);
                } else if (!r.sameDelivery(there)) {
                    UnifiedPushRegistration now = r.movedTo(there);
                    save(app, now);
                    sendNewEndpoint(app, now);
                    moved = true;
                }
            }
            if (moved) PushService.reconnect(app);
        });
    }

    /**
     * Another person signed in on this phone: each app's registration moves
     * to the new account and gets its endpoint there, and the previous
     * account's is removed with the key being replaced. When the new account
     * gives the same topic (the same person with a new key) nothing is
     * removed. A registration the new account cannot take is ended and its
     * app hears UNREGISTERED.
     */
    static void switchAccount(Context context, String previousServer, String previousKey) {
        final Context app = context.getApplicationContext();
        run(() -> {
            for (UnifiedPushRegistration r : all(app)) {
                boolean keep = false;
                try {
                    UnifiedPushRegistration now = registerWithGateway(app, r.app, r.token, r.description);
                    save(app, now);
                    sendNewEndpoint(app, now);
                    keep = now.topic.equals(r.topic);
                } catch (Exception e) {
                    Log.w(TAG, "could not move " + r.app + " to the new account: " + e.getMessage());
                    forget(app, r.token);
                    sendUnregistered(app, r.app, r.token);
                }
                if (keep || previousServer == null || previousServer.isEmpty()
                        || previousKey == null || previousKey.isEmpty()) {
                    continue;
                }
                try {
                    PushGateway.post(app, previousServer, previousKey, PushGateway.UP_UNREGISTER, upBody(r));
                } catch (Exception e) {
                    Log.w(TAG, "gateway unregister for the previous account " + r.app + ": " + e.getMessage());
                }
            }
            PushService.reconnect(app);
        });
    }

    /**
     * UNREGISTER from an app (caller known) or removal by the person in
     * Settings (caller null). The app always hears UNREGISTERED. Runs on the
     * worker thread.
     */
    static boolean unregister(Context context, String token, String caller) {
        UnifiedPushRegistration r = get(context, token);
        if (r == null) {
            // Unknown token: nothing to remove, but the app still gets its answer.
            if (caller != null) sendUnregistered(context, caller, token);
            return false;
        }
        if (!UnifiedPushRules.mayUnregister(r.app, caller)) {
            Log.w(TAG, caller + " tried to remove a registration of " + r.app + "; ignored");
            return false;
        }
        forget(context, token);
        sendUnregistered(context, r.app, token);
        try {
            PushGateway.post(context, PushGateway.UP_UNREGISTER, upBody(r));
        } catch (Exception e) {
            Log.w(TAG, "gateway unregister " + r.app + ": " + e.getMessage());
        }
        PushService.reconnect(context);
        return true;
    }

    /** Removes every registration of {@code app}, or only {@code token} when given. */
    static int remove(Context context, String app, String token) {
        int removed = 0;
        for (UnifiedPushRegistration r : all(context)) {
            boolean match = token != null && !token.isEmpty() ? r.token.equals(token) : r.app.equals(app);
            if (match && unregister(context, r.token, null)) removed++;
        }
        return removed;
    }

    /**
     * Signing out ends every registration: each app hears UNREGISTERED, the
     * gateway is told with the key being signed out, and the components are
     * switched off.
     */
    static void signOut(Context context, String server, String key) {
        final Context app = context.getApplicationContext();
        setEnabled(app, false);
        run(() -> {
            for (UnifiedPushRegistration r : all(app)) {
                forget(app, r.token);
                sendUnregistered(app, r.app, r.token);
                if (server == null || server.isEmpty() || key == null || key.isEmpty()) continue;
                try {
                    PushGateway.post(app, server, key, PushGateway.UP_UNREGISTER, upBody(r));
                } catch (Exception e) {
                    Log.w(TAG, "gateway unregister on sign-out " + r.app + ": " + e.getMessage());
                }
            }
        });
    }

    private static JSONObject upBody(UnifiedPushRegistration r) throws Exception {
        JSONObject body = new JSONObject();
        body.put("app", r.app);
        body.put("token", r.token);
        return body;
    }

    /**
     * A message arrived on an {@code up*} topic: hand the raw bytes to the
     * app it belongs to. The app is first raised to foreground importance by
     * binding its RAISE_TO_FOREGROUND service for a few seconds, the method
     * the spec allows a distributor without privileged APIs, so it may start
     * work from the message. {@code context} is the running push service.
     */
    static void deliver(Context context, String topic, byte[] body, String messageId) {
        UnifiedPushRegistration r = byTopic(context, topic);
        if (r == null) return;
        if (!UnifiedPushRules.validMessage(body)) {
            Log.w(TAG, "dropped a message of " + (body == null ? 0 : body.length) + " bytes for " + r.app);
            return;
        }
        final Intent message = new Intent(ACTION_MESSAGE)
                .setPackage(r.app)
                .putExtra(EXTRA_TOKEN, r.token)
                .putExtra(EXTRA_BYTES_MESSAGE, body);
        if (UnifiedPushRules.validMessageId(messageId)) message.putExtra(EXTRA_ID, messageId);

        final AtomicBoolean sent = new AtomicBoolean(false);
        final Runnable send = () -> {
            if (sent.compareAndSet(false, true)) context.sendBroadcast(message);
        };
        ServiceConnection raise = new ServiceConnection() {
            @Override
            public void onServiceConnected(ComponentName name, IBinder service) {
                send.run();
            }

            @Override
            public void onServiceDisconnected(ComponentName name) {}
        };
        int flags = Context.BIND_AUTO_CREATE;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) flags |= Context.BIND_INCLUDE_CAPABILITIES;
        boolean bound = false;
        try {
            bound = context.bindService(new Intent(ACTION_RAISE_TO_FOREGROUND).setPackage(r.app), raise, flags);
        } catch (SecurityException e) {
            Log.i(TAG, r.app + " cannot be raised: " + e.getMessage());
        }
        if (!bound) {
            try {
                context.unbindService(raise);
            } catch (IllegalArgumentException ignored) {
                // Never bound.
            }
            send.run();
            return;
        }
        Handler main = new Handler(Looper.getMainLooper());
        main.postDelayed(send, RAISE_WAIT_MS);
        main.postDelayed(() -> {
            try {
                context.unbindService(raise);
            } catch (IllegalArgumentException ignored) {
                // Already unbound (the service went away).
            }
        }, RAISE_MS);
    }

    private static void sendNewEndpoint(Context context, UnifiedPushRegistration r) {
        context.sendBroadcast(new Intent(ACTION_NEW_ENDPOINT)
                .setPackage(r.app)
                .putExtra(EXTRA_TOKEN, r.token)
                .putExtra(EXTRA_ENDPOINT, r.endpoint)
                .putExtra(EXTRA_ID, UUID.randomUUID().toString()));
    }

    private static void sendFailed(Context context, String app, String token, String reason) {
        context.sendBroadcast(new Intent(ACTION_REGISTRATION_FAILED)
                .setPackage(app)
                .putExtra(EXTRA_TOKEN, token)
                .putExtra(EXTRA_REASON, reason));
    }

    private static void sendUnregistered(Context context, String app, String token) {
        context.sendBroadcast(new Intent(ACTION_UNREGISTERED)
                .setPackage(app)
                .putExtra(EXTRA_TOKEN, token));
    }

    private static boolean signedIn(Context context) {
        String key = TokenBridgePlugin.prefs(context).getString(TokenBridgePlugin.KEY_API_KEY, null);
        return key != null && !key.isEmpty();
    }

    /** The registration receiver and link activity exist only while signed in. */
    static void setEnabled(Context context, boolean enabled) {
        PackageManager pm = context.getPackageManager();
        int state = enabled
                ? PackageManager.COMPONENT_ENABLED_STATE_ENABLED
                : PackageManager.COMPONENT_ENABLED_STATE_DISABLED;
        for (Class<?> c : new Class<?>[] {UnifiedPushReceiver.class, UnifiedPushLinkActivity.class}) {
            ComponentName name = new ComponentName(context, c);
            try {
                if (pm.getComponentEnabledSetting(name) != state) {
                    pm.setComponentEnabledSetting(name, state, PackageManager.DONT_KILL_APP);
                }
            } catch (Exception e) {
                Log.w(TAG, "could not switch " + c.getSimpleName() + ": " + e.getMessage());
            }
        }
    }

    /** The name people know an app by, or its package name when it is not visible. */
    static String label(Context context, String packageName) {
        try {
            PackageManager pm = context.getPackageManager();
            ApplicationInfo info = pm.getApplicationInfo(packageName, 0);
            CharSequence label = pm.getApplicationLabel(info);
            return label == null ? packageName : label.toString();
        } catch (PackageManager.NameNotFoundException e) {
            return packageName;
        }
    }
}
