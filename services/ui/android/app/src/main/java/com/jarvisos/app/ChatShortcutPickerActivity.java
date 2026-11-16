package com.jarvisos.app;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.drawable.BitmapDrawable;
import android.os.Bundle;
import android.view.Gravity;
import android.widget.Button;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import androidx.core.content.pm.ShortcutInfoCompat;
import androidx.core.content.pm.ShortcutManagerCompat;
import java.util.List;

/**
 * "Jarvis chat" in the launcher's widget list (the CREATE_SHORTCUT action):
 * pick one of the chats this phone has opened, Beloved among them, and the
 * launcher places its icon, the person's picture, on the home screen. The
 * list is ChatShortcutStore, filled whenever a chat is opened in the app.
 */
public class ChatShortcutPickerActivity extends Activity {

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setResult(RESULT_CANCELED);
        if (!Intent.ACTION_CREATE_SHORTCUT.equals(getIntent().getAction())) {
            finish();
            return;
        }

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(0xFF0F172A);
        int pad = dp(20);
        root.setPadding(pad, pad, pad, pad);

        TextView heading = new TextView(this);
        heading.setText("Choose a chat");
        heading.setTextColor(0xFFE2E8F0);
        heading.setTextSize(20);
        root.addView(heading);

        List<ChatShortcutStore.Entry> entries = ChatShortcutStore.list(this);
        TextView sub = new TextView(this);
        sub.setText(entries.isEmpty()
                ? "Open a chat in Jarvis first, then come back here to put it on the home screen."
                : "Its icon opens that chat. A private chat opens on its lock screen.");
        sub.setTextColor(0xFF94A3B8);
        sub.setTextSize(13);
        sub.setPadding(0, dp(6), 0, dp(14));
        root.addView(sub);

        ScrollView scroll = new ScrollView(this);
        scroll.setLayoutParams(new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f));
        LinearLayout list = new LinearLayout(this);
        list.setOrientation(LinearLayout.VERTICAL);
        scroll.addView(list);
        root.addView(scroll);

        for (ChatShortcutStore.Entry entry : entries) list.addView(row(entry));

        Button cancel = new Button(this);
        cancel.setText("Cancel");
        cancel.setOnClickListener(v -> finish());
        root.addView(cancel);
        setContentView(root);
    }

    private LinearLayout row(ChatShortcutStore.Entry entry) {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setMinimumHeight(dp(56));
        row.setPadding(dp(4), dp(6), dp(4), dp(6));
        row.setClickable(true);
        row.setFocusable(true);
        row.setContentDescription(entry.label);

        Bitmap picture = ChatShortcutStore.picture(this, entry.id);
        ImageView icon = new ImageView(this);
        icon.setImageDrawable(new BitmapDrawable(getResources(),
                ChatShortcutPlugin.roundIcon(picture, entry.colour, entry.monogram, entry.label, dp(44))));
        row.addView(icon, new LinearLayout.LayoutParams(dp(44), dp(44)));

        TextView name = new TextView(this);
        name.setText(entry.label);
        name.setTextColor(0xFFF1F5F9);
        name.setTextSize(16);
        name.setPadding(dp(14), 0, 0, 0);
        row.addView(name, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));

        row.setOnClickListener(v -> choose(entry, picture));
        return row;
    }

    private void choose(ChatShortcutStore.Entry entry, Bitmap picture) {
        try {
            ShortcutInfoCompat info = ChatShortcutPlugin.build(this, entry.id, entry.label, entry.path,
                    entry.colour, entry.monogram, picture);
            ShortcutManagerCompat.pushDynamicShortcut(this, info);
            setResult(RESULT_OK, ShortcutManagerCompat.createShortcutResultIntent(this, info));
        } catch (Exception ignored) {
            setResult(RESULT_CANCELED);
        }
        finish();
    }

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }
}
