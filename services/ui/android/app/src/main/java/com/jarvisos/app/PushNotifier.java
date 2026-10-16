package com.jarvisos.app;

import android.app.NotificationChannel;
import android.app.NotificationChannelGroup;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.service.notification.StatusBarNotification;
import androidx.core.app.NotificationCompat;
import androidx.core.app.Person;
import androidx.core.app.RemoteInput;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Turns Jarvis notices into Android notifications: one channel per category
 * ({@link PushCategories}), a tag replaces the notification with that tag,
 * {@code data.url} opens that page in the app, and actions become buttons
 * (view opens a page, callback and reply go through {@link PushActionReceiver}).
 *
 * Chat messages are grouped per conversation in a MessagingStyle notification
 * with inline reply, and cleared when the conversation is opened in the app.
 */
final class PushNotifier {
    /** A tapped notification carries the page to open (MainActivity reads it). */
    static final String EXTRA_OPEN_PATH = "jarvis_open_path";

    private static final String GROUP_CHAT = "com.jarvisos.app.FAMILY_CHAT";
    private static final int SUMMARY_NOTIFICATION_ID = 0x10C2;
    private static final int MAX_LINES = 6;

    /** The conversation open on screen, so it does not buzz while being read. */
    static volatile String activeToken = null;
    static volatile boolean appVisible = false;

    /** One line of a conversation; a null sender is the person themselves. */
    private static final class Line {
        final String sender;
        final CharSequence text;
        final long when;

        Line(String sender, CharSequence text, long when) {
            this.sender = sender;
            this.text = text;
            this.when = when;
        }
    }

    /** Recent lines per conversation, so a burst reads as one notification. */
    private static final Map<String, ArrayDeque<Line>> conversations = new HashMap<>();
    /** The latest notice per conversation, to rebuild it after an inline reply. */
    private static final Map<String, PushNotice> latest = new HashMap<>();
    private static volatile boolean channelsReady = false;

    private PushNotifier() {}

    static void show(Context context, PushNotice n) {
        ensureChannels(context);
        NotificationManager nm = context.getSystemService(NotificationManager.class);
        if (nm == null) return;
        String token = n.chatToken();
        if (token != null) {
            showChat(context, nm, n, token);
            return;
        }
        int id = n.notificationId();
        NotificationCompat.Builder b = new NotificationCompat.Builder(
                context, PushCategories.channelId(n.category, n.priority))
                .setSmallIcon(icon(n.category))
                .setContentTitle(n.title)
                .setContentText(n.body)
                .setStyle(new NotificationCompat.BigTextStyle().bigText(n.body))
                .setPriority(PushCategories.compatPriority(n.priority))
                .setCategory(androidCategory(n.category, n.priority))
                .setAutoCancel(true)
                .setShowWhen(true)
                .setContentIntent(openIntent(context, n.path.isEmpty() ? "/" : n.path, id));
        if (PushCategories.silent(n.priority)) b.setSilent(true);
        addActions(context, b, n.actions, n, id, null);
        nm.notify(id, b.build());
    }

    private static void showChat(Context context, NotificationManager nm, PushNotice n, String token) {
        if (appVisible && token.equals(activeToken)) return;  // being read right now
        List<Line> recent;
        synchronized (conversations) {
            recent = remember(token, new Line(n.title, n.body, System.currentTimeMillis()));
            latest.put(token, n);
        }
        nm.notify(PushNotice.chatNotificationId(token), chat(context, n, token, recent, false).build());
        nm.notify(SUMMARY_NOTIFICATION_ID, new NotificationCompat.Builder(
                context, PushCategories.channelId(PushCategories.CHAT, n.priority))
                .setSmallIcon(R.drawable.mi_chat)
                .setContentTitle("Family chat")
                .setGroup(GROUP_CHAT)
                .setGroupSummary(true)
                .setAutoCancel(true)
                .setContentIntent(openIntent(context, "/family", SUMMARY_NOTIFICATION_ID))
                .build());
    }

    private static List<Line> remember(String token, Line line) {
        ArrayDeque<Line> lines = conversations.get(token);
        if (lines == null) {
            lines = new ArrayDeque<>();
            conversations.put(token, lines);
        }
        lines.addLast(line);
        while (lines.size() > MAX_LINES) lines.removeFirst();
        return new ArrayList<>(lines);
    }

