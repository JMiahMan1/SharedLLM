package com.jarvisos.app;

import android.content.Context;
import android.graphics.Color;
import android.graphics.PorterDuff;
import android.graphics.drawable.GradientDrawable;
import android.graphics.Typeface;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.inputmethod.EditorInfo;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

/**
 * What the screen guard shows instead of a blocked screen, built in code (no
 * layout files) so the Accessibility cover ({@link GuardScreenActivity}) and
 * the full-screen lock overlay ({@link GuardOverlay}) show exactly the same
 * thing. The look follows the Family Link family of screens: one clear
 * reason, one clear next step for the child, and the parents' way in - as a
 * quiet text action beside the child's buttons, and as the parent card itself
 * at the bottom of the screen.
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

    // The Family Link palette: a calm mint-white canvas, one teal brand, soft
    // tonal surfaces, dark ink, and one accent per reason for the lock.
    private static final int BG_TOP = 0xFFF5FBF7;
    private static final int BG_BOTTOM = 0xFFECF5EF;
    private static final int CARD = 0xFFFFFFFF;
    private static final int FIELD = 0xFFFFFFFF;
    private static final int STROKE = 0xFFE2EDE6;
    private static final int FIELD_STROKE = 0xFFD8E6DC;
    private static final int TEXT_PRIMARY = 0xFF1B2B22;
    private static final int TEXT_SECONDARY = 0xFF44584D;
    private static final int TEXT_FAINT = 0xFF71867B;
    private static final int ERROR = 0xFFBA1A1A;

    private static final int TEAL = 0xFF0B6E4F;
    private static final int TONAL = 0xFFDCEFDE;
    private static final int ON_TONAL = 0xFF0B4C36;
    private static final int RING_OUT = 0xFFE2F2E9;
    private static final int RING_IN = 0xFFCFE9DA;

    private static final int AMBER = 0xFF0B6E4F;   // the day's time is the default reason
    private static final int VIOLET = 0xFF5B3BC4;
    private static final int SKY = 0xFF0369A1;
    private static final int SLATE = 0xFF475569;
    private static final int GREEN = 0xFF059669;
    private static final int RED = 0xFFBA1A1A;

    private GuardScreenView() {}

    static View build(Context context, String mode, String app, boolean denied, Host host) {
        return build(context, mode, "", app, denied, host);
    }

    static View build(Context context, String mode, String pkg, String app, boolean denied, Host host) {
        boolean tamper = GuardScreenActivity.MODE_TAMPER.equals(mode);
        boolean appOff = GuardScreenActivity.MODE_APP.equals(mode);
        boolean install = GuardScreenActivity.MODE_INSTALL.equals(mode);
        boolean permission = GuardScreenActivity.MODE_PERMISSION.equals(mode);
        boolean bypass = GuardScreenActivity.MODE_BYPASS.equals(mode);
        boolean locked = !tamper && !appOff && !install && !permission && !bypass;
        String appName = app == null || app.isEmpty() ? "This app" : app;

        int accent;
        int iconRes;
        String titleText;
        String bodyText;
        if (tamper) {
            accent = RED;
            iconRes = R.drawable.mi_shield;
            titleText = "A parent needs to sign in";
            bodyText = "Removing Jarvis or switching off its protection needs one of your parents' logins.";
        } else if (appOff) {
            accent = AMBER;
            iconRes = R.drawable.mi_hourglass;
            titleText = "That app is off for now";
            bodyText = "Your parents turned this app off, or its time is used up for today. Everything else still works.";
        } else if (install) {
            accent = SKY;
            iconRes = R.drawable.mi_download;
            titleText = denied ? "A parent said no to " + appName : appName + " needs a parent's OK";
            bodyText = denied
                    ? "This app stays closed. Everything else still works."
                    : "New apps open once one of your parents says yes. They have been asked.";
        } else if (bypass) {
            accent = RED;
            iconRes = R.drawable.mi_warning;
            titleText = appName + " is off while the web filter is on";
            bodyText = "It gets around the filter your parents set up, so it stays closed. Other browsers and apps still work.";
        } else if (permission) {
            accent = VIOLET;
            iconRes = R.drawable.mi_shield;
            titleText = "Ask a parent first";
            bodyText = denied
                    ? "A parent said no to that for " + appName + "."
                    : appName + " asked for something your parents approve first. They have been asked; try again once one of them says yes.";
        } else {
            String reason = GuardApi.lockReason(context);
            switch (reason) {
                case "bedtime":
                    accent = VIOLET;
                    iconRes = R.drawable.mi_dark_mode;
                    break;
                case "school":
                    accent = SKY;
                    iconRes = R.drawable.mi_school;
                    break;
                case "paused":
                    accent = SLATE;
                    iconRes = R.drawable.mi_pause;
                    break;
                case "chores":
                    accent = GREEN;
                    iconRes = R.drawable.mi_chore;
                    break;
                default:
                    accent = AMBER;
                    iconRes = R.drawable.mi_hourglass;
                    break;
            }
            titleText = lockedTitle(context, reason);
            bodyText = "chores".equals(reason)
                    ? "Tick off today's chores in Jarvis or on the chore chart and this unlocks. Calls, texts and"
                    + " Jarvis still work, and you can ask a parent for more time from here."
                    : "Calls, texts and Jarvis still work. You can ask a parent for more time, or message them,"
                    + " without leaving this screen. A parent can sign in below, or use their offline code, to lift it.";
        }

        LinearLayout root = new LinearLayout(context);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setGravity(Gravity.CENTER);
        root.setPadding(dp(context, 24), dp(context, 30), dp(context, 24), dp(context, 28));
        // Swallow touches so nothing behind the lock is used (the overlay has no
        // activity to take input for it).
        root.setClickable(true);

        // The hero: one big layered illustration with the reason at a glance.
        root.addView(hero(context, iconRes, accent));
        TextView title = title(context, titleText);
        root.addView(title);
        TextView body = text(context, bodyText, 15, TEXT_SECONDARY);
        body.setMaxWidth(dp(context, 540));
        body.setLineSpacing(dp(context, 2), 1.06f);
        body.setGravity(Gravity.CENTER);
        body.setPadding(dp(context, 4), dp(context, 10), dp(context, 4), dp(context, 20));
        root.addView(body);

        // The sheets are painted over the lock; the lock itself is never taken
        // down for them, so a child always has a way back.
        Sheet sheet = new Sheet(context);

        // The two big pill buttons for the child: the one clear next step, and
        // the way to talk to a parent in every mode.
        if (appOff && pkg != null && !pkg.isEmpty()) {
            Button askApp = primaryButton(context, TEAL, "Ask a parent to unblock");
            askApp.setOnClickListener(v -> sheet.show(askAppCard(context, sheet, pkg, appName, TEAL)));
            addFullWidth(root, askApp, 0);
        } else if (!tamper && !install && !permission && !bypass) {
            Button ask = primaryButton(context, TEAL, "Ask for more time");
            ask.setOnClickListener(v -> sheet.show(askCard(context, sheet, TEAL)));
            addFullWidth(root, ask, 0);
        }
        Button message = tonalButton(context, "Message a parent");
        message.setOnClickListener(v -> sheet.show(messageCard(context, sheet, TEAL)));
        addFullWidth(root, message, 12);

        // A parent can't miss the way in from here either: this slides the
        // screen down to the parent card and focuses it.
        Button parentAction = textButton(context, "A parent can unlock");
        addFullWidth(root, parentAction, 4);

        if (locked) {
            Button jarvis = textButton(context, "Open Jarvis");
            jarvis.setOnClickListener(v -> host.onOpenJarvis());
            addFullWidth(root, jarvis, 0);
        }

        // A quiet marker for the adults' part of the screen.
        root.addView(divider(context, "PARENTS", 18));

        // A parent's own Jarvis login, checked by the server, and an offline
        // code for when there is no connection.
        LinearLayout parentCard = panel(context, CARD, 20, STROKE);
        parentCard.setPadding(dp(context, 18), dp(context, 16), dp(context, 18), dp(context, 16));
        parentCard.setElevation(dp(context, 1));
        LinearLayout parentLabel = smallCapsLabel(context, "PARENT UNLOCK");
        parentCard.addView(parentLabel);

        EditText user = field(context, "Parent's username");
        user.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_NORMAL);
        user.setImeOptions(EditorInfo.IME_ACTION_NEXT);
        addFullWidth(parentCard, user, 10);

        EditText pass = field(context, "Parent's password");
        pass.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        pass.setImeOptions(EditorInfo.IME_ACTION_DONE);
        pass.setPadding(dp(context, 14), dp(context, 13), dp(context, 56), dp(context, 13));
        FrameLayout passWrap = new FrameLayout(context);
        FrameLayout.LayoutParams passLp = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        passLp.topMargin = dp(context, 10);
        passWrap.addView(pass, passLp);
        TextView showHide = smallToggle(context, "Show");
        FrameLayout.LayoutParams toggleLp = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.END | Gravity.CENTER_VERTICAL);
        toggleLp.rightMargin = dp(context, 10);
        toggleLp.topMargin = dp(context, 10);
        passWrap.addView(showHide, toggleLp);
        showHide.setOnClickListener(v -> {
            boolean isPassword = pass.getInputType()
                    == (InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
            if (isPassword) {
                pass.setInputType(InputType.TYPE_CLASS_TEXT
                        | InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD);
                showHide.setText("Hide");
            } else {
                pass.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
                showHide.setText("Show");
            }
            pass.setSelection(pass.getText().length());
        });
        parentCard.addView(passWrap);

        TextView error = text(context, "", 13, ERROR);
        error.setGravity(Gravity.START);
        error.setPadding(dp(context, 2), dp(context, 8), dp(context, 2), 0);

        Handler main = new Handler(Looper.getMainLooper());
        Button unlock = primaryButton(context, TEAL, "Parent unlock");
        unlock.setOnClickListener(v -> {
            unlock.setEnabled(false);
            unlock.setText("Checking…");
            String u = user.getText().toString().trim();
            String p = pass.getText().toString();
            new Thread(() -> {
                String problem = GuardApi.parentUnlock(context, u, p);
                main.post(() -> {
                    unlock.setEnabled(true);
                    unlock.setText("Parent unlock");
                    pass.setText("");
                    if (problem == null) {
                        host.onUnlocked();
                    } else {
                        error.setText(problem);
                    }
                });
            }).start();
        });
        pass.setOnEditorActionListener((v, actionId, event) -> {
            if (actionId == EditorInfo.IME_ACTION_DONE) {
                unlock.performClick();
                return true;
            }
            return false;
        });
        addFullWidth(parentCard, unlock, 14);

        // No connection: a parent's offline code works instead of their login.
        parentCard.addView(divider(context, "OR USE A CODE", 14));
        EditText offline = field(context, "Parent's offline code");
        offline.setInputType(InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_VARIATION_PASSWORD);
        offline.setPadding(dp(context, 14), dp(context, 13), dp(context, 14), dp(context, 13));
        addFullWidth(parentCard, offline, 10);
        Button useCode = ghostButton(context, "Use offline code");
        useCode.setOnClickListener(v -> {
            useCode.setEnabled(false);
            useCode.setText("Checking…");
            String code = offline.getText().toString().trim();
            new Thread(() -> {
                String problem = GuardApi.offlineUnlock(context, code);
                main.post(() -> {
                    useCode.setEnabled(true);
                    useCode.setText("Use offline code");
                    offline.setText("");
                    if (problem == null) {
                        host.onUnlocked();
                    } else {
                        error.setText(problem);
                    }
                });
            }).start();
        });
        addFullWidth(parentCard, useCode, 10);
        parentCard.addView(error);
        addFullWidth(root, parentCard, 16);

        // The sheet sits inside the same view, over the lock, so the allowed
        // flows paint over the lock window instead of replacing it.
        ScrollView scroller = new ScrollView(context);
        scroller.setFillViewport(true);
        scroller.setVerticalScrollBarEnabled(false);
        scroller.setBackground(new GradientDrawable(
                GradientDrawable.Orientation.TOP_BOTTOM, new int[]{BG_TOP, BG_BOTTOM}));
        scroller.setClickable(true);
        // "A parent can unlock" scrolls straight to the parent card and puts the
        // cursor in the username field, so the way in is always one tap away.
        parentAction.setOnClickListener(v -> {
            scroller.fullScroll(View.FOCUS_DOWN);
            user.requestFocus();
        });
        scroller.addView(root, new ScrollView.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        FrameLayout frame = new FrameLayout(context);
        frame.setBackgroundColor(BG_TOP);
        frame.addView(scroller, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        frame.addView(sheet.layer, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        return frame;
    }

    /**
     * The panel painted over the lock, Family Link style: a dimmed lock behind a
     * bottom sheet with a grabber, one accent, and a way back. Only one is up at a
     * time and it never removes the lock under it.
     */
    private static final class Sheet {
        final FrameLayout layer;
        private final Context context;

        Sheet(Context context) {
            this.context = context;
            layer = new FrameLayout(context);
            layer.setBackgroundColor(0x66000000);
            layer.setClickable(true);
            layer.setVisibility(View.GONE);
        }

        void show(View card) {
            layer.removeAllViews();
            FrameLayout.LayoutParams lp = new FrameLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
            lp.gravity = Gravity.BOTTOM;
            lp.setMargins(dp(context, 12), 0, dp(context, 12), dp(context, 12));
            layer.addView(card, lp);
            layer.setVisibility(View.VISIBLE);
        }

        void hide() {
            layer.setVisibility(View.GONE);
            layer.removeAllViews();
        }
    }

    /** "Ask for more time": how many minutes and why, sent for a parent to answer. */
    private static View askCard(Context context, Sheet sheet, int accent) {
        int[] minutes = {30};
        LinearLayout card = sheetPanel(context);

        card.addView(cardHeader(context, R.drawable.mi_hourglass, accent, "Ask for more time"));
        TextView hint = text(context, "Your parents get a notification and can allow it for today.", 13, TEXT_SECONDARY);
        hint.setGravity(Gravity.START);
        hint.setPadding(dp(context, 2), 0, dp(context, 2), dp(context, 4));
        card.addView(hint);

        LinearLayout choices = new LinearLayout(context);
        choices.setOrientation(LinearLayout.HORIZONTAL);
        choices.setGravity(Gravity.CENTER);
        int[] options = {15, 30, 60};
        String[] labels = {"15 min", "30 min", "1 hour"};
        Button[] buttons = new Button[options.length];
        for (int i = 0; i < options.length; i++) {
            final int option = options[i];
            Button choice = chip(context, labels[i], option == minutes[0], accent);
            choice.setOnClickListener(v -> {
                minutes[0] = option;
                for (int j = 0; j < buttons.length; j++) {
                    styleChip(buttons[j], options[j] == minutes[0], accent);
                }
            });
            buttons[i] = choice;
            choices.addView(choice, new LinearLayout.LayoutParams(0, dp(context, 44), 1f));
        }
        LinearLayout.LayoutParams choicesLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        choicesLp.setMargins(0, dp(context, 10), 0, dp(context, 10));
        card.addView(choices, choicesLp);

        EditText note = field(context, "Why? (optional)");
        note.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_MULTI_LINE);
        note.setMinLines(2);
        addFullWidth(card, note, 0);

        TextView status = statusLine(context);
        card.addView(status);
        Button send = primaryButton(context, TEAL, "Send request");
        send.setOnClickListener(v -> {
            send.setEnabled(false);
            send.setText("Sending…");
            status.setTextColor(TEXT_SECONDARY);
            status.setText("Asking…");
            String why = note.getText().toString().trim();
            new Thread(() -> {
                String problem = GuardApi.askMoreTime(context, minutes[0], why);
                new Handler(Looper.getMainLooper()).post(() -> {
                    send.setEnabled(true);
                    send.setText("Send request");
                    if (problem == null) {
                        sheet.show(confirmation(context, sheet, TEAL, "Asked!",
                                "A parent will answer soon."));
                    } else {
                        status.setTextColor(ERROR);
                        status.setText(problem);
                    }
                });
            }).start();
        });
        addFullWidth(card, send, 10);
        card.addView(ghostButton(context, sheet, "Back to the lock"));
        return card;
    }

    /** "Ask a parent to unblock": asks a parent to allow a blocked or time-limited app. */
    private static View askAppCard(Context context, Sheet sheet, String pkg, String appName, int accent) {
        LinearLayout card = sheetPanel(context);

        card.addView(cardHeader(context, R.drawable.mi_lock_open, accent, "Ask to use " + appName));
        TextView hint = text(context, "Your parents can allow it for an hour or for good.", 13, TEXT_SECONDARY);
        hint.setGravity(Gravity.START);
        hint.setPadding(dp(context, 2), 0, dp(context, 2), dp(context, 10));
        card.addView(hint);

        EditText note = field(context, "Why? (optional)");
        note.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_MULTI_LINE);
        note.setMinLines(2);
        addFullWidth(card, note, 0);

        TextView status = statusLine(context);
        card.addView(status);
        Button send = primaryButton(context, TEAL, "Ask a parent");
        send.setOnClickListener(v -> {
            send.setEnabled(false);
            send.setText("Asking…");
            status.setTextColor(TEXT_SECONDARY);
            status.setText("Asking…");
            String why = note.getText().toString().trim();
            new Thread(() -> {
                String problem = GuardApi.askParent(context, "app_unblock", pkg, appName, why);
                new Handler(Looper.getMainLooper()).post(() -> {
                    send.setEnabled(true);
                    send.setText("Ask a parent");
                    if (problem == null) {
                        sheet.show(confirmation(context, sheet, TEAL, "Asked!",
                                "A parent will answer soon."));
                    } else {
                        status.setTextColor(ERROR);
                        status.setText(problem);
                    }
                });
            }).start();
        });
        addFullWidth(card, send, 10);
        card.addView(ghostButton(context, sheet, "Back to the lock"));
        return card;
    }

    /** "Message a parent": a short chat message to them, sent from the lock. */
    private static View messageCard(Context context, Sheet sheet, int accent) {
        LinearLayout card = sheetPanel(context);

        card.addView(cardHeader(context, R.drawable.mi_chat, accent, "Message a parent"));
        TextView hint = text(context, "It goes to your parents in the family chat.", 13, TEXT_SECONDARY);
        hint.setGravity(Gravity.START);
        hint.setPadding(dp(context, 2), 0, dp(context, 2), dp(context, 10));
        card.addView(hint);

        EditText message = field(context, "Type a message");
        message.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_MULTI_LINE);
        message.setMinLines(2);
        addFullWidth(card, message, 0);

        TextView status = statusLine(context);
        card.addView(status);
        Button send = primaryButton(context, TEAL, "Send");
        send.setOnClickListener(v -> {
            String text = message.getText().toString().trim();
            if (text.isEmpty()) {
                status.setTextColor(ERROR);
                status.setText("Type a message first.");
                return;
            }
            send.setEnabled(false);
            send.setText("Sending…");
            status.setTextColor(TEXT_SECONDARY);
            status.setText("Sending…");
            new Thread(() -> {
                String problem = GuardApi.messageParents(context, text);
                new Handler(Looper.getMainLooper()).post(() -> {
                    send.setEnabled(true);
                    send.setText("Send");
                    if (problem == null) {
                        message.setText("");
                        sheet.show(confirmation(context, sheet, TEAL, "Sent!",
                                "A parent will see it in the family chat."));
                    } else {
                        status.setTextColor(ERROR);
                        status.setText(problem);
                    }
                });
            }).start();
        });
        addFullWidth(card, send, 10);
        card.addView(ghostButton(context, sheet, "Back to the lock"));
        return card;
    }

    /** What a sent ask or message turns into: one big tick, one line, one way back. */
    private static View confirmation(Context context, Sheet sheet, int accent, String titleText, String subText) {
        LinearLayout panel = new LinearLayout(context);
        panel.setOrientation(LinearLayout.VERTICAL);
        panel.setGravity(Gravity.CENTER);
        panel.setPadding(dp(context, 26), dp(context, 26), dp(context, 26), dp(context, 22));
        panel.setBackground(rounded(context, CARD, 24, STROKE, 1));
        panel.setElevation(dp(context, 2));

        panel.addView(iconBadge(context, R.drawable.mi_check, accent, 92));

        TextView title = text(context, titleText, 21, TEXT_PRIMARY);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        title.setPadding(0, dp(context, 16), 0, dp(context, 4));
        panel.addView(title);
        TextView sub = text(context, subText, 14, TEXT_SECONDARY);
        sub.setPadding(0, 0, 0, dp(context, 16));
        panel.addView(sub);
        Button back = primaryButton(context, TEAL, "Back to the lock");
        back.setOnClickListener(v -> sheet.hide());
        addFullWidth(panel, back, 0);
        return panel;
    }

    /** The bottom-sheet panel the ask and message cards live in. */
    private static LinearLayout sheetPanel(Context context) {
        LinearLayout card = new LinearLayout(context);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(context, 22), dp(context, 10), dp(context, 22), dp(context, 18));
        card.setBackground(rounded(context, CARD, 24, STROKE, 1));
        card.setElevation(dp(context, 2));
        grabber(card, context);
        return card;
    }

    private static void grabber(LinearLayout card, Context context) {
        View bar = new View(context);
        bar.setBackground(rounded(context, 0xFFC9D6CC, 2, 0, 0));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(dp(context, 40), dp(context, 4));
        lp.gravity = Gravity.CENTER_HORIZONTAL;
        lp.bottomMargin = dp(context, 14);
        card.addView(bar, lp);
    }

    private static LinearLayout cardHeader(Context context, int iconRes, int accent, String title) {
        LinearLayout row = new LinearLayout(context);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding(dp(context, 2), dp(context, 2), dp(context, 2), dp(context, 12));
        row.addView(iconBadge(context, iconRes, accent, 36));
        TextView text = text(context, title, 19, TEXT_PRIMARY);
        text.setGravity(Gravity.START);
        text.setTypeface(Typeface.DEFAULT_BOLD);
        text.setPadding(dp(context, 12), 0, 0, 0);
        row.addView(text, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        return row;
    }

    private static LinearLayout smallCapsLabel(Context context, String value) {
        TextView label = new TextView(context);
        label.setText(value);
        label.setTextSize(11);
        label.setTextColor(TEXT_FAINT);
        label.setLetterSpacing(0.18f);
        label.setPadding(dp(context, 2), 0, dp(context, 2), dp(context, 2));
        LinearLayout row = new LinearLayout(context);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.addView(label);
        return row;
    }

    private static View divider(Context context, String label, int topMarginDp) {
        LinearLayout row = new LinearLayout(context);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding(0, dp(context, topMarginDp), 0, dp(context, 12));
        View left = new View(context);
        left.setBackgroundColor(0xFFDCE7DE);
        row.addView(left, new LinearLayout.LayoutParams(0, dp(context, 1), 1f));
        LinearLayout value = smallCapsLabel(context, label);
        value.setPadding(dp(context, 10), 0, dp(context, 10), 0);
        row.addView(value);
        View right = new View(context);
        right.setBackgroundColor(0xFFDCE7DE);
        row.addView(right, new LinearLayout.LayoutParams(0, dp(context, 1), 1f));
        return row;
    }

    private static LinearLayout hero(Context context, int iconRes, int accent) {
        LinearLayout wrap = new LinearLayout(context);
        wrap.setOrientation(LinearLayout.VERTICAL);
        wrap.setGravity(Gravity.CENTER);
        wrap.setPadding(0, dp(context, 0), 0, dp(context, 16));
        FrameLayout outer = new FrameLayout(context);
        GradientDrawable outerCircle = new GradientDrawable();
        outerCircle.setShape(GradientDrawable.OVAL);
        outerCircle.setColor(RING_OUT);
        outer.setBackground(outerCircle);
        int outerSize = dp(context, 180);
        outer.setMinimumWidth(outerSize);
        outer.setMinimumHeight(outerSize);
        FrameLayout inner = new FrameLayout(context);
        GradientDrawable innerCircle = new GradientDrawable();
        innerCircle.setShape(GradientDrawable.OVAL);
        innerCircle.setColor(RING_IN);
        inner.setBackground(innerCircle);
        int innerSize = dp(context, 124);
        inner.setMinimumWidth(innerSize);
        inner.setMinimumHeight(innerSize);
        ImageView icon = new ImageView(context);
        icon.setImageResource(iconRes);
        icon.setColorFilter(accent, PorterDuff.Mode.SRC_IN);
        int iconSize = dp(context, 72);
        FrameLayout.LayoutParams iconLp = new FrameLayout.LayoutParams(iconSize, iconSize, Gravity.CENTER);
        inner.addView(icon, iconLp);
        FrameLayout.LayoutParams innerLp = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT, Gravity.CENTER);
        int ring = dp(context, 26);
        innerLp.setMargins(ring, ring, ring, ring);
        outer.addView(inner, innerLp);
        wrap.addView(outer);
        return wrap;
    }

    private static FrameLayout iconBadge(Context context, int iconRes, int accent, int sizeDp) {
        FrameLayout badge = new FrameLayout(context);
        GradientDrawable circle = new GradientDrawable();
        circle.setShape(GradientDrawable.OVAL);
        circle.setColor(withAlpha(accent, 0.12f));
        badge.setBackground(circle);
        int size = dp(context, sizeDp);
        // The badges are added without layout params (LinearLayout parents
        // generate their own), so the circle keeps its size as a minimum.
        badge.setMinimumWidth(size);
        badge.setMinimumHeight(size);
        ImageView icon = new ImageView(context);
        icon.setImageResource(iconRes);
        icon.setColorFilter(accent, PorterDuff.Mode.SRC_IN);
        icon.setLayoutParams(new FrameLayout.LayoutParams(
                dp(context, sizeDp >= 92 ? 56 : sizeDp >= 48 ? 30 : 20),
                dp(context, sizeDp >= 92 ? 56 : sizeDp >= 48 ? 30 : 20),
                Gravity.CENTER));
        badge.addView(icon);
        return badge;
    }

    private static EditText field(Context context, String hint) {
        EditText field = new EditText(context);
        field.setHint(hint);
        field.setTextSize(16);
        field.setTextColor(TEXT_PRIMARY);
        field.setHintTextColor(TEXT_FAINT);
        field.setPadding(dp(context, 14), dp(context, 13), dp(context, 14), dp(context, 13));
        field.setBackground(rounded(context, FIELD, 14, FIELD_STROKE, 1));
        field.setLayoutParams(new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        return field;
    }

    private static Button primaryButton(Context context, int accent, String label) {
        Button button = new Button(context);
        button.setText(label);
        button.setTextSize(16);
        button.setAllCaps(false);
        button.setTypeface(Typeface.DEFAULT_BOLD);
        button.setTextColor(0xFFFFFFFF);
        button.setBackground(rounded(context, accent, 999, 0, 0));
        button.setMinHeight(dp(context, 54));
        button.setLayoutParams(new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(context, 54)));
        return button;
    }

    private static Button tonalButton(Context context, String label) {
        Button button = new Button(context);
        button.setText(label);
        button.setTextSize(16);
        button.setAllCaps(false);
        button.setTypeface(Typeface.DEFAULT_BOLD);
        button.setTextColor(ON_TONAL);
        button.setBackground(rounded(context, TONAL, 999, 0, 0));
        button.setMinHeight(dp(context, 54));
        button.setLayoutParams(new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(context, 54)));
        return button;
    }

    private static Button ghostButton(Context context, String label) {
        return ghostButton(context, null, label);
    }

    private static Button ghostButton(Context context, Sheet sheet, String label) {
        Button button = new Button(context);
        button.setText(label);
        button.setTextSize(15);
        button.setAllCaps(false);
        button.setTypeface(Typeface.DEFAULT_BOLD);
        button.setTextColor(TEAL);
        button.setBackground(rounded(context, 0x00000000, 999, TEAL, 1));
        button.setMinHeight(dp(context, 50));
        button.setLayoutParams(new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(context, 50)));
        if (sheet != null) {
            button.setOnClickListener(v -> sheet.hide());
        }
        return button;
    }

    private static Button textButton(Context context, String label) {
        Button button = new Button(context);
        button.setText(label);
        button.setTextSize(14);
        button.setAllCaps(false);
        button.setTypeface(Typeface.DEFAULT_BOLD);
        button.setTextColor(TEAL);
        button.setBackgroundColor(Color.TRANSPARENT);
        button.setMinHeight(dp(context, 44));
        button.setLayoutParams(new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(context, 44)));
        return button;
    }

    private static Button chip(Context context, String label, boolean selected, int accent) {
        Button button = new Button(context);
        button.setText(label);
        button.setTextSize(14);
        button.setAllCaps(false);
        button.setMinHeight(dp(context, 44));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                0, dp(context, 44), 1f);
        lp.setMargins(dp(context, 4), 0, dp(context, 4), 0);
        button.setLayoutParams(lp);
        styleChip(button, selected, accent);
        return button;
    }

    private static void styleChip(Button button, boolean selected, int accent) {
        if (selected) {
            button.setBackground(rounded(button.getContext(), TEAL, 999, 0, 0));
            button.setTextColor(0xFFFFFFFF);
            button.setTypeface(Typeface.DEFAULT_BOLD);
        } else {
            button.setBackground(rounded(button.getContext(), FIELD, 999, FIELD_STROKE, 1));
            button.setTextColor(TEXT_SECONDARY);
            button.setTypeface(Typeface.DEFAULT);
        }
    }

    private static TextView smallToggle(Context context, String label) {
        TextView toggle = new TextView(context);
        toggle.setText(label);
        toggle.setTextSize(13);
        toggle.setTypeface(Typeface.DEFAULT_BOLD);
        toggle.setTextColor(TEAL);
        toggle.setGravity(Gravity.CENTER);
        toggle.setPadding(dp(context, 12), dp(context, 8), dp(context, 12), dp(context, 8));
        toggle.setClickable(true);
        toggle.setFocusable(true);
        return toggle;
    }

    private static TextView title(Context context, String value) {
        TextView view = new TextView(context);
        view.setText(value);
        view.setTextSize(26);
        view.setTypeface(Typeface.DEFAULT_BOLD);
        view.setTextColor(TEXT_PRIMARY);
        view.setGravity(Gravity.CENTER);
        view.setPadding(0, dp(context, 4), 0, 0);
        return view;
    }

    private static TextView statusLine(Context context) {
        TextView view = text(context, "", 13, TEXT_FAINT);
        view.setGravity(Gravity.START);
        view.setPadding(dp(context, 2), dp(context, 10), dp(context, 2), 0);
        return view;
    }

    private static TextView text(Context context, String value, int sizeSp, int color) {
        TextView view = new TextView(context);
        view.setText(value);
        view.setTextSize(sizeSp);
        view.setTextColor(color);
        view.setGravity(Gravity.CENTER);
        return view;
    }

    private static LinearLayout panel(Context context, int fill, float radiusDp, int strokeColor) {
        LinearLayout panel = new LinearLayout(context);
        panel.setOrientation(LinearLayout.VERTICAL);
        panel.setBackground(rounded(context, fill, radiusDp, strokeColor, 1));
        return panel;
    }

    private static GradientDrawable rounded(Context context, int fill, float radiusDp,
            int strokeColor, float strokeWidthDp) {
        GradientDrawable d = new GradientDrawable();
        d.setShape(GradientDrawable.RECTANGLE);
        d.setCornerRadius(dp(context, radiusDp));
        d.setColor(fill);
        if (strokeColor != 0 && strokeWidthDp > 0) {
            d.setStroke(dp(context, strokeWidthDp), strokeColor);
        }
        return d;
    }

    private static int withAlpha(int color, float alpha) {
        int a = Math.round(255 * alpha) & 0xFF;
        return (a << 24) | (color & 0x00FFFFFF);
    }

    private static void addFullWidth(LinearLayout parent, View child, int topMarginDp) {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.topMargin = dp(parent.getContext(), topMarginDp);
        parent.addView(child, lp);
    }

    private static int dp(Context context, float value) {
        return Math.round(value * context.getResources().getDisplayMetrics().density);
    }

    private static String lockedTitle(Context context, String reason) {
        switch (reason) {
            case "bedtime":
                return "It's bedtime";
            case "school":
                return "It's school time";
            case "paused":
                return "A parent paused this " + DeviceNoun.of(context);
            case "chores":
                return "Chores first";
            default:
                return "That's today's screen time";
        }
    }
}
