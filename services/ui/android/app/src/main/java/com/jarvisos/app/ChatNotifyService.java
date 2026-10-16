package com.jarvisos.app;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ServiceInfo;
import android.net.Uri;
import android.os.Build;
import android.os.IBinder;
import android.util.Log;
import androidx.core.app.NotificationCompat;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayDeque;
import java.util.HashMap;
import java.util.Map;
import org.json.JSONArray;
import org.json.JSONObject;

/**
 * Family chat and family notices as phone notifications, with no outside push
 * service: one long poll held open to the gateway
 * ({@code /api/telemetry/notifications/wait}), answered the moment the server
 * has something for this person, each answer posted as a system notification.
 *
 * A foreground service because nothing else is allowed to keep a connection
 * open once the app is closed; its own notification sits on a silent channel
 * that the person can hide in Android's settings without losing the messages.
 * Credentials come from TokenBridge's prefs, as for location sharing.
 */
public class ChatNotifyService extends Service {
    private static final String TAG = "ChatNotify";
    private static final String CHANNEL_CHAT = "family_chat";
    private static final String CHANNEL_NOTICES = "family_notices";
    private static final String GROUP_CHAT = "com.jarvisos.app.FAMILY_CHAT";
    private static final int SERVICE_NOTIFICATION_ID = 0x10C1;
    private static final int SUMMARY_NOTIFICATION_ID = 0x10C2;
    private static final String KEY_CURSOR = "chat_notify_cursor";
    public static final String EXTRA_OPEN_PATH = "jarvis_open_path";
    private static final int WAIT_SECONDS = 50;
    private static final int MAX_LINES = 6;

    /** The conversation open on screen, so it does not buzz while being read. */
    static volatile String activeToken = null;
    static volatile boolean appVisible = false;
    /** Recent lines per conversation, so a burst reads as one notification. */
    private static final Map<String, ArrayDeque<String>> lines = new HashMap<>();

    private volatile boolean running = false;
    private Thread worker;

    public static void start(Context context) {
        SharedPreferences p = TokenBridgePlugin.prefs(context);
        if (p.getString(TokenBridgePlugin.KEY_API_KEY, null) == null) return;
        Intent intent = new Intent(context, ChatNotifyService.class);
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
        Intent intent = new Intent(context, ChatNotifyService.class).setAction("stop");
        try {
            context.startService(intent);
        } catch (Exception e) {
            Log.w(TAG, "could not stop: " + e.getMessage());
        }
    }

    /** The conversation is being read: clear what was announced for it. */
    static void conversationOpened(Context context, String token) {
        activeToken = token;
        if (token == null) return;
        synchronized (lines) {
            lines.remove(token);
        }
        NotificationManager nm = context.getSystemService(NotificationManager.class);
        if (nm != null) nm.cancel(chatNotificationId(token));
    }

    private static int chatNotificationId(String token) {
        return ("chat:" + token).hashCode();
    }