    private static NotificationCompat.Builder chat(
            Context context, PushNotice n, String token, List<Line> recent, boolean quiet) {
        int id = PushNotice.chatNotificationId(token);
        NotificationCompat.MessagingStyle style = new NotificationCompat.MessagingStyle(
                new Person.Builder().setName("You").build());
        for (Line line : recent) {
            style.addMessage(line.text, line.when,
                    line.sender == null ? (Person) null : new Person.Builder().setName(line.sender).build());
        }
        CharSequence last = recent.isEmpty() ? n.body : recent.get(recent.size() - 1).text;
        NotificationCompat.Builder b = new NotificationCompat.Builder(
                context, PushCategories.channelId(PushCategories.CHAT, n.priority))
                .setSmallIcon(R.drawable.mi_chat)
                .setContentTitle(n.title)
                .setContentText(last)
                .setStyle(style)
                .setCategory(NotificationCompat.CATEGORY_MESSAGE)
                .setPriority(PushCategories.compatPriority(Math.max(n.priority, 4)))
                .setAutoCancel(true)
                .setGroup(GROUP_CHAT)
                .setNumber(recent.size())
                .setContentIntent(openIntent(context, n.path.isEmpty() ? "/family?chat=" + token : n.path, id));
        if (quiet) b.setSilent(true);
        addActions(context, b, n.actions, n, id, token);
        return b;
    }

    /** The conversation is being read: clear what was announced for it. */
    static void conversationOpened(Context context, String token) {
        activeToken = token;
        if (token == null) return;
        synchronized (conversations) {
            conversations.remove(token);
            latest.remove(token);
        }
        NotificationManager nm = context.getSystemService(NotificationManager.class);
        if (nm != null) nm.cancel(PushNotice.chatNotificationId(token));
    }

