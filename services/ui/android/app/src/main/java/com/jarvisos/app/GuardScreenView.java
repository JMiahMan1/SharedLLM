package com.jarvisos.app;

import android.content.Context;
import android.graphics.Color;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
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
 * pause), a way to ask for more time, a way to message a parent, and the same
 * parent login, with a parent's offline code for when there is no connection.
 * The whole screen scrolls, so the parent's fields stay reachable with the
 * keyboard up and the way out is never hidden.
 *
 * Asking for more time and messaging a parent happen <em>over the lock</em>: a
 * sheet is painted on top of the lock instead of the lock being taken down, so
 * the child always has a way back and never lands on a page of its own. The
 * screen calls the {@link Host} only for the two things only the place showing
 * it can do: a parent unlocked, or the child wants Jarvis itself.
 */
final class GuardScreenView {

    /** What the place showing this screen does when the child acts on it. */
    interface Host {
        /** A parent signed in (or used their offline code): the lock is lifted. */
        void onUnlocked();

        /** The child opened Jarvis, which is always allowed to run. */
        void onOpenJarvis();
    }

    private GuardScreenView() {}

    static View build(Context context, String mode, String app, boolean denied, Host host) {
        boolean tamper = GuardScreenActivity.MODE_TAMPER.equals(mode);
        boolean appOff = GuardScreenActivity.MODE_APP.equals(mode);
        boolean install = GuardScreenActivity.MODE_INSTALL.equals(mode);
        boolean permission = GuardScreenActivity.MODE_PERMISSION.equals(mode);
        boolean bypass = GuardScreenActivity.MODE_BYPASS.equals(mode);
        boolean locked = !tamper && !appOff && !install && !permission && !bypass;
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
            bodyText = "Calls, texts and Jarvis still work. You can ask a parent for more time, or message them,"
                    + " without leaving this screen. A parent can sign in below, or use their offline code, to lift it.";
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
        // The sheets are painted over the lock; the lock itself is never taken
        // down for them, so a child always has a way back.
        Sheet sheet = new Sheet(context);
        if (!tamper && !install && !permission && !bypass) {
            Button ask = new Button(context);
            ask.setText("Ask for more time");
            ask.setOnClickListener(v -> sheet.show(askCard(context, sheet)));
            root.addView(ask);
        }
        // A way to reach a parent by chat in every mode: a blocked app or a
        // denied install is exactly when a child needs to talk to one.
        Button message = new Button(context);
        message.setText("Message a parent");
        message.setOnClickListener(v -> sheet.show(messageCard(context, sheet)));
        root.addView(message);
        if (locked) {
            Button jarvis = new Button(context);
            jarvis.setText("Open Jarvis");
            jarvis.setOnClickListener(v -> host.onOpenJarvis());
            root.addView(jarvis);
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

        // Scrollable so the parent's fields stay reachable with the keyboard
        // up, and a small screen never hides the way out of the lock.
        ScrollView scroller = new ScrollView(context);
        scroller.setFillViewport(true);
        scroller.setBackgroundColor(Color.parseColor("#030711"));
        scroller.setClickable(true);
        scroller.addView(root, new ScrollView.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        // The lock, with the sheets painted on top of it. The sheets sit inside
        // the same view, so the lock stays up underneath: the allowed flows
        // paint over the lock window instead of replacing it.
        FrameLayout frame = new FrameLayout(context);
        frame.setBackgroundColor(Color.parseColor("#030711"));
        frame.addView(scroller, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        frame.addView(sheet.layer, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        return frame;
    }

    /**
     * The panel painted over the lock: a dimmed lock behind a card. Only one is
     * up at a time and it never removes the lock under it.
     */
    private static final class Sheet {
        final FrameLayout layer;

        Sheet(Context context) {
            layer = new FrameLayout(context);
            layer.setBackgroundColor(Color.parseColor("#E6030711"));
            layer.setClickable(true);
            layer.setVisibility(View.GONE);
        }

        void show(View card) {
            layer.removeAllViews();
            FrameLayout.LayoutParams lp = new FrameLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
            lp.gravity = Gravity.CENTER;
            lp.setMargins(40, 40, 40, 40);
            layer.addView(card, lp);
            layer.setVisibility(View.VISIBLE);
        }

        void hide() {
            layer.setVisibility(View.GONE);
            layer.removeAllViews();
        }
    }

    /** "Ask for more time": how many minutes and why, sent for a parent to answer. */
    private static View askCard(Context context, Sheet sheet) {
        int[] minutes = {30};
        LinearLayout card = card(context);
        card.addView(heading(context, "Ask for more time"));

        LinearLayout choices = new LinearLayout(context);
        choices.setOrientation(LinearLayout.HORIZONTAL);
        choices.setGravity(Gravity.CENTER);
        int[] options = {15, 30, 60};
        String[] labels = {"15 min", "30 min", "1 hour"};
        Button[] buttons = new Button[options.length];
        for (int i = 0; i < options.length; i++) {
            final int option = options[i];
            Button choice = new Button(context);
            choice.setText(labels[i]);
            choice.setAlpha(option == minutes[0] ? 1f : 0.45f);
            choice.setOnClickListener(v -> {
                minutes[0] = option;
                for (Button b : buttons) b.setAlpha(0.45f);
                choice.setAlpha(1f);
            });
            buttons[i] = choice;
            choices.addView(choice);
        }
        card.addView(choices);

        EditText note = new EditText(context);
        note.setHint("Why? (optional)");
        note.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_MULTI_LINE);
        note.setTextColor(Color.WHITE);
        note.setHintTextColor(Color.parseColor("#64748B"));
        card.addView(note);

        TextView status = statusLine(context);
        Button send = new Button(context);
        send.setText("Ask");
        send.setOnClickListener(v -> {
            send.setEnabled(false);
            status.setTextColor(Color.parseColor("#94A3B8"));
            status.setText("Asking...");
            String why = note.getText().toString().trim();
            new Thread(() -> {
                String problem = GuardApi.askMoreTime(context, minutes[0], why);
                new Handler(Looper.getMainLooper()).post(() -> {
                    send.setEnabled(true);
                    if (problem == null) {
                        status.setTextColor(Color.parseColor("#86EFAC"));
                        status.setText("Asked. A parent will answer soon.");
                    } else {
                        status.setTextColor(Color.parseColor("#FCD34D"));
                        status.setText(problem);
                    }
                });
            }).start();
        });
        card.addView(send);
        card.addView(status);
        card.addView(closeButton(context, sheet));
        return card;
    }

    /** "Message a parent": a short chat message to them, sent from the lock. */
    private static View messageCard(Context context, Sheet sheet) {
        LinearLayout card = card(context);
        card.addView(heading(context, "Message a parent"));
        TextView hint = text(context, "It goes to your parents in the family chat.", 14);
        hint.setTextColor(Color.parseColor("#94A3B8"));
        card.addView(hint);

        EditText message = new EditText(context);
        message.setHint("Type a message");
        message.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_MULTI_LINE);
        message.setTextColor(Color.WHITE);
        message.setHintTextColor(Color.parseColor("#64748B"));
        card.addView(message);

        TextView status = statusLine(context);
        Button send = new Button(context);
        send.setText("Send");
        send.setOnClickListener(v -> {
            String text = message.getText().toString().trim();
            if (text.isEmpty()) {
                status.setTextColor(Color.parseColor("#FCD34D"));
                status.setText("Type a message first.");
                return;
            }
            send.setEnabled(false);
            status.setTextColor(Color.parseColor("#94A3B8"));
            status.setText("Sending...");
            new Thread(() -> {
                String problem = GuardApi.messageParents(context, text);
                new Handler(Looper.getMainLooper()).post(() -> {
                    send.setEnabled(true);
                    if (problem == null) {
                        message.setText("");
                        status.setTextColor(Color.parseColor("#86EFAC"));
                        status.setText("Sent. A parent will see it in the family chat.");
                    } else {
                        status.setTextColor(Color.parseColor("#FCD34D"));
                        status.setText(problem);
                    }
                });
            }).start();
        });
        card.addView(send);
        card.addView(status);
        card.addView(closeButton(context, sheet));
        return card;
    }

    private static LinearLayout card(Context context) {
        LinearLayout card = new LinearLayout(context);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(40, 40, 40, 40);
        card.setBackgroundColor(Color.parseColor("#0B1220"));
        return card;
    }

    private static TextView heading(Context context, String value) {
        TextView view = text(context, value, 20);
        view.setGravity(Gravity.START);
        return view;
    }

    private static TextView statusLine(Context context) {
        TextView view = text(context, "", 14);
        view.setGravity(Gravity.START);
        return view;
    }

    private static Button closeButton(Context context, Sheet sheet) {
        Button close = new Button(context);
        close.setText("Back to the lock");
        close.setOnClickListener(v -> sheet.hide());
        return close;
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
