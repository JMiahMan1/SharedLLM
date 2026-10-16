package com.jarvisos.app;

import android.app.Notification;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ServiceInfo;
import android.net.ConnectivityManager;
import android.net.Network;
import android.os.Build;
import android.os.IBinder;
import android.util.Base64;
import android.util.Log;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.SocketTimeoutException;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;

/**
 * Every Jarvis notification and every UnifiedPush message for this phone,
 * over one connection and with no outside push service: the gateway's
 * {@code GET /api/push/stream}, ntfy's line-delimited JSON for the person's
 * topic plus the {@code up*} topics registered from this device
 * (docs/PUSH.md).
 *
 * Jarvis notices go to {@link PushNotifier}; messages on an {@code up*} topic
 * go to the app that registered it ({@link UnifiedPushDistributor}). After a
 * drop the stream reconnects with backoff and asks for {@code since} the last
 * message read, so nothing in ntfy's cache is missed.
 *
 * A foreground service because nothing else may keep a connection open once
 * the app is closed; its notice is the hidden {@link BackgroundNotice}.
 * Credentials come from TokenBridge's prefs, as for location sharing.
 */
public class PushService extends Service {
    private static final String TAG = "PushService";
    private static final int SERVICE_NOTIFICATION_ID = 0x10C1;
    private static final String ACTION_STOP = "stop";
    private static final String ACTION_RECONNECT = "reconnect";
    /** ntfy sends a keepalive every 45 s; silence for longer means a dead link. */
    private static final int READ_TIMEOUT_MS = 120_000;
    private static final long MIN_BACKOFF_MS = 2_000L;
    private static final long MAX_BACKOFF_MS = 5 * 60_000L;
    private static final long UNAUTHORISED_WAIT_MS = 5 * 60_000L;
    /** The long poll's cursor from earlier builds; meaningless to the stream. */
    private static final String RETIRED_CURSOR = "chat_notify_cursor";

    private volatile boolean running = false;
    private volatile boolean reconnectNow = false;
    private volatile HttpURLConnection current;
    private final Object pause = new Object();
    private Thread worker;
    private ConnectivityManager.NetworkCallback networkCallback;