    private static void addActions(Context context, NotificationCompat.Builder b, List<PushNotice.Action> actions,
                                   PushNotice n, int id, String chatToken) {
        for (int i = 0; i < actions.size(); i++) {
            PushNotice.Action a = actions.get(i);
            if (a.isView()) {
                PendingIntent pi = a.isExternal()
                        ? browserIntent(context, a.url, id + i + 1)
                        : openIntent(context, a.url, id + i + 1);
                b.addAction(new NotificationCompat.Action.Builder(0, a.label, pi).build());
                continue;
            }
            Intent intent = PushActionReceiver.intent(context, a, n, id, i, chatToken);
            if (a.isReply()) {
                // RemoteInput fills the reply into the intent, so it must stay mutable.
                int flags = PendingIntent.FLAG_UPDATE_CURRENT
                        | (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S ? PendingIntent.FLAG_MUTABLE : 0);
                PendingIntent pi = PendingIntent.getBroadcast(context, id, intent, flags);
                RemoteInput input = new RemoteInput.Builder(PushActionReceiver.KEY_REPLY)
                        .setLabel(a.label)
                        .build();
                b.addAction(new NotificationCompat.Action.Builder(R.drawable.mi_chat, a.label, pi)
                        .addRemoteInput(input)
                        .setSemanticAction(NotificationCompat.Action.SEMANTIC_ACTION_REPLY)
                        .setShowsUserInterface(false)
                        .build());
            } else {
                PendingIntent pi = PendingIntent.getBroadcast(context, id, intent,
                        PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
                b.addAction(new NotificationCompat.Action.Builder(0, a.label, pi)
                        .setShowsUserInterface(false)
                        .build());
            }
        }
    }

    /**
     * A callback or reply button finished. Android shows a spinner on a reply
     * until the notification is posted again, so every outcome updates it: a
     * chat gets the reply as the person's own line, another notice says the
     * reply was sent, a plain callback that worked clears the notice, and a
     * failure says so and stays.
     */
    static void actionFinished(Context context, Intent intent, boolean ok, CharSequence reply) {
        NotificationManager nm = context.getSystemService(NotificationManager.class);
        if (nm == null) return;
        int id = intent.getIntExtra(PushActionReceiver.EXTRA_NOTIFICATION_ID, 0);
        String token = intent.getStringExtra(PushActionReceiver.EXTRA_CHAT_TOKEN);
        boolean isReply = intent.getBooleanExtra(PushActionReceiver.EXTRA_IS_REPLY, false);
        if (token != null && !token.isEmpty()) {
            PushNotice n;
            List<Line> recent;
            synchronized (conversations) {
                n = latest.get(token);
                CharSequence text = reply == null ? "" : reply;
                if (!ok) text = "Not sent: " + text;
                recent = remember(token, new Line(null, text, System.currentTimeMillis()));
            }
            if (n == null) {
                n = PushNotice.plain("", intent.getStringExtra(PushActionReceiver.EXTRA_TITLE), "");
            }
            nm.notify(id, chat(context, n, token, recent, true).build());
            return;
        }
        if (ok && !isReply) {
            nm.cancel(id);
            return;
        }
        String title = intent.getStringExtra(PushActionReceiver.EXTRA_TITLE);
        String category = intent.getStringExtra(PushActionReceiver.EXTRA_CATEGORY);
        String path = intent.getStringExtra(PushActionReceiver.EXTRA_PATH);
        String text = ok
                ? "You replied: " + reply
                : "Could not reach Jarvis. Tap to open the app.";
        nm.notify(id, new NotificationCompat.Builder(context, PushCategories.channelId(category, 3))
                .setSmallIcon(icon(category))
                .setContentTitle(title == null || title.isEmpty() ? "Jarvis" : title)
                .setContentText(text)
                .setStyle(new NotificationCompat.BigTextStyle().bigText(text))
                .setSilent(true)
                .setAutoCancel(true)
                .setContentIntent(openIntent(context, path == null || path.isEmpty() ? "/" : path, id))
                .build());
    }

    /** Opens a page of the app, as a notification tap or a view button. */
    static PendingIntent openIntent(Context context, String path, int requestCode) {
        Intent intent = new Intent(context, MainActivity.class)
                .setAction(Intent.ACTION_VIEW)
                // A distinct data URI per target, so PendingIntents for two
                // pages are never merged into one.
                .setData(Uri.parse("jarvis://open" + path))
                .putExtra(EXTRA_OPEN_PATH, path)
                .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        int flags = PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE;
        return PendingIntent.getActivity(context, requestCode, intent, flags);
    }

    /** Opens a web page from a view button (SOS directions) in the browser. */
    static PendingIntent browserIntent(Context context, String url, int requestCode) {
        Intent intent = new Intent(Intent.ACTION_VIEW, Uri.parse(url))
                .addCategory(Intent.CATEGORY_BROWSABLE)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        int flags = PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE;
        return PendingIntent.getActivity(context, requestCode, intent, flags);
    }

    /** One channel per category, grouped as in Settings; earlier builds' channels removed. */
    static void ensureChannels(Context context) {
        if (channelsReady || Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return;
        NotificationManager nm = context.getSystemService(NotificationManager.class);
        if (nm == null) return;
        for (String retired : PushCategories.RETIRED_CHANNELS) {
            if (nm.getNotificationChannel(retired) != null) nm.deleteNotificationChannel(retired);
        }
        nm.createNotificationChannelGroups(Arrays.asList(
                new NotificationChannelGroup(PushCategories.GROUP_FAMILY, "Family"),
                new NotificationChannelGroup(PushCategories.GROUP_REMINDERS, "Reminders"),
                new NotificationChannelGroup(PushCategories.GROUP_HOME, "Home and system")));
        List<NotificationChannel> channels = new ArrayList<>();
        for (PushCategories.Category c : PushCategories.ALL) {
            // Creating an existing channel only refreshes its name and
            // description; what the person changed in Android stays.
            NotificationChannel ch = new NotificationChannel(c.channelId(), c.name, importance(c.importance));
            ch.setDescription(c.description);
            if (c.group != null) ch.setGroup(c.group);
            channels.add(ch);
        }
        nm.createNotificationChannels(channels);
        channelsReady = true;
    }

    private static int importance(int level) {
        if (level >= PushCategories.POP_UP) return NotificationManager.IMPORTANCE_HIGH;
        if (level <= PushCategories.QUIET) return NotificationManager.IMPORTANCE_LOW;
        return NotificationManager.IMPORTANCE_DEFAULT;
    }

    private static String androidCategory(String category, int priority) {
        if (PushNotice.clampPriority(priority) >= 5) return NotificationCompat.CATEGORY_ALARM;
        switch (category == null ? "" : category) {
            case "timers":
                return NotificationCompat.CATEGORY_ALARM;
            case "calendar":
                return NotificationCompat.CATEGORY_EVENT;
            case "leave_by":
            case "bible":
                return NotificationCompat.CATEGORY_REMINDER;
            case "admin":
            case "app_updates":
            case "jobs":
            case "reports":
                return NotificationCompat.CATEGORY_STATUS;
            default:
                return null;
        }
    }

    private static int icon(String category) {
        switch (category == null ? "" : category) {
            case "chat":
                return R.drawable.mi_chat;
            case "family_presence":
            case "home":
                return R.drawable.mi_home;
            case "family_safety":
                return R.drawable.mi_shield;
            case "leave_by":
                return R.drawable.mi_car;
            case "bible":
                return R.drawable.mi_bookmark;
            case "health":
                return R.drawable.mi_favorite;
            case "jobs":
                return R.drawable.mi_star;
            case "raven":
                return R.drawable.mi_flag;
            case "app_updates":
            case "admin":
                return R.drawable.mi_settings;
            default:
                return R.drawable.mi_notifications;
        }
    }

    /**
     * Removes every Jarvis notice on show (another person signed in on this
     * phone). Only the push channels are touched; the background and media
     * notices stay.
     */
    static void clearAll(Context context) {
        clearConversations();
        NotificationManager nm = context.getSystemService(NotificationManager.class);
        if (nm == null) return;
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            // No channels to tell them apart; foreground services' notices survive cancelAll.
            nm.cancelAll();
            return;
        }
        for (StatusBarNotification sbn : nm.getActiveNotifications()) {
            if (PushCategories.isPushChannel(sbn.getNotification().getChannelId())) {
                nm.cancel(sbn.getTag(), sbn.getId());
            }
        }
    }

    /** Nothing left to show once signed out. */
    static void clearConversations() {
        synchronized (conversations) {
            conversations.clear();
            latest.clear();
        }
    }
}