    @Override
    public void onCreate() {
        super.onCreate();
        createChannels();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && "stop".equals(intent.getAction())) {
            running = false;
            if (worker != null) worker.interrupt();
            stopForeground(true);
            stopSelf();
            return START_NOT_STICKY;
        }
        startForegroundCompat();
        if (worker == null || !worker.isAlive()) {
            running = true;
            worker = new Thread(this::loop, "chat-notify");
            worker.start();
        }
        return START_STICKY;
    }

    private void loop() {
        long backoffMs = 5_000L;
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
            try {
                String base = server.endsWith("/") ? server.substring(0, server.length() - 1) : server;
                String cursor = p.getString(KEY_CURSOR, "");
                String url = base + "/api/telemetry/notifications/wait?timeout=" + WAIT_SECONDS
                        + "&since=" + URLEncoder.encode(cursor, "UTF-8");
                HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
                conn.setConnectTimeout(15_000);
                conn.setReadTimeout((WAIT_SECONDS + 20) * 1000);
                conn.setRequestProperty("Authorization", "Bearer " + token);
                int status = conn.getResponseCode();
                if (status == 401 || status == 403) {
                    conn.disconnect();
                    Log.w(TAG, "not authorised (" + status + "); retrying in 5 min");
                    sleep(5 * 60_000L);
                    continue;
                }
                if (status != 200) {
                    conn.disconnect();
                    throw new Exception("HTTP " + status);
                }
                JSONObject body = new JSONObject(read(conn.getInputStream()));
                conn.disconnect();
                JSONArray items = body.optJSONArray("notifications");
                if (items != null) {
                    for (int i = 0; i < items.length(); i++) {
                        JSONObject n = items.optJSONObject(i);
                        if (n != null) show(n);
                    }
                }
                String next = body.optString("cursor", cursor);
                if (!next.equals(cursor)) p.edit().putString(KEY_CURSOR, next).apply();
                backoffMs = 5_000L;
            } catch (InterruptedException e) {
                return;
            } catch (Exception e) {
                Log.w(TAG, "wait failed: " + e.getMessage() + "; retry in " + (backoffMs / 1000) + "s");
                sleep(backoffMs);
                backoffMs = Math.min(backoffMs * 2, 60_000L);
            }
        }
    }

    private void sleep(long ms) {
        try {
            Thread.sleep(ms);
        } catch (InterruptedException ignored) {
            Thread.currentThread().interrupt();
        }
    }

    private static String read(InputStream in) throws Exception {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        int n;
        while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
        in.close();
        return out.toString(StandardCharsets.UTF_8.name());
    }

    private void show(JSONObject n) {
        NotificationManager nm = getSystemService(NotificationManager.class);
        if (nm == null) return;
        String kind = n.optString("kind", "notice");
        String title = n.optString("title", "Jarvis");
        String text = n.optString("body", "");
        JSONObject data = n.optJSONObject("data");
        String path = data != null ? data.optString("url", "") : "";

        if ("chat".equals(kind) && data != null) {
            String token = data.optString("token", "");
            if (token.isEmpty()) return;
            if (appVisible && token.equals(activeToken)) return;  // being read right now
            int id = chatNotificationId(token);
            ArrayDeque<String> recent;
            synchronized (lines) {
                recent = lines.get(token);
                if (recent == null) {
                    recent = new ArrayDeque<>();
                    lines.put(token, recent);
                }
                recent.addLast(text);
                while (recent.size() > MAX_LINES) recent.removeFirst();
                recent = new ArrayDeque<>(recent);
            }
            NotificationCompat.Builder b = new NotificationCompat.Builder(this, CHANNEL_CHAT)
                    .setSmallIcon(android.R.drawable.sym_action_chat)
                    .setContentTitle(title)
                    .setContentText(text)
                    .setCategory(NotificationCompat.CATEGORY_MESSAGE)
                    .setPriority(NotificationCompat.PRIORITY_HIGH)
                    .setAutoCancel(true)
                    .setGroup(GROUP_CHAT)
                    .setNumber(recent.size())
                    .setContentIntent(openIntent(path.isEmpty() ? "/family?chat=" + token : path, id));
            if (recent.size() > 1) {
                NotificationCompat.InboxStyle style = new NotificationCompat.InboxStyle();
                for (String line : recent) style.addLine(line);
                style.setSummaryText(recent.size() + " new messages");
                b.setStyle(style);
            } else {
                b.setStyle(new NotificationCompat.BigTextStyle().bigText(text));
            }
            nm.notify(id, b.build());
            nm.notify(SUMMARY_NOTIFICATION_ID, new NotificationCompat.Builder(this, CHANNEL_CHAT)
                    .setSmallIcon(android.R.drawable.sym_action_chat)
                    .setContentTitle("Family chat")
                    .setGroup(GROUP_CHAT)
                    .setGroupSummary(true)
                    .setAutoCancel(true)
                    .setContentIntent(openIntent("/family", SUMMARY_NOTIFICATION_ID))
                    .build());
            return;
        }

        int id = n.optString("id", title + text).hashCode();
        nm.notify(id, new NotificationCompat.Builder(this, CHANNEL_NOTICES)
                .setSmallIcon(android.R.drawable.ic_dialog_info)
                .setContentTitle(title)
                .setContentText(text)
                .setStyle(new NotificationCompat.BigTextStyle().bigText(text))
                .setAutoCancel(true)
                .setContentIntent(openIntent(path.isEmpty() ? "/" : path, id))
                .build());
    }

    private PendingIntent openIntent(String path, int requestCode) {
        Intent intent = new Intent(this, MainActivity.class)
                .setAction(Intent.ACTION_VIEW)
                // A distinct data URI per target, so PendingIntents for two
                // conversations are never merged into one.
                .setData(Uri.parse("jarvis://open" + path))
                .putExtra(EXTRA_OPEN_PATH, path)
                .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        int flags = PendingIntent.FLAG_UPDATE_CURRENT
                | (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M ? PendingIntent.FLAG_IMMUTABLE : 0);
        return PendingIntent.getActivity(this, requestCode, intent, flags);
    }

    private void createChannels() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return;
        NotificationManager nm = getSystemService(NotificationManager.class);
        if (nm == null) return;
        if (nm.getNotificationChannel(CHANNEL_CHAT) == null) {
            NotificationChannel ch = new NotificationChannel(
                    CHANNEL_CHAT, "Family chat", NotificationManager.IMPORTANCE_HIGH);
            ch.setDescription("New messages in your family conversations.");
            nm.createNotificationChannel(ch);
        }
        if (nm.getNotificationChannel(CHANNEL_NOTICES) == null) {
            NotificationChannel ch = new NotificationChannel(
                    CHANNEL_NOTICES, "Family notices", NotificationManager.IMPORTANCE_DEFAULT);
            ch.setDescription("Arrivals, departures, reminders and reports.");
            nm.createNotificationChannel(ch);
        }
    }

    private void startForegroundCompat() {
        Notification notification = BackgroundNotice.build(
                this, android.R.drawable.sym_action_chat, "Listening for family messages",
                openIntent("/family", SERVICE_NOTIFICATION_ID));
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
        running = false;
        if (worker != null) worker.interrupt();
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