    public static void start(Context context) {
        SharedPreferences p = TokenBridgePlugin.prefs(context);
        if (p.getString(TokenBridgePlugin.KEY_API_KEY, null) == null) return;
        UnifiedPushDistributor.setEnabled(context, true);
        Intent intent = new Intent(context, PushService.class);
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent);
            } else {
                context.startService(intent);
            }
        } catch (Exception e) {
            // Background-start restrictions: the next time the app opens starts it.
            Log.w(TAG, "could not start: " + e.getMessage());
        }
    }

    public static void stop(Context context) {
        PushNotifier.clearConversations();
        command(context, ACTION_STOP);
    }

    /** Open the stream again at once, so it covers a newly registered topic. */
    static void reconnect(Context context) {
        command(context, ACTION_RECONNECT);
    }

    private static void command(Context context, String action) {
        try {
            context.startService(new Intent(context, PushService.class).setAction(action));
        } catch (Exception e) {
            Log.w(TAG, "could not send " + action + ": " + e.getMessage());
        }
    }

    @Override
    public void onCreate() {
        super.onCreate();
        PushNotifier.ensureChannels(this);
        TokenBridgePlugin.prefs(this).edit().remove(RETIRED_CURSOR).apply();
        watchNetwork();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent != null ? intent.getAction() : null;
        if (ACTION_STOP.equals(action)) {
            shutDown();
            stopForeground(true);
            stopSelf();
            return START_NOT_STICKY;
        }
        if (ACTION_RECONNECT.equals(action) && worker != null && worker.isAlive()) {
            reconnectNow = true;
            dropConnection();
            wake();
            return START_STICKY;
        }
        startForegroundCompat();
        if (worker == null || !worker.isAlive()) {
            running = true;
            worker = new Thread(this::loop, "push-stream");
            worker.start();
        }
        return START_STICKY;
    }

    private void loop() {
        long backoffMs = MIN_BACKOFF_MS;
        while (running) {
            SharedPreferences p = TokenBridgePlugin.prefs(this);
            String token = p.getString(TokenBridgePlugin.KEY_API_KEY, null);
            String server = p.getString(TokenBridgePlugin.KEY_SERVER_URL, null);
            if (token == null || server == null || token.isEmpty() || server.isEmpty()) {
                Log.i(TAG, "signed out; stopping");
                running = false;
                stopSelf();
                return;
            }
            HttpURLConnection conn = null;
            try {
                String base = server.endsWith("/") ? server.substring(0, server.length() - 1) : server;
                String since = p.getString(TokenBridgePlugin.KEY_PUSH_SINCE, "");
                String url = base + PushGateway.STREAM
                        + (since.isEmpty() ? "" : "?since=" + URLEncoder.encode(since, "UTF-8"));
                conn = (HttpURLConnection) new URL(url).openConnection();
                conn.setConnectTimeout(15_000);
                conn.setReadTimeout(READ_TIMEOUT_MS);
                conn.setRequestProperty("Authorization", "Bearer " + token);
                conn.setRequestProperty("X-Device-Id", PushGateway.deviceId(this));
                conn.setRequestProperty("Accept", "application/x-ndjson");
                current = conn;
                int status = conn.getResponseCode();
                if (status == 401 || status == 403) {
                    Log.w(TAG, "not authorised (" + status + "); retrying in 5 min");
                    sleep(UNAUTHORISED_WAIT_MS);
                    continue;
                }
                if (status != 200) throw new Exception("HTTP " + status);
                BufferedReader in = new BufferedReader(
                        new InputStreamReader(conn.getInputStream(), StandardCharsets.UTF_8));
                String line;
                while (running && (line = in.readLine()) != null) {
                    PushEvent event = PushEvent.parse(line);
                    if (event == null) continue;
                    if (event.isLiveness()) backoffMs = MIN_BACKOFF_MS;
                    if (!event.isMessage()) continue;
                    handle(event);
                    if (!event.id.isEmpty()) p.edit().putString(TokenBridgePlugin.KEY_PUSH_SINCE, event.id).apply();
                }
                // The server ended the stream (a deploy, a proxy timeout): come straight back.
                in.close();
                backoffMs = MIN_BACKOFF_MS;
                if (!reconnectNow) sleep(MIN_BACKOFF_MS);
            } catch (Exception e) {
                if (!running) return;
                if (reconnectNow) {
                    Log.i(TAG, "reconnecting for new registrations");
                } else {
                    String why = e instanceof SocketTimeoutException ? "no keepalive" : e.getMessage();
                    Log.w(TAG, "stream lost (" + why + "); retry in " + (backoffMs / 1000) + "s");
                    sleep(backoffMs);
                    backoffMs = Math.min(backoffMs * 2, MAX_BACKOFF_MS);
                }
            } finally {
                current = null;
                reconnectNow = false;
                if (conn != null) conn.disconnect();
            }
        }
    }

    private void handle(PushEvent event) {
        try {
            if (event.isUnifiedPush()) {
                byte[] body = event.base64
                        ? Base64.decode(event.message, Base64.DEFAULT)
                        : event.message.getBytes(StandardCharsets.UTF_8);
                UnifiedPushDistributor.deliver(this, event.topic, body, event.id);
                return;
            }
            PushNotice notice = PushNotice.parse(event.message);
            if (notice == null) notice = PushNotice.plain(event.id, event.title, event.message);
            PushNotifier.show(this, notice);
        } catch (Exception e) {
            // One bad message must not end the stream.
            Log.w(TAG, "could not handle message " + event.id + ": " + e.getMessage());
        }
    }

    /** Waits, unless a reconnect request or a returning network ends the wait early. */
    private void sleep(long ms) {
        synchronized (pause) {
            if (reconnectNow) return;
            try {
                pause.wait(ms);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                running = false;
            }
        }
    }

    private void wake() {
        synchronized (pause) {
            pause.notifyAll();
        }
    }

    private void dropConnection() {
        HttpURLConnection conn = current;
        if (conn != null) {
            // Unblocks the read on the worker thread; disconnect may touch the network.
            new Thread(conn::disconnect, "push-drop").start();
        }
    }

    /** Back on a network: try now rather than at the end of a long backoff. */
    private void watchNetwork() {
        ConnectivityManager cm = getSystemService(ConnectivityManager.class);
        if (cm == null) return;
        networkCallback = new ConnectivityManager.NetworkCallback() {
            @Override
            public void onAvailable(Network network) {
                wake();
            }
        };
        try {
            cm.registerDefaultNetworkCallback(networkCallback);
        } catch (Exception e) {
            Log.w(TAG, "no network callback: " + e.getMessage());
            networkCallback = null;
        }
    }

    private void shutDown() {
        running = false;
        dropConnection();
        wake();
        if (worker != null) worker.interrupt();
    }

    private void startForegroundCompat() {
        Notification notification = BackgroundNotice.build(
                this, R.drawable.mi_notifications, "Connected for notifications",
                PushNotifier.openIntent(this, "/settings", SERVICE_NOTIFICATION_ID));
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
                startForeground(SERVICE_NOTIFICATION_ID, notification,
                        ServiceInfo.FOREGROUND_SERVICE_TYPE_REMOTE_MESSAGING);
            } else {
                startForeground(SERVICE_NOTIFICATION_ID, notification);
            }
        } catch (Exception e) {
            Log.w(TAG, "startForeground refused: " + e.getMessage());
        }
    }

    @Override
    public void onDestroy() {
        shutDown();
        if (networkCallback != null) {
            ConnectivityManager cm = getSystemService(ConnectivityManager.class);
            try {
                if (cm != null) cm.unregisterNetworkCallback(networkCallback);
            } catch (Exception ignored) {
                // Already gone.
            }
        }
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
