package com.jarvisos.app;

import android.content.Context;
import android.graphics.Color;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;

/**
 * What the screen guard shows instead of a blocked screen, built in code (no
 * layout files) so the Accessibility cover ({@link GuardScreenActivity}) and
 * the full-screen lock overlay ({@link GuardOverlay}) show exactly the same
 * thing.
 *
 * Tamper mode: "A parent needs to sign in" with a parent's own Jarvis login,
 * checked by the server (only this child's parents, five tries then a wait).
 * Locked mode: why the phone is locked (bedtime, school, the day's time, a
 * pause), a way to ask for more time in Jarvis, and the same parent login, with
 * a parent's offline code for when there is no connection.
 *
 * The screen does not act on its own: it calls the {@link Host} for the two
 * things only the place showing it can do (a cover activity finishes; the
 * overlay takes its window down).
 */
final class GuardScreenView {

    /** What the place showing this screen does when the child acts on it. */
    interface Host {
        /** A parent signed in (or used their offline code): the lock is lifted. */
        void onUnlocked();

        /** The child asked for more time: open Jarvis (Health holds the card). */
        void onAskMoreTime();
    }

    private GuardScreenView() {}

    static View build(Context context, String mode, String app, boolean denied, Host host) {
        boolean tamper = GuardScreenActivity.MODE_TAMPER.equals(mode);
        boolean appOff = GuardScreenActivity.MODE_APP.equals(mode);
        boolean install = GuardScreenActivity.MODE_INSTALL.equals(mode);
        boolean permission = GuardScreenActivity.MODE_PERMISSION.equals(mode);
        boolean bypass = GuardScreenActivity.MODE_BYPASS.equals(mode);
        String appName = app == null || app.isEmpty() ? "This app" : app;

        LinearLayout root = new LinearLayout(context);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setGravity(Gravity.CENTER);
        root.setPadding(64, 64, 64, 64);
        root.setBackgroundColor(Color.parseColor("#030711"));
        // Swallow touches so nothing behind the lock is used (the overlay has no
        // activity to take input for it).
        root.setClickable(true);

        String titleText;
        String bodyText;
        if (tamper) {
            titleText = "A parent needs to sign in";
            bodyText = "Removing Jarvis or switching off its protection needs one of your parents' logins.";
        } else if (appOff) {
            titleText = "That app is off for now";
            bodyText = "Your parents turned this app off, or its time is used up for today. Everything else still works.";
        } else if (install) {
            titleText = denied ? "A parent said no to " + appName : appName + " needs a parent's OK";
            bodyText = denied
                    ? "This app stays closed. Everything else still works."
                    : "New apps open once one of your parents says yes. They have been asked.";
        } else if (bypass) {
            titleText = appName + " is off while the web filter is on";
            bodyText = "It gets around the filter your parents set up, so it stays closed. Other browsers and apps still work.";
        } else if (permission) {
            titleText = "Ask a parent first";
            bodyText = denied
                    ? "A parent said no to that for " + appName + "."
                    : appName + " asked for something your parents approve first. They have been asked; try again once one of them says yes.";
        } else {
            titleText = lockedTitle(context);
            bodyText = "Calls, texts and Jarvis still work. You can ask a parent for more time in Jarvis.";
        }
        TextView title = text(context, titleText, 24);
        TextView body = text(context, bodyText, 16);
        body.setTextColor(Color.parseColor("#94A3B8"));

        EditText user = new EditText(context);
        user.setHint("Parent's username");
        user.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_NORMAL);
        EditText pass = new EditText(context);
        pass.setHint("Parent's password");
        pass.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        for (EditText field : new EditText[] {user, pass}) {
            field.setTextColor(Color.WHITE);
            field.setHintTextColor(Color.parseColor("#64748B"));
        }
        TextView error = text(context, "", 14);
        error.setTextColor(Color.parseColor("#FCD34D"));

        Handler main = new Handler(Looper.getMainLooper());
        Button unlock = new Button(context);
        unlock.setText("Parent unlock");
        unlock.setOnClickListener(v -> {
            unlock.setEnabled(false);
            String u = user.getText().toString().trim();
            String p = pass.getText().toString();
            new Thread(() -> {
                String problem = GuardApi.parentUnlock(context, u, p);
                main.post(() -> {
                    unlock.setEnabled(true);
                    pass.setText("");
                    if (problem == null) {
                        host.onUnlocked();
                    } else {
                        error.setText(problem);
                    }
                });
            }).start();
        });

        root.addView(title);
        root.addView(body);
        if (!tamper && !install && !permission && !bypass) {
            Button ask = new Button(context);
            ask.setText("Ask for more time");
            ask.setOnClickListener(v -> host.onAskMoreTime());
            root.addView(ask);
        }
        // No connection: a parent's offline code works instead of their login.
        EditText offline = new EditText(context);
        offline.setHint("Parent's offline code");
        offline.setInputType(InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_VARIATION_PASSWORD);
        offline.setTextColor(Color.WHITE);
        offline.setHintTextColor(Color.parseColor("#64748B"));
        Button useCode = new Button(context);
        useCode.setText("Use offline code");
        useCode.setOnClickListener(v -> {
            useCode.setEnabled(false);
            String code = offline.getText().toString().trim();
            new Thread(() -> {
                String problem = GuardApi.offlineUnlock(context, code);
                main.post(() -> {
                    useCode.setEnabled(true);
                    offline.setText("");
                    if (problem == null) {
                        host.onUnlocked();
                    } else {
                        error.setText(problem);
                    }
                });
            }).start();
        });

        root.addView(user);
        root.addView(pass);
        root.addView(unlock);
        root.addView(offline);
        root.addView(useCode);
        root.addView(error);
        return root;
    }

    private static String lockedTitle(Context context) {
        switch (GuardApi.lockReason(context)) {
            case "bedtime":
                return "It's bedtime";
            case "school":
                return "It's school time";
            case "paused":
                return "A parent paused this phone";
            default:
                return "That's today's screen time";
        }
    }

    private static TextView text(Context context, String value, int sizeSp) {
        TextView view = new TextView(context);
        view.setText(value);
        view.setTextSize(sizeSp);
        view.setTextColor(Color.WHITE);
        view.setGravity(Gravity.CENTER);
        view.setPadding(0, 16, 0, 16);
        return view;
    }
}
