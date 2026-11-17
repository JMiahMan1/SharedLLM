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


    /**
     * Active Jarvis theme palette resolved from TokenBridge prefs or the
     * server's status theme_id (matching jarvis-default.pack.json).
     */
    static final class Palette {
        final String themeId;
        final boolean dark;
        final int bgTop;
        final int bgBottom;
        final int card;
        final int field;
        final int stroke;
        final int fieldStroke;
        final int textPrimary;
        final int textSecondary;
        final int textFaint;
        final int error;
        final int brand;
        final int onBrand;
        final int tonal;
        final int onTonal;
        final int ringOut;
        final int ringIn;
        final int dividerLine;
        final int grabberBar;
        /** The theme's accentAlt, ring2 and ring3, for the lock reasons. */
        final int alt;
        final int second;
        final int third;

        Palette(String themeId, int bg, int surface, int text, int textMuted,
                int border, int accent, int onAccent, boolean dark, int alt, int second, int third) {
            this.alt = alt;
            this.second = second;
            this.third = third;
            this.themeId = themeId;
            this.dark = dark;
            this.bgTop = blend(bg, accent, dark ? 0.10f : 0.05f);
            this.bgBottom = bg;
            this.card = surface;
            this.field = blend(bg, surface, 0.55f);
            this.stroke = border;
            this.fieldStroke = border;
            this.textPrimary = text;
            this.textSecondary = textMuted;
            this.textFaint = withAlpha(textMuted, 0.78f);
            this.error = accent;
            this.brand = accent;
            this.onBrand = onAccent;
            this.tonal = blend(surface, accent, dark ? 0.24f : 0.14f);
            this.onTonal = dark ? text : blend(text, accent, 0.35f);
            this.ringOut = blend(bg, accent, dark ? 0.16f : 0.10f);
            this.ringIn = blend(bg, accent, dark ? 0.28f : 0.18f);
            this.dividerLine = withAlpha(border, 0.85f);
            this.grabberBar = withAlpha(textMuted, 0.45f);
        }

        static Palette resolve(Context context) {
            JarvisTheme t = JarvisTheme.resolve(context);
            return new Palette(t.id, t.bg, t.surface, t.text, t.textMuted, t.border, t.accent, t.onAccent, t.dark,
                    t.accentAlt, t.ring2, t.ring3);
        }
    }

    static String themeSignature(Context context) {
        Palette p = Palette.resolve(context);
        return p.themeId + ":" + Integer.toHexString(p.brand) + ":" + Integer.toHexString(p.bgBottom);
    }

    private static int blend(int c1, int c2, float ratio) {
        float inv = 1f - ratio;
        int r = Math.round(Color.red(c1) * inv + Color.red(c2) * ratio);
        int g = Math.round(Color.green(c1) * inv + Color.green(c2) * ratio);
        int b = Math.round(Color.blue(c1) * inv + Color.blue(c2) * ratio);
        return Color.argb(255, Math.min(255, Math.max(0, r)), Math.min(255, Math.max(0, g)), Math.min(255, Math.max(0, b)));
    }

    private GuardScreenView() {}

    static View build(Context context, String mode, String app, boolean denied, Host host) {
        return build(context, mode, "", app, denied, host);
    }

    static View build(Context context, String mode, String pkg, String app, boolean denied, Host host) {
        Palette pal = Palette.resolve(context);
        boolean tamper = GuardScreenActivity.MODE_TAMPER.equals(mode);
        boolean appOff = GuardScreenActivity.MODE_APP.equals(mode);
        boolean install = GuardScreenActivity.MODE_INSTALL.equals(mode);
        boolean permission = GuardScreenActivity.MODE_PERMISSION.equals(mode);
        boolean bypass = GuardScreenActivity.MODE_BYPASS.equals(mode);
        boolean videoCall = GuardScreenActivity.MODE_VIDEO_CALL.equals(mode);
        boolean locked = !tamper && !appOff && !install && !permission && !bypass && !videoCall;
        String appName = app == null || app.isEmpty() ? "This app" : app;

        int accent;
        int iconRes;
        String titleText;
        String bodyText;
        String reason = locked ? GuardApi.lockReason(context) : "";
        if (tamper) {
            accent = pal.brand;
            iconRes = R.drawable.mi_shield;
            titleText = "A parent needs to sign in";
            bodyText = "Removing Jarvis or switching off its protection needs one of your parents.";
        } else if (appOff) {
            accent = pal.brand;
            iconRes = R.drawable.mi_hourglass;
            titleText = "That app is off for now";
            bodyText = "Your parents turned it off, or its time is up for today. Everything else still works.";
        } else if (install) {
            accent = pal.alt;
            iconRes = R.drawable.mi_download;
            titleText = denied ? "A parent said no to " + appName : appName + " needs a parent's OK";
            bodyText = denied
                    ? "This app stays closed. Everything else still works."
                    : "It opens once one of your parents says yes. They have been asked.";
        } else if (videoCall) {
            accent = pal.brand;
            iconRes = R.drawable.mi_shield;
            titleText = "Video calls are off for now";
            bodyText = (app == null || app.isEmpty() ? "That site" : app)
                    + " is a video-call site, and your parents turned video calls off.";
        } else if (bypass) {
            accent = pal.brand;
            iconRes = R.drawable.mi_warning;
            titleText = appName + " is off while the web filter is on";
            bodyText = "It gets around the filter your parents set up. Other browsers and apps still work.";
        } else if (permission) {
            accent = pal.second;
            iconRes = R.drawable.mi_shield;
            titleText = "Ask a parent first";
            bodyText = denied
                    ? "A parent said no to that for " + appName + "."
                    : appName + " asked for something your parents approve first. They have been asked.";
        } else {
            switch (reason) {
                case "bedtime":
                    accent = pal.second;
                    iconRes = R.drawable.mi_bed;
                    break;
                case "school":
                    accent = pal.alt;
                    iconRes = R.drawable.mi_school;
                    break;
                case "paused":
                    accent = pal.brand;
                    iconRes = R.drawable.mi_pause_shield;
                    break;
                case "chores":
                    accent = pal.third;
                    iconRes = R.drawable.mi_chore;
                    break;
                default:
                    accent = pal.brand;
                    iconRes = R.drawable.mi_hourglass;
                    break;
            }
            titleText = lockedTitle(context, reason);
            bodyText = "chores".equals(reason)
                    ? "Tick off today's chores in Jarvis and this unlocks. You can still call your parents or 911."
                    : "You can still call your parents or 911, and use Jarvis. Texts wait until it unlocks.";
        }

        Sheet sheet = new Sheet(context);

        LinearLayout story = new LinearLayout(context);
        story.setOrientation(LinearLayout.VERTICAL);
        story.setGravity(Gravity.CENTER_HORIZONTAL);
        View clock = locked ? clock(context, pal) : null;
        if (clock != null) story.addView(clock, fullWidth(context, 0));
        FrameLayout hero = hero(context, pal, iconRes, accent, 116);
        story.addView(hero, wrapCentered(context, clock != null ? 28 : 0));
        TextView title = title(context, pal, titleText);
        title.setPadding(0, dp(context, 20), 0, 0);
        story.addView(title, fullWidth(context, 0));
        String until = locked ? untilLine(context, reason) : null;
        if (until != null) {
            story.addView(pill(context, pal, until, accent), wrapCentered(context, 12));
        }
        TextView body = text(context, bodyText, 15, pal.textSecondary);
        body.setLineSpacing(dp(context, 2), 1.05f);
        body.setPadding(dp(context, 4), dp(context, 12), dp(context, 4), 0);
        story.addView(body, fullWidth(context, 0));

        LinearLayout actions = new LinearLayout(context);
        actions.setOrientation(LinearLayout.VERTICAL);
        actions.setGravity(Gravity.CENTER_HORIZONTAL);
        boolean chores = "chores".equals(reason);
        // The child's one clear next step, as the big button.
        if (tamper) {
            Button signIn = primaryButton(context, pal, "Parent sign in");
            signIn.setOnClickListener(v -> sheet.show(parentCard(context, pal, sheet, host)));
            actions.addView(signIn, fullWidth(context, 0));
        } else if (appOff && pkg != null && !pkg.isEmpty()) {
            Button askApp = primaryButton(context, pal, "Ask a parent to unblock");
            askApp.setOnClickListener(v -> sheet.show(askAppCard(context, pal, sheet, pkg, appName, pal.brand)));
            actions.addView(askApp, fullWidth(context, 0));
        } else if (locked && chores) {
            Button tick = primaryButton(context, pal, "Check off chores");
            tick.setOnClickListener(v -> host.onOpenJarvis());
            actions.addView(tick, fullWidth(context, 0));
        } else if (locked || appOff) {
            Button ask = primaryButton(context, pal, "Ask for more time");
            ask.setOnClickListener(v -> sheet.show(askCard(context, pal, sheet, pal.brand)));
            actions.addView(ask, fullWidth(context, 0));
        }

        // A parent is always reachable: a message, and a call when the
        // server knows their number (calls to parents and 911 still go through).
        java.util.List<String[]> parents = parentPhones(context);
        LinearLayout reach = new LinearLayout(context);
        reach.setOrientation(LinearLayout.HORIZONTAL);
        Button message = tonalButton(context, pal, parents.isEmpty() ? "Message a parent" : "Message", R.drawable.mi_chat);
        message.setOnClickListener(v -> sheet.show(messageCard(context, pal, sheet, pal.brand)));
        reach.addView(message, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        if (!parents.isEmpty()) {
            Button call = tonalButton(context, pal, "Call", R.drawable.mi_call);
            call.setOnClickListener(v -> {
                if (parents.size() == 1) {
                    dial(context, parents.get(0)[1]);
                } else {
                    sheet.show(callCard(context, pal, sheet, parents));
                }
            });
            LinearLayout.LayoutParams callLp = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
            callLp.leftMargin = dp(context, 10);
            reach.addView(call, callLp);
        }
        actions.addView(reach, fullWidth(context, actions.getChildCount() == 0 ? 0 : 12));

        // The quiet ways out, side by side: Jarvis (chores, chat, the parts a
        // parent left open), more time on a chores lock, and a parent's unlock.
        LinearLayout quiet = new LinearLayout(context);
        quiet.setOrientation(LinearLayout.HORIZONTAL);
        quiet.setGravity(Gravity.CENTER);
        if (locked && chores) {
            Button more = quietButton(context, pal, "More time", R.drawable.mi_hourglass);
            more.setOnClickListener(v -> sheet.show(askCard(context, pal, sheet, pal.brand)));
            quiet.addView(more, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        } else if (locked) {
            Button jarvis = quietButton(context, pal, "Chores & Jarvis", R.drawable.mi_home);
            jarvis.setOnClickListener(v -> host.onOpenJarvis());
            quiet.addView(jarvis, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        }
        if (!tamper) {
            Button parentButton = quietButton(context, pal, "Parent unlock", R.drawable.mi_key);
            parentButton.setOnClickListener(v -> sheet.show(parentCard(context, pal, sheet, host)));
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
            if (quiet.getChildCount() > 0) lp.leftMargin = dp(context, 10);
            quiet.addView(parentButton, lp);
        }
        if (quiet.getChildCount() > 0) actions.addView(quiet, fullWidth(context, 12));

        // One column, never wider than a phone, centred on a tablet; two
        // columns on a phone on its side. Decided from the size the lock is
        // actually given, every time it changes, so turning the phone with the
        // lock up never leaves a layout made for the other way round.
        CappedColumn content = new CappedColumn(context, story, actions, clock, hero);
        // Swallow touches so nothing behind the lock is used (the overlay has no
        // activity to take input for it).
        FrameLayout page = new FrameLayout(context);
        page.setClickable(true);
        int side = dp(context, 24);
        page.setPadding(side, dp(context, 24), side, dp(context, 24));
        page.addView(content, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.CENTER));
        content.adaptTo(dm(context).widthPixels, dm(context).heightPixels);

        ScrollView scroller = new ScrollView(context);
        scroller.setFillViewport(true);
        scroller.setVerticalScrollBarEnabled(false);
        scroller.setClipToPadding(false);
        scroller.addView(page, new ScrollView.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        FrameLayout frame = new FrameLayout(context);
        frame.setBackground(new GradientDrawable(
                GradientDrawable.Orientation.TOP_BOTTOM, new int[]{pal.bgTop, pal.bgBottom}));
        frame.addView(scroller, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        frame.addView(sheet.layer, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        frame.addOnLayoutChangeListener((v, l, t, r, b, ol, ot, or, ob) -> {
            if (r - l != or - ol || b - t != ob - ot) {
                v.post(() -> content.adaptTo(r - l, b - t));
            }
        });
        keepClearOfBars(context, frame, scroller, sheet.layer);
        return frame;
    }

    /**
     * Keeps everything off the status bar, the navigation buttons, a camera cut-out
     * and the keyboard. The lock is drawn under every system bar (the overlay on
     * purpose, the activity because Android draws edge to edge), so the bars'
     * sizes come in as insets and become padding here. A screen that reports no
     * status bar inset still gets the platform's status bar height on top.
     */
    private static void keepClearOfBars(Context context, View frame, View scroller, View sheetLayer) {
        int statusFallback = 0;
        int id = context.getResources().getIdentifier("status_bar_height", "dimen", "android");
        if (id > 0) statusFallback = context.getResources().getDimensionPixelSize(id);
        final int statusMin = statusFallback;
        frame.setOnApplyWindowInsetsListener((v, insets) -> {
            android.graphics.Insets bars = insets.getInsets(
                    android.view.WindowInsets.Type.systemBars() | android.view.WindowInsets.Type.displayCutout());
            android.graphics.Insets ime = insets.getInsets(android.view.WindowInsets.Type.ime());
            int top = Math.max(bars.top, statusMin);
            int bottom = Math.max(bars.bottom, ime.bottom);
            scroller.setPadding(bars.left, top, bars.right, bottom);
            sheetLayer.setPadding(bars.left, top, bars.right, bottom);
            return insets;
        });
        frame.requestApplyInsets();
    }

    /**
     * The lock's content: the reason (story) above the buttons (actions) in one
     * column no wider than a phone, or side by side when the screen is wide and
     * short (a phone on its side). The clock only shows when there is height for it.
     */
    private static final class CappedColumn extends LinearLayout {
        private final View story;
        private final View actions;
        private final View clock;
        private final View hero;
        private int maxWidth;
        private Boolean sideways;
        private Boolean roomy;

        CappedColumn(Context context, View story, View actions, View clock, View hero) {
            super(context);
            this.story = story;
            this.actions = actions;
            this.clock = clock;
            this.hero = hero;
            setGravity(Gravity.CENTER);
            addView(story);
            addView(actions);
        }

        /** Lay out for a screen of this many pixels; does nothing when nothing changes. */
        void adaptTo(int widthPx, int heightPx) {
            if (widthPx <= 0 || heightPx <= 0) return;
            float density = getResources().getDisplayMetrics().density;
            float widthDp = widthPx / density;
            float heightDp = heightPx / density;
            boolean side = widthDp > heightDp && heightDp < 600;
            boolean tall = heightDp >= 700;
            if (Boolean.valueOf(side).equals(sideways) && Boolean.valueOf(tall).equals(roomy)) return;
            sideways = side;
            roomy = tall;
            Context c = getContext();
            maxWidth = dp(c, side ? 820 : 440);
            setOrientation(side ? HORIZONTAL : VERTICAL);
            if (side) {
                LayoutParams left = new LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
                left.rightMargin = dp(c, 20);
                left.gravity = Gravity.CENTER_VERTICAL;
                story.setLayoutParams(left);
                LayoutParams right = new LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
                right.leftMargin = dp(c, 20);
                right.gravity = Gravity.CENTER_VERTICAL;
                actions.setLayoutParams(right);
            } else {
                story.setLayoutParams(new LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
                LayoutParams below = new LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
                below.topMargin = dp(c, 28);
                actions.setLayoutParams(below);
            }
            boolean showClock = clock != null && tall && !side;
            if (clock != null) clock.setVisibility(showClock ? VISIBLE : GONE);
            int heroSize = dp(c, side ? 88 : 116);
            LinearLayout.LayoutParams heroLp = new LinearLayout.LayoutParams(heroSize, heroSize);
            heroLp.gravity = Gravity.CENTER_HORIZONTAL;
            heroLp.topMargin = showClock ? dp(c, 28) : 0;
            hero.setLayoutParams(heroLp);
            if (hero instanceof ViewGroup && ((ViewGroup) hero).getChildCount() > 0) {
                View disc = ((ViewGroup) hero).getChildAt(0);
                int discSize = Math.round(heroSize * 0.74f);
                disc.setLayoutParams(new FrameLayout.LayoutParams(discSize, discSize, Gravity.CENTER));
                if (disc instanceof ViewGroup && ((ViewGroup) disc).getChildCount() > 0) {
                    int iconSize = Math.round(heroSize * 0.40f);
                    ((ViewGroup) disc).getChildAt(0).setLayoutParams(
                            new FrameLayout.LayoutParams(iconSize, iconSize, Gravity.CENTER));
                }
            }
            requestLayout();
        }

        @Override
        protected void onMeasure(int widthMeasureSpec, int heightMeasureSpec) {
            int size = MeasureSpec.getSize(widthMeasureSpec);
            if (maxWidth > 0 && size > maxWidth) {
                widthMeasureSpec = MeasureSpec.makeMeasureSpec(maxWidth, MeasureSpec.getMode(widthMeasureSpec));
            }
            super.onMeasure(widthMeasureSpec, heightMeasureSpec);
        }
    }

    private static android.util.DisplayMetrics dm(Context context) {
        return context.getResources().getDisplayMetrics();
    }

    /** The time and date, like the phone's own lock screen. */
    private static View clock(Context context, Palette pal) {
        LinearLayout wrap = new LinearLayout(context);
        wrap.setOrientation(LinearLayout.VERTICAL);
        wrap.setGravity(Gravity.CENTER_HORIZONTAL);
        android.widget.TextClock time = new android.widget.TextClock(context);
        time.setFormat12Hour("h:mm");
        time.setFormat24Hour("H:mm");
        time.setTextSize(64);
        time.setTypeface(Typeface.create("sans-serif-light", Typeface.NORMAL));
        time.setTextColor(pal.textPrimary);
        time.setIncludeFontPadding(false);
        time.setGravity(Gravity.CENTER);
        wrap.addView(time, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        android.widget.TextClock date = new android.widget.TextClock(context);
        date.setFormat12Hour("EEEE, MMMM d");
        date.setFormat24Hour("EEEE, d MMMM");
        date.setTextSize(15);
        date.setTextColor(pal.textSecondary);
        date.setPadding(0, dp(context, 4), 0, 0);
        date.setGravity(Gravity.CENTER);
        wrap.addView(date, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        return wrap;
    }

    /** When the lock lifts by itself, as a short line ("Until 7:00 AM"), or null when it does not say. */
    private static String untilLine(Context context, String reason) {
        long until = GuardApi.epochMs(GuardApi.lastStatus(context).optString("until", ""));
        long now = System.currentTimeMillis();
        if (until <= now) {
            return "paused".equals(reason) ? "Until a parent lifts it" : null;
        }
        java.util.Date at = new java.util.Date(until);
        String time = android.text.format.DateFormat.getTimeFormat(context).format(at);
        java.util.Calendar a = java.util.Calendar.getInstance();
        java.util.Calendar b = java.util.Calendar.getInstance();
        b.setTimeInMillis(until);
        boolean sameDay = a.get(java.util.Calendar.YEAR) == b.get(java.util.Calendar.YEAR)
                && a.get(java.util.Calendar.DAY_OF_YEAR) == b.get(java.util.Calendar.DAY_OF_YEAR);
        if (sameDay) return "Until " + time;
        if (until - now < 36L * 3600_000L) {
            a.add(java.util.Calendar.DAY_OF_YEAR, 1);
            boolean tomorrow = a.get(java.util.Calendar.YEAR) == b.get(java.util.Calendar.YEAR)
                    && a.get(java.util.Calendar.DAY_OF_YEAR) == b.get(java.util.Calendar.DAY_OF_YEAR);
            if (tomorrow) return "Until " + time + " tomorrow";
        }
        return "Until " + new java.text.SimpleDateFormat("EEE", java.util.Locale.getDefault()).format(at) + " " + time;
    }

    /** A small rounded label in the reason's colour. */
    private static TextView pill(Context context, Palette pal, String value, int accent) {
        TextView view = new TextView(context);
        view.setText(value);
        view.setTextSize(14);
        view.setTypeface(Typeface.DEFAULT_BOLD);
        view.setTextColor(pal.dark ? blend(accent, 0xFFFFFFFF, 0.35f) : blend(accent, 0xFF000000, 0.25f));
        view.setGravity(Gravity.CENTER);
        view.setPadding(dp(context, 14), dp(context, 6), dp(context, 14), dp(context, 6));
        view.setBackground(rounded(context, withAlpha(accent, pal.dark ? 0.20f : 0.14f), 999, 0, 0));
        return view;
    }

    /** "Parent unlock": a parent's own login, or their offline code when there is no connection. */
    private static View parentCard(Context context, Palette pal, Sheet sheet, Host host) {
        LinearLayout card = sheetPanel(context, pal);
        card.addView(cardHeader(context, pal, R.drawable.mi_key, pal.brand, "Parent unlock"));

        EditText user = field(context, pal, "Parent's username");
        user.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_NORMAL);
        user.setImeOptions(EditorInfo.IME_ACTION_NEXT);
        addFullWidth(card, user, 0);

        EditText pass = field(context, pal, "Parent's password");
        pass.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        pass.setTypeface(Typeface.DEFAULT);
        pass.setImeOptions(EditorInfo.IME_ACTION_DONE);
        pass.setPadding(dp(context, 14), dp(context, 13), dp(context, 64), dp(context, 13));
        FrameLayout passWrap = new FrameLayout(context);
        passWrap.addView(pass, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.CENTER_VERTICAL));
        TextView showHide = smallToggle(context, pal, "Show");
        FrameLayout.LayoutParams toggleLp = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.END | Gravity.CENTER_VERTICAL);
        toggleLp.rightMargin = dp(context, 6);
        passWrap.addView(showHide, toggleLp);
        showHide.setOnClickListener(v -> {
            boolean hidden = pass.getInputType()
                    == (InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
            pass.setInputType(InputType.TYPE_CLASS_TEXT | (hidden
                    ? InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD : InputType.TYPE_TEXT_VARIATION_PASSWORD));
            // Changing the input type switches Android to a typewriter font; keep the screen's own.
            pass.setTypeface(Typeface.DEFAULT);
            showHide.setText(hidden ? "Hide" : "Show");
            pass.setSelection(pass.getText().length());
        });
        addFullWidth(card, passWrap, 10);

        TextView error = text(context, "", 13, pal.error);
        error.setGravity(Gravity.START);
        error.setPadding(dp(context, 2), dp(context, 8), dp(context, 2), 0);

        Handler main = new Handler(Looper.getMainLooper());
        Button unlock = primaryButton(context, pal, "Unlock");
        unlock.setOnClickListener(v -> {
            unlock.setEnabled(false);
            unlock.setText("Checking…");
            error.setText("");
            String u = user.getText().toString().trim();
            String p = pass.getText().toString();
            new Thread(() -> {
                String problem = GuardApi.parentUnlock(context, u, p);
                main.post(() -> {
                    unlock.setEnabled(true);
                    unlock.setText("Unlock");
                    pass.setText("");
                    if (problem == null) {
                        sheet.hide();
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
        card.addView(error);
        addFullWidth(card, unlock, 12);

        // No connection: a parent's offline code works instead of their login.
        card.addView(divider(context, pal, "NO CONNECTION?", 16));
        LinearLayout codeRow = new LinearLayout(context);
        codeRow.setOrientation(LinearLayout.HORIZONTAL);
        codeRow.setGravity(Gravity.CENTER_VERTICAL);
        EditText offline = field(context, pal, "Offline code");
        offline.setInputType(InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_VARIATION_PASSWORD);
        offline.setTypeface(Typeface.DEFAULT);
        codeRow.addView(offline, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        Button useCode = ghostButton(context, pal, "Use code");
        LinearLayout.LayoutParams codeLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        codeLp.leftMargin = dp(context, 10);
        useCode.setPadding(dp(context, 20), 0, dp(context, 20), 0);
        codeRow.addView(useCode, codeLp);
        useCode.setOnClickListener(v -> {
            useCode.setEnabled(false);
            useCode.setText("Checking…");
            error.setText("");
            String code = offline.getText().toString().trim();
            new Thread(() -> {
                String problem = GuardApi.offlineUnlock(context, code);
                main.post(() -> {
                    useCode.setEnabled(true);
                    useCode.setText("Use code");
                    offline.setText("");
                    if (problem == null) {
                        sheet.hide();
                        host.onUnlocked();
                    } else {
                        error.setText(problem);
                    }
                });
            }).start();
        });
        addFullWidth(card, codeRow, 0);
        Button back = textButton(context, pal, sheet.backLabel);
        back.setOnClickListener(v -> sheet.hide());
        addFullWidth(card, back, 8);
        return card;
    }

    private static LinearLayout.LayoutParams fullWidth(Context context, int topMarginDp) {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.topMargin = dp(context, topMarginDp);
        return lp;
    }

    private static LinearLayout.LayoutParams wrapCentered(Context context, int topMarginDp) {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.gravity = Gravity.CENTER_HORIZONTAL;
        lp.topMargin = dp(context, topMarginDp);
        return lp;
    }

    /**
     * A small panel over whatever the child is doing (not a lock): today's
     * screen time at a glance - what is left, what was used of how much, and
     * what locks next - with "Ask for more time" right there. With a
     * {@code warningTitle} it is the ten- and five-minute warning; without
     * one it is the "how much have I used" panel the Quick Settings tile opens.
     * {@code onClose} takes it down.
     */
    static View buildTimePanel(Context context, String warningTitle, String warningText, Runnable onClose) {
        Palette pal = Palette.resolve(context);
        org.json.JSONObject s = GuardApi.lastStatus(context);
        boolean known = s.length() > 0;
        boolean warning = warningTitle != null && !warningTitle.isEmpty();

        FrameLayout frame = new FrameLayout(context);
        frame.setBackgroundColor(0x99000000);
        frame.setClickable(true);

        LinearLayout card = panel(context, pal.card, 24, pal.stroke);
        card.setPadding(dp(context, 22), dp(context, 18), dp(context, 22), dp(context, 18));
        card.setElevation(dp(context, 6));
        card.setClickable(true);

        card.addView(cardHeader(context, pal, R.drawable.mi_hourglass, pal.brand,
                warning ? warningTitle : "Screen time today"));
        if (warning && warningText != null && !warningText.isEmpty()) {
            TextView w = text(context, warningText, 16, pal.textPrimary);
            w.setGravity(Gravity.START);
            w.setPadding(dp(context, 2), 0, dp(context, 2), dp(context, 12));
            card.addView(w);
        }

        boolean limited = com.jarvisos.app.widgets.ScreenTimeFormat.limited(s);
        if (!known) {
            TextView none = text(context, "Screen time isn't available yet. Check again once the "
                    + DeviceNoun.of(context) + " is online.", 14, pal.textSecondary);
            none.setGravity(Gravity.START);
            card.addView(none);
        } else {
            TextView big = text(context, com.jarvisos.app.widgets.ScreenTimeFormat.headline(s), 24, pal.textPrimary);
            big.setTypeface(Typeface.DEFAULT_BOLD);
            big.setGravity(Gravity.START);
            card.addView(big);

            int used = Math.max(0, s.optInt("used_min", 0));
            String usedLine = limited
                    ? "Used " + com.jarvisos.app.widgets.WidgetTime.minutes(used) + " of "
                    + com.jarvisos.app.widgets.WidgetTime.minutes(TimeUsage.allowed(s))
                    : "Used " + com.jarvisos.app.widgets.WidgetTime.minutes(used) + " today";
            TextView usedView = text(context, usedLine, 15, pal.textSecondary);
            usedView.setGravity(Gravity.START);
            usedView.setPadding(0, dp(context, 2), 0, dp(context, 10));
            card.addView(usedView);

            if (limited) card.addView(usageBar(context, pal, TimeUsage.usedPercent(s)));

            String extra = TimeUsage.extraLine(s);
            String next = com.jarvisos.app.widgets.ScreenTimeFormat.line1(s, java.util.TimeZone.getDefault());
            for (String line : new String[]{extra, next}) {
                if (line == null || line.isEmpty()) continue;
                TextView v = text(context, line, 14, pal.textSecondary);
                v.setGravity(Gravity.START);
                v.setPadding(0, dp(context, 8), 0, 0);
                card.addView(v);
            }
        }

        Sheet sheet = new Sheet(context, "Back");
        boolean canAsk = known && (limited || !s.optString("next_lock_at", "").isEmpty()
                || com.jarvisos.app.widgets.ScreenTimeFormat.locked(s));
        if (canAsk) {
            Button ask = primaryButton(context, pal, "Ask for more time");
            ask.setOnClickListener(v -> sheet.show(askCard(context, pal, sheet, pal.brand)));
            addFullWidth(card, ask, 18);
        }
        Button close = ghostButton(context, pal, warning ? "OK" : "Close");
        close.setOnClickListener(v -> onClose.run());
        addFullWidth(card, close, canAsk ? 10 : 18);

        FrameLayout.LayoutParams cardLp = new FrameLayout.LayoutParams(
                Math.min(dp(context, 440), context.getResources().getDisplayMetrics().widthPixels - dp(context, 32)),
                ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.CENTER);
        ScrollView scroller = new ScrollView(context);
        scroller.addView(card);
        frame.addView(scroller, cardLp);
        frame.addView(sheet.layer, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        return frame;
    }

    /** A plain track with today's used share filled in. */
    private static View usageBar(Context context, Palette pal, int usedPercent) {
        LinearLayout track = new LinearLayout(context);
        track.setOrientation(LinearLayout.HORIZONTAL);
        track.setBackground(rounded(context, pal.tonal, 999, 0, 0));
        int pct = Math.max(0, Math.min(100, usedPercent));
        View fill = new View(context);
        fill.setBackground(rounded(context, pal.brand, 999, 0, 0));
        track.addView(fill, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, pct));
        View rest = new View(context);
        track.addView(rest, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, 100 - pct));
        track.setLayoutParams(new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(context, 10)));
        return track;
    }

    /**
     * The panel painted over the lock, Family Link style: a dimmed lock behind a
     * bottom sheet with a grabber, one accent, and a way back. Only one is up at a
     * time and it never removes the lock under it.
     */
    private static final class Sheet {
        final FrameLayout layer;
        private final Context context;
        /** The way back from a card: to the lock, or to the warning or panel the card was opened from. */
        final String backLabel;

        Sheet(Context context) {
            this(context, "Back to the lock");
        }

        Sheet(Context context, String backLabel) {
            this.context = context;
            this.backLabel = backLabel;
            layer = new FrameLayout(context);
            layer.setBackgroundColor(0x66000000);
            layer.setClickable(true);
            layer.setVisibility(View.GONE);
        }

        void show(View card) {
            layer.removeAllViews();
            // On a tablet the sheet is a centred card, not a strip across the screen.
            int screen = context.getResources().getDisplayMetrics().widthPixels;
            int width = Math.min(screen - dp(context, 24), dp(context, 480));
            ScrollView scroll = new ScrollView(context);
            scroll.setVerticalScrollBarEnabled(false);
            scroll.addView(card);
            FrameLayout.LayoutParams lp = new FrameLayout.LayoutParams(
                    width, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.BOTTOM | Gravity.CENTER_HORIZONTAL);
            lp.setMargins(0, dp(context, 12), 0, dp(context, 12));
            layer.addView(scroll, lp);
            layer.setVisibility(View.VISIBLE);
        }

        void hide() {
            layer.setVisibility(View.GONE);
            layer.removeAllViews();
        }
    }

    /** "Ask for more time": how many minutes and why, sent for a parent to answer. */
    private static View askCard(Context context, Palette pal, Sheet sheet, int accent) {
        int[] minutes = {30};
        LinearLayout card = sheetPanel(context, pal);

        card.addView(cardHeader(context, pal, R.drawable.mi_hourglass, accent, "Ask for more time"));
        TextView hint = text(context, "Your parents get a notification and can allow it for today.", 13, pal.textSecondary);
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
            Button choice = chip(context, pal, labels[i], option == minutes[0], accent);
            choice.setOnClickListener(v -> {
                minutes[0] = option;
                for (int j = 0; j < buttons.length; j++) {
                    styleChip(buttons[j], pal, options[j] == minutes[0], accent);
                }
            });
            buttons[i] = choice;
            choices.addView(choice, new LinearLayout.LayoutParams(0, dp(context, 44), 1f));
        }
        LinearLayout.LayoutParams choicesLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        choicesLp.setMargins(0, dp(context, 10), 0, dp(context, 10));
        card.addView(choices, choicesLp);

        EditText note = field(context, pal, "Why? (optional)");
        note.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_MULTI_LINE);
        note.setMinLines(2);
        addFullWidth(card, note, 0);

        TextView status = statusLine(context, pal);
        card.addView(status);
        Button send = primaryButton(context, pal, "Send request");
        send.setOnClickListener(v -> {
            send.setEnabled(false);
            send.setText("Sending…");
            status.setTextColor(pal.textSecondary);
            status.setText("Asking…");
            String why = note.getText().toString().trim();
            new Thread(() -> {
                String problem = GuardApi.askMoreTime(context, minutes[0], why);
                new Handler(Looper.getMainLooper()).post(() -> {
                    send.setEnabled(true);
                    send.setText("Send request");
                    if (problem == null) {
                        sheet.show(confirmation(context, pal, sheet, pal.brand, "Asked!",
                                "A parent will answer soon."));
                    } else {
                        status.setTextColor(pal.error);
                        status.setText(problem);
                    }
                });
            }).start();
        });
        addFullWidth(card, send, 10);
        card.addView(ghostButton(context, pal, sheet, sheet.backLabel));
        return card;
    }

    /** "Ask a parent to unblock": asks a parent to allow a blocked or time-limited app. */
    private static View askAppCard(Context context, Palette pal, Sheet sheet, String pkg, String appName, int accent) {
        LinearLayout card = sheetPanel(context, pal);

        card.addView(cardHeader(context, pal, R.drawable.mi_lock_open, accent, "Ask to use " + appName));
        TextView hint = text(context, "Your parents can allow it for an hour or for good.", 13, pal.textSecondary);
        hint.setGravity(Gravity.START);
        hint.setPadding(dp(context, 2), 0, dp(context, 2), dp(context, 10));
        card.addView(hint);

        EditText note = field(context, pal, "Why? (optional)");
        note.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_MULTI_LINE);
        note.setMinLines(2);
        addFullWidth(card, note, 0);

        TextView status = statusLine(context, pal);
        card.addView(status);
        Button send = primaryButton(context, pal, "Ask a parent");
        send.setOnClickListener(v -> {
            send.setEnabled(false);
            send.setText("Asking…");
            status.setTextColor(pal.textSecondary);
            status.setText("Asking…");
            String why = note.getText().toString().trim();
            new Thread(() -> {
                String problem = GuardApi.askParent(context, "app_unblock", pkg, appName, why);
                new Handler(Looper.getMainLooper()).post(() -> {
                    send.setEnabled(true);
                    send.setText("Ask a parent");
                    if (problem == null) {
                        sheet.show(confirmation(context, pal, sheet, pal.brand, "Asked!",
                                "A parent will answer soon."));
                    } else {
                        status.setTextColor(pal.error);
                        status.setText(problem);
                    }
                });
            }).start();
        });
        addFullWidth(card, send, 10);
        card.addView(ghostButton(context, pal, sheet, sheet.backLabel));
        return card;
    }

    /** "Message a parent": a short chat message to them, sent from the lock. */
    private static View messageCard(Context context, Palette pal, Sheet sheet, int accent) {
        LinearLayout card = sheetPanel(context, pal);

        card.addView(cardHeader(context, pal, R.drawable.mi_chat, accent, "Message a parent"));
        TextView hint = text(context, "It goes to your parents in the family chat.", 13, pal.textSecondary);
        hint.setGravity(Gravity.START);
        hint.setPadding(dp(context, 2), 0, dp(context, 2), dp(context, 10));
        card.addView(hint);

        EditText message = field(context, pal, "Type a message");
        message.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_MULTI_LINE);
        message.setMinLines(2);
        addFullWidth(card, message, 0);

        TextView status = statusLine(context, pal);
        card.addView(status);
        Button send = primaryButton(context, pal, "Send");
        send.setOnClickListener(v -> {
            String text = message.getText().toString().trim();
            if (text.isEmpty()) {
                status.setTextColor(pal.error);
                status.setText("Type a message first.");
                return;
            }
            send.setEnabled(false);
            send.setText("Sending…");
            status.setTextColor(pal.textSecondary);
            status.setText("Sending…");
            new Thread(() -> {
                String problem = GuardApi.messageParents(context, text);
                new Handler(Looper.getMainLooper()).post(() -> {
                    send.setEnabled(true);
                    send.setText("Send");
                    if (problem == null) {
                        message.setText("");
                        sheet.show(confirmation(context, pal, sheet, pal.brand, "Sent!",
                                "A parent will see it in the family chat."));
                    } else {
                        status.setTextColor(pal.error);
                        status.setText(problem);
                    }
                });
            }).start();
        });
        addFullWidth(card, send, 10);
        card.addView(ghostButton(context, pal, sheet, sheet.backLabel));
        return card;
    }

    /** What a sent ask or message turns into: one big tick, one line, one way back. */
    private static View confirmation(Context context, Palette pal, Sheet sheet, int accent, String titleText, String subText) {
        LinearLayout panel = new LinearLayout(context);
        panel.setOrientation(LinearLayout.VERTICAL);
        panel.setGravity(Gravity.CENTER);
        panel.setPadding(dp(context, 26), dp(context, 26), dp(context, 26), dp(context, 22));
        panel.setBackground(rounded(context, pal.card, 24, pal.stroke, 1));
        panel.setElevation(dp(context, 2));

        panel.addView(iconBadge(context, R.drawable.mi_check, accent, 92));

        TextView title = text(context, titleText, 21, pal.textPrimary);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        title.setPadding(0, dp(context, 16), 0, dp(context, 4));
        panel.addView(title);
        TextView sub = text(context, subText, 14, pal.textSecondary);
        sub.setPadding(0, 0, 0, dp(context, 16));
        panel.addView(sub);
        Button back = primaryButton(context, pal, sheet.backLabel);
        back.setOnClickListener(v -> sheet.hide());
        addFullWidth(panel, back, 0);
        return panel;
    }

    /** The bottom-sheet panel the ask and message cards live in. */
    private static LinearLayout sheetPanel(Context context, Palette pal) {
        LinearLayout card = new LinearLayout(context);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(context, 22), dp(context, 10), dp(context, 22), dp(context, 18));
        card.setBackground(rounded(context, pal.card, 24, pal.stroke, 1));
        card.setElevation(dp(context, 2));
        grabber(card, context, pal);
        return card;
    }

    private static void grabber(LinearLayout card, Context context, Palette pal) {
        View bar = new View(context);
        bar.setBackground(rounded(context, pal.grabberBar, 2, 0, 0));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(dp(context, 40), dp(context, 4));
        lp.gravity = Gravity.CENTER_HORIZONTAL;
        lp.bottomMargin = dp(context, 14);
        card.addView(bar, lp);
    }

    private static LinearLayout cardHeader(Context context, Palette pal, int iconRes, int accent, String title) {
        LinearLayout row = new LinearLayout(context);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding(dp(context, 2), dp(context, 2), dp(context, 2), dp(context, 12));
        row.addView(iconBadge(context, iconRes, accent, 36));
        TextView text = text(context, title, 19, pal.textPrimary);
        text.setGravity(Gravity.START);
        text.setTypeface(Typeface.DEFAULT_BOLD);
        text.setPadding(dp(context, 12), 0, 0, 0);
        row.addView(text, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        return row;
    }

    private static LinearLayout smallCapsLabel(Context context, Palette pal, String value) {
        TextView label = new TextView(context);
        label.setText(value);
        label.setTextSize(11);
        label.setTextColor(pal.textFaint);
        label.setLetterSpacing(0.18f);
        label.setPadding(dp(context, 2), 0, dp(context, 2), dp(context, 2));
        LinearLayout row = new LinearLayout(context);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.addView(label);
        return row;
    }

    private static View divider(Context context, Palette pal, String label, int topMarginDp) {
        LinearLayout row = new LinearLayout(context);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding(0, dp(context, topMarginDp), 0, dp(context, 12));
        View left = new View(context);
        left.setBackgroundColor(pal.dividerLine);
        row.addView(left, new LinearLayout.LayoutParams(0, dp(context, 1), 1f));
        LinearLayout value = smallCapsLabel(context, pal, label);
        value.setPadding(dp(context, 10), 0, dp(context, 10), 0);
        row.addView(value);
        View right = new View(context);
        right.setBackgroundColor(pal.dividerLine);
        row.addView(right, new LinearLayout.LayoutParams(0, dp(context, 1), 1f));
        return row;
    }

    /** The reason at a glance: the icon on a soft disc inside a fainter halo, always a true circle. */
    private static FrameLayout hero(Context context, Palette pal, int iconRes, int accent, int sizeDp) {
        int size = dp(context, sizeDp);
        FrameLayout halo = new FrameLayout(context);
        GradientDrawable outer = new GradientDrawable();
        outer.setShape(GradientDrawable.OVAL);
        outer.setColor(withAlpha(accent, pal.dark ? 0.10f : 0.08f));
        halo.setBackground(outer);
        halo.setMinimumWidth(size);
        halo.setMinimumHeight(size);
        int discSize = Math.round(size * 0.74f);
        FrameLayout disc = new FrameLayout(context);
        GradientDrawable inner = new GradientDrawable();
        inner.setShape(GradientDrawable.OVAL);
        inner.setColor(withAlpha(accent, pal.dark ? 0.22f : 0.16f));
        inner.setStroke(dp(context, 1), withAlpha(accent, 0.40f));
        disc.setBackground(inner);
        ImageView icon = new ImageView(context);
        icon.setImageResource(iconRes);
        icon.setColorFilter(accent, PorterDuff.Mode.SRC_IN);
        int iconSize = Math.round(size * 0.40f);
        disc.addView(icon, new FrameLayout.LayoutParams(iconSize, iconSize, Gravity.CENTER));
        halo.addView(disc, new FrameLayout.LayoutParams(discSize, discSize, Gravity.CENTER));
        // Fixed size both ways: inside a vertical column a view without its own
        // size is stretched to the full width, which turned this into an oval.
        halo.setLayoutParams(new LinearLayout.LayoutParams(size, size));
        return halo;
    }

    private static FrameLayout iconBadge(Context context, int iconRes, int accent, int sizeDp) {
        FrameLayout badge = new FrameLayout(context);
        GradientDrawable circle = new GradientDrawable();
        circle.setShape(GradientDrawable.OVAL);
        circle.setColor(withAlpha(accent, 0.16f));
        badge.setBackground(circle);
        int size = dp(context, sizeDp);
        // The badges are added without layout params (LinearLayout parents
        // generate their own), so the circle keeps its size as a minimum.
        badge.setMinimumWidth(size);
        badge.setMinimumHeight(size);
        LinearLayout.LayoutParams badgeLp = new LinearLayout.LayoutParams(size, size);
        badgeLp.gravity = Gravity.CENTER_HORIZONTAL;
        badge.setLayoutParams(badgeLp);
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

    private static EditText field(Context context, Palette pal, String hint) {
        EditText field = new EditText(context);
        field.setHint(hint);
        field.setTextSize(16);
        field.setTextColor(pal.textPrimary);
        field.setHintTextColor(pal.textFaint);
        field.setPadding(dp(context, 14), dp(context, 13), dp(context, 14), dp(context, 13));
        field.setBackground(rounded(context, pal.field, 14, pal.fieldStroke, 1));
        field.setLayoutParams(new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        return field;
    }

    private static Button primaryButton(Context context, Palette pal, String label) {
        Button button = new Button(context);
        button.setText(label);
        button.setTextSize(16);
        button.setAllCaps(false);
        button.setTypeface(Typeface.DEFAULT_BOLD);
        button.setTextColor(pal.onBrand);
        button.setBackground(rounded(context, pal.brand, 999, 0, 0));
        button.setMinHeight(dp(context, 54));
        button.setPadding(dp(context, 20), dp(context, 12), dp(context, 20), dp(context, 12));
        button.setLayoutParams(new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        return button;
    }

    private static Button tonalButton(Context context, Palette pal, String label) {
        Button button = new Button(context);
        button.setText(label);
        button.setTextSize(16);
        button.setAllCaps(false);
        button.setTypeface(Typeface.DEFAULT_BOLD);
        button.setTextColor(pal.onTonal);
        button.setBackground(rounded(context, pal.tonal, 999, 0, 0));
        button.setMinHeight(dp(context, 54));
        button.setPadding(dp(context, 20), dp(context, 12), dp(context, 20), dp(context, 12));
        button.setLayoutParams(new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        return button;
    }

    private static Button ghostButton(Context context, Palette pal, String label) {
        return ghostButton(context, pal, null, label);
    }

    private static Button ghostButton(Context context, Palette pal, Sheet sheet, String label) {
        Button button = new Button(context);
        button.setText(label);
        button.setTextSize(15);
        button.setAllCaps(false);
        button.setTypeface(Typeface.DEFAULT_BOLD);
        button.setTextColor(pal.brand);
        button.setBackground(rounded(context, 0x00000000, 999, pal.brand, 1));
        button.setMinHeight(dp(context, 50));
        button.setPadding(dp(context, 16), dp(context, 10), dp(context, 16), dp(context, 10));
        button.setLayoutParams(new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        if (sheet != null) {
            button.setOnClickListener(v -> sheet.hide());
        }
        return button;
    }

    private static Button textButton(Context context, Palette pal, String label) {
        Button button = new Button(context);
        button.setText(label);
        button.setTextSize(14);
        button.setAllCaps(false);
        button.setTypeface(Typeface.DEFAULT_BOLD);
        button.setTextColor(pal.brand);
        button.setBackgroundColor(Color.TRANSPARENT);
        button.setMinHeight(dp(context, 44));
        button.setLayoutParams(new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        return button;
    }

    private static Button tonalButton(Context context, Palette pal, String label, int iconRes) {
        Button button = tonalButton(context, pal, label);
        button.setText(withIcon(context, label, iconRes, pal.onTonal, 20));
        oneLine(button, 13, 16);
        return button;
    }

    /**
     * The label with its icon just before it, so the two are centred together
     * in the button (a compound drawable sits at the button's far edge instead).
     */
    private static CharSequence withIcon(Context context, String label, int iconRes, int tint, int sizeDp) {
        android.graphics.drawable.Drawable icon = context.getDrawable(iconRes);
        if (icon == null) return label;
        icon = icon.mutate();
        icon.setTint(tint);
        int size = dp(context, sizeDp);
        icon.setBounds(0, 0, size, size);
        android.text.SpannableString text = new android.text.SpannableString("\u00A0\u00A0" + label);
        text.setSpan(new android.text.style.ImageSpan(icon, android.text.style.DynamicDrawableSpan.ALIGN_CENTER),
                0, 1, android.text.Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
        return text;
    }

    /** Keeps a side-by-side button's label on one line, shrinking it a little on a narrow screen or a big font. */
    private static void oneLine(Button button, int minSp, int maxSp) {
        button.setMaxLines(1);
        button.setAutoSizeTextTypeUniformWithConfiguration(minSp, maxSp, 1, android.util.TypedValue.COMPLEX_UNIT_SP);
    }

    /** The child's parents who have a phone number, as {name, number}, from the guard status. */
    private static java.util.List<String[]> parentPhones(Context context) {
        java.util.List<String[]> out = new java.util.ArrayList<>();
        org.json.JSONArray list = GuardApi.lastStatus(context).optJSONArray("parent_contacts");
        if (list == null) return out;
        for (int i = 0; i < list.length(); i++) {
            org.json.JSONObject p = list.optJSONObject(i);
            if (p == null) continue;
            String phone = p.optString("phone", "").trim();
            if (phone.isEmpty()) continue;
            String name = p.optString("name", "").trim();
            out.add(new String[]{name.isEmpty() ? "Parent" : name, phone});
        }
        return out;
    }

    /** Opens the phone's dialer on a parent's number; the child presses call. */
    private static void dial(Context context, String number) {
        try {
            android.content.Intent intent = new android.content.Intent(android.content.Intent.ACTION_DIAL,
                    android.net.Uri.fromParts("tel", number, null));
            intent.addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK);
            context.startActivity(intent);
        } catch (Exception ignored) {
            // No dialer on this device (a tablet): the message button is still there.
        }
    }

    /** "Call a parent" with more than one parent: one row each. */
    private static View callCard(Context context, Palette pal, Sheet sheet, java.util.List<String[]> parents) {
        LinearLayout card = sheetPanel(context, pal);
        card.addView(cardHeader(context, pal, R.drawable.mi_call, pal.brand, "Call a parent"));
        for (String[] parent : parents) {
            Button call = tonalButton(context, pal, "Call " + parent[0], R.drawable.mi_call);
            call.setOnClickListener(v -> {
                sheet.hide();
                dial(context, parent[1]);
            });
            addFullWidth(card, call, 10);
        }
        Button back = textButton(context, pal, sheet.backLabel);
        back.setOnClickListener(v -> sheet.hide());
        addFullWidth(card, back, 8);
        return card;
    }

    /** A low-key outlined pill with a small icon, for the ways out that are not the child's next step. */
    private static Button quietButton(Context context, Palette pal, String label, int iconRes) {
        Button button = new Button(context);
        button.setText(label);
        button.setTextSize(14);
        button.setAllCaps(false);
        button.setTypeface(Typeface.DEFAULT_BOLD);
        button.setTextColor(pal.textSecondary);
        button.setBackground(rounded(context, withAlpha(pal.card, 0.55f), 999, pal.stroke, 1));
        button.setMinHeight(dp(context, 48));
        button.setPadding(dp(context, 14), dp(context, 8), dp(context, 14), dp(context, 8));
        button.setText(withIcon(context, label, iconRes, pal.textSecondary, 18));
        oneLine(button, 11, 14);
        button.setStateListAnimator(null);
        return button;
    }

    private static Button chip(Context context, Palette pal, String label, boolean selected, int accent) {
        Button button = new Button(context);
        button.setText(label);
        button.setTextSize(14);
        button.setAllCaps(false);
        button.setMinHeight(dp(context, 44));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                0, dp(context, 44), 1f);
        lp.setMargins(dp(context, 4), 0, dp(context, 4), 0);
        button.setLayoutParams(lp);
        styleChip(button, pal, selected, accent);
        return button;
    }

    private static void styleChip(Button button, Palette pal, boolean selected, int accent) {
        if (selected) {
            button.setBackground(rounded(button.getContext(), pal.brand, 999, 0, 0));
            button.setTextColor(pal.onBrand);
            button.setTypeface(Typeface.DEFAULT_BOLD);
        } else {
            button.setBackground(rounded(button.getContext(), pal.field, 999, pal.fieldStroke, 1));
            button.setTextColor(pal.textSecondary);
            button.setTypeface(Typeface.DEFAULT);
        }
    }

    private static TextView smallToggle(Context context, Palette pal, String label) {
        TextView toggle = new TextView(context);
        toggle.setText(label);
        toggle.setTextSize(13);
        toggle.setTypeface(Typeface.DEFAULT_BOLD);
        toggle.setTextColor(pal.brand);
        toggle.setGravity(Gravity.CENTER);
        toggle.setPadding(dp(context, 12), dp(context, 8), dp(context, 12), dp(context, 8));
        toggle.setClickable(true);
        toggle.setFocusable(true);
        return toggle;
    }

    private static TextView title(Context context, Palette pal, String value) {
        TextView view = new TextView(context);
        view.setText(value);
        view.setTextSize(26);
        view.setTypeface(Typeface.DEFAULT_BOLD);
        view.setTextColor(pal.textPrimary);
        view.setGravity(Gravity.CENTER);
        view.setPadding(0, dp(context, 4), 0, 0);
        return view;
    }

    private static TextView statusLine(Context context, Palette pal) {
        TextView view = text(context, "", 13, pal.textFaint);
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
