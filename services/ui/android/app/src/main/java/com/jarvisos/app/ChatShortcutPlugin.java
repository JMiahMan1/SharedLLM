package com.jarvisos.app;

import android.content.Context;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Rect;
import android.graphics.Typeface;
import android.net.Uri;
import android.util.Base64;
import androidx.core.app.Person;
import androidx.core.content.pm.ShortcutInfoCompat;
import androidx.core.content.pm.ShortcutManagerCompat;
import androidx.core.graphics.drawable.IconCompat;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * Home-screen shortcuts for chat conversations (JS: JarvisChatShortcut).
 *
 * A pinned shortcut shows the other person's name and picture (or their
 * initials on their colour) and opens MainActivity with the conversation's
 * page in {@link PushNotifier#EXTRA_OPEN_PATH}, exactly as a notification tap
 * does. The same shortcut is published as a long-lived conversation shortcut,
 * so it is also on a long-press of the Jarvis icon.
 *
 * A shortcut carries a name, a picture and a page only: never message text
 * and never anything that opens a private chat. Its page is the chat itself,
 * which opens on the lock screen unless it is already unlocked in the app.
 */
@CapacitorPlugin(name = "JarvisChatShortcut")
public class ChatShortcutPlugin extends Plugin {

    /** Ids are "chat:" plus the conversation's token, so adding again updates rather than duplicates. */
    static final String ID_PREFIX = "chat:";
    /** The only pages a chat shortcut may open: a conversation in the Family hub. */
    static final String PATH_PREFIX = "/family?chat=";
    /** Long-press lists stay short; Android also caps them (getMaxShortcutCountPerActivity). */
    static final int MAX_DYNAMIC = 4;
    /** Adaptive icon canvas: 108dp at 4x. The launcher shows the middle 72dp. */
    static final int ICON_PX = 432;
    static final int VISIBLE_PX = 288;

    @PluginMethod
    public void supported(PluginCall call) {
        JSObject ret = new JSObject();
        ret.put("supported", ShortcutManagerCompat.isRequestPinShortcutSupported(getContext()));
        call.resolve(ret);
    }

    @PluginMethod
    public void pin(PluginCall call) {
        String id = call.getString("id", "");
        String label = call.getString("label", "");
        String path = call.getString("path", "");
        if (!validId(id) || !validPath(path) || label == null || label.trim().isEmpty()) {
            call.reject("A chat shortcut needs a chat id, a name and a chat page");
            return;
        }
        Context ctx = getContext();
        try {
            ShortcutInfoCompat info = build(ctx, id, label.trim(), path,
                    parseColour(call.getString("color", "")),
                    call.getString("monogram", ""),
                    decode(call.getString("icon", "")));
            keepRoomFor(ctx, id);
            ShortcutManagerCompat.pushDynamicShortcut(ctx, info);

            JSObject ret = new JSObject();
            if (isPinned(ctx, id)) {
                // On the home screen already (perhaps greyed out after a delete): bring it back up to date.
                ShortcutManagerCompat.enableShortcuts(ctx, Collections.singletonList(info));
                ShortcutManagerCompat.updateShortcuts(ctx, Collections.singletonList(info));
                ret.put("updated", true);
                ret.put("requested", false);
            } else if (ShortcutManagerCompat.isRequestPinShortcutSupported(ctx)) {
                ret.put("updated", false);
                ret.put("requested", ShortcutManagerCompat.requestPinShortcut(ctx, info, null));
            } else {
                ret.put("updated", false);
                ret.put("requested", false);
            }
            call.resolve(ret);
        } catch (Exception e) {
            call.reject("Could not add the shortcut: " + e.getMessage());
        }
    }

    /** The conversation is gone: the pinned shortcut greys out with the message, and leaves the long-press list. */
    @PluginMethod
    public void disable(PluginCall call) {
        String id = call.getString("id", "");
        if (!validId(id)) {
            call.reject("Not a chat shortcut");
            return;
        }
        String message = call.getString("message", "This chat is no longer available");
        List<String> ids = Collections.singletonList(id);
        try {
            ShortcutManagerCompat.disableShortcuts(getContext(), ids, message);
            ShortcutManagerCompat.removeLongLivedShortcuts(getContext(), ids);
        } catch (Exception ignored) {
            // Nothing pinned or published for it.
        }
        call.resolve();
    }

    static boolean validId(String id) {
        return id != null && id.startsWith(ID_PREFIX) && id.length() > ID_PREFIX.length();
    }

    static boolean validPath(String path) {
        return path != null && path.startsWith(PATH_PREFIX) && path.length() > PATH_PREFIX.length();
    }

    private static ShortcutInfoCompat build(Context ctx, String id, String label, String path, int colour,
                                            String monogram, Bitmap picture) {
        IconCompat icon = IconCompat.createWithAdaptiveBitmap(iconBitmap(picture, colour, monogram, label));
        Intent intent = new Intent(ctx, MainActivity.class)
                .setAction(Intent.ACTION_VIEW)
                // One data URI per conversation, like a notification's, so two are never merged.
                .setData(Uri.parse("jarvis://open" + path))
                .putExtra(PushNotifier.EXTRA_OPEN_PATH, path)
                // MainActivity is singleTask: a running Jarvis gets onNewIntent and opens the chat.
                .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        Person person = new Person.Builder().setName(label).setKey(id).setIcon(icon).build();
        return new ShortcutInfoCompat.Builder(ctx, id)
                .setShortLabel(label)
                .setLongLabel(label)
                .setIcon(icon)
                .setIntent(intent)
                .setPerson(person)
                .setIsConversation()
                .setLongLived(true)
                .setRank(0)
                .build();
    }

    /** Whether the launcher already has this shortcut on the home screen. */
    private static boolean isPinned(Context ctx, String id) {
        for (ShortcutInfoCompat s : ShortcutManagerCompat.getShortcuts(ctx, ShortcutManagerCompat.FLAG_MATCH_PINNED)) {
            if (id.equals(s.getId())) return true;
        }
        return false;
    }

    /** Keeps the long-press list within its cap by dropping the conversation added longest ago. */
    private static void keepRoomFor(Context ctx, String id) {
        int cap = Math.max(1, Math.min(MAX_DYNAMIC, ShortcutManagerCompat.getMaxShortcutCountPerActivity(ctx)));
        List<ShortcutInfoCompat> dynamic = new ArrayList<>(ShortcutManagerCompat.getDynamicShortcuts(ctx));
        for (ShortcutInfoCompat s : dynamic) {
            if (id.equals(s.getId())) return;
        }
        List<String> drop = new ArrayList<>();
        while (dynamic.size() - drop.size() >= cap) {
            ShortcutInfoCompat oldest = null;
            for (ShortcutInfoCompat s : dynamic) {
                if (drop.contains(s.getId())) continue;
                if (oldest == null || s.getLastChangedTimestamp() < oldest.getLastChangedTimestamp()) oldest = s;
            }
            if (oldest == null) break;
            drop.add(oldest.getId());
        }
        if (!drop.isEmpty()) ShortcutManagerCompat.removeDynamicShortcuts(ctx, drop);
    }

    private static Bitmap decode(String base64) {
        if (base64 == null || base64.isEmpty()) return null;
        try {
            byte[] bytes = Base64.decode(base64, Base64.DEFAULT);
            return BitmapFactory.decodeByteArray(bytes, 0, bytes.length);
        } catch (IllegalArgumentException e) {
            return null;
        }
    }

    /** #rrggbb from the web app; a calm purple when it is missing or not a colour. */
    static int parseColour(String value) {
        try {
            if (value != null && value.matches("#[0-9a-fA-F]{6}")) return Color.parseColor(value);
        } catch (IllegalArgumentException ignored) {
            // The default below.
        }
        return 0xFF6D28D9;
    }

    /** One or two letters for the monogram: the web app's, else the name's first letters. */
    static String monogramFor(String monogram, String label) {
        String m = monogram == null ? "" : monogram.trim();
        if (m.isEmpty()) {
            String[] parts = label.trim().split("\\s+");
            if (parts.length == 0 || parts[0].isEmpty()) return "?";
            m = parts.length == 1
                    ? parts[0].substring(0, Math.min(2, parts[0].length()))
                    : parts[0].substring(0, 1) + parts[parts.length - 1].substring(0, 1);
        }
        return m.substring(0, Math.min(2, m.length())).toUpperCase();
    }

    /**
     * The adaptive icon: the colour across the whole canvas, then the picture
     * filling the part the launcher shows (or the initials in white).
     */
    private static Bitmap iconBitmap(Bitmap picture, int colour, String monogram, String label) {
        Bitmap out = Bitmap.createBitmap(ICON_PX, ICON_PX, Bitmap.Config.ARGB_8888);
        Canvas canvas = new Canvas(out);
        canvas.drawColor(colour);
        int inset = (ICON_PX - VISIBLE_PX) / 2;
        if (picture != null && picture.getWidth() > 0 && picture.getHeight() > 0) {
            int side = Math.min(picture.getWidth(), picture.getHeight());
            Rect src = new Rect((picture.getWidth() - side) / 2, (picture.getHeight() - side) / 2,
                    (picture.getWidth() + side) / 2, (picture.getHeight() + side) / 2);
            Rect dst = new Rect(inset, inset, ICON_PX - inset, ICON_PX - inset);
            canvas.drawBitmap(picture, src, dst, new Paint(Paint.ANTI_ALIAS_FLAG | Paint.FILTER_BITMAP_FLAG));
            return out;
        }
        Paint text = new Paint(Paint.ANTI_ALIAS_FLAG);
        text.setColor(Color.WHITE);
        text.setTypeface(Typeface.create(Typeface.DEFAULT, Typeface.BOLD));
        text.setTextAlign(Paint.Align.CENTER);
        text.setTextSize(VISIBLE_PX * 0.42f);
        Paint.FontMetrics fm = text.getFontMetrics();
        float y = ICON_PX / 2f - (fm.ascent + fm.descent) / 2f;
        canvas.drawText(monogramFor(monogram, label), ICON_PX / 2f, y, text);
        return out;
    }
}
