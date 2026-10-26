package com.jarvisos.app;

import android.content.Intent;
import android.graphics.Color;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.view.Gravity;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;

import androidx.activity.ComponentActivity;
import androidx.activity.OnBackPressedCallback;

/**
 * What the screen guard shows instead of a blocked screen.
 *
 * Tamper mode: "A parent needs to sign in" with a parent's own Jarvis login,
 * checked by the server (only this child's parents, five tries then a wait).
 * Locked mode: why the phone is locked (bedtime, school, the day's time, a
 * pause), a way to ask for more time in Jarvis, and the same parent login.
 *
 * Built in code, no layout files, so it stays one small class.
 */
public class GuardScreenActivity extends ComponentActivity {

    static final String EXTRA_MODE = "mode";
    static final String MODE_TAMPER = "tamper";
    static final String MODE_LOCKED = "locked";
    /** One app is off (blocked by a parent, or its own minutes are up); the rest of the phone works. */
    static final String MODE_APP = "app";
    /** An app installed since the guard started waits for a parent's OK (or a parent said no). */
    static final String MODE_INSTALL = "install";
    /** An app asked for a kind of permission a parent approves first; the dialog was dismissed. */
    static final String MODE_PERMISSION = "permission";
    /** An app that gets around the web filter (FilterBypass), closed while the filter is on. */
    static final String MODE_BYPASS = "bypass";
    /** The app's name, for the install and permission screens. */
    static final String EXTRA_APP = "app";
    /** A parent already said no. */
    static final String EXTRA_DENIED = "denied";

    /**
     * How often a kiosk lock re-checks the cached status, so a lock a parent
     * lifts remotely (or a window that opens) releases the pin without waiting
     * for the child to find a way out.
     */
    private static final long KIOSK_CHECK_MS = 5000;
    /** This screen is the locked cover, not a tamper/app/install/permission/bypass screen. */
    private boolean lockedMode = false;
    /** Android's lock-task (kiosk) mode is holding this screen: release it when it goes. */
    private boolean kiosk = false;
    private final Handler kioskCheck = new Handler(Looper.getMainLooper());
    private final Runnable kioskTick = new Runnable() {
        @Override
        public void run() {
            if (!kiosk) return;
            if (!lockTaskWanted()) {
                // A parent lifted the lock (or their unlock window opened): leave the kiosk.
                finish();
                return;
            }
            kioskCheck.postDelayed(this, KIOSK_CHECK_MS);
        }
    };

    @Override
    protected void onResume() {
        super.onResume();
        if (lockedMode && lockTaskWanted()) enterKiosk();
    }

    @Override
    protected void onDestroy() {
        kioskCheck.removeCallbacks(kioskTick);
        releaseKiosk();
        super.onDestroy();
    }

    /**
     * Whether Android's lock-task (kiosk) mode should hold this screen now: a
     * device owner, on a protected phone, locked, and not inside a parent's
     * unlock window (see {@link DeviceOwnerRules#lockTask}). On any other phone
     * this is false and the screen is only the Accessibility soft cover.
     */
    private boolean lockTaskWanted() {
        return DeviceOwnerRules.lockTask(DeviceOwnerGuard.isOwner(this), GuardApi.isProtected(this),
                GuardApi.isLocked(this), GuardApi.isUnlocked(this));
    }

    /** Pin the phone to this screen: allow Jarvis, then start lock task. */
    private void enterKiosk() {
        DeviceOwnerGuard.enterLockTask(this);
        try {
            startLockTask();
            kiosk = true;
            kioskCheck.removeCallbacks(kioskTick);
            kioskCheck.postDelayed(kioskTick, KIOSK_CHECK_MS);
        } catch (Exception ignored) {
            // Not allowlisted (older Android, or no longer device owner): the soft cover still shows.
        }
    }

    /** Leave lock task and drop the allowlist, so the phone is free again. */
    private void releaseKiosk() {
        if (!kiosk) return;
        kiosk = false;
        try {
            stopLockTask();
        } catch (Exception ignored) {
            // Never actually in lock task.
        }
        DeviceOwnerGuard.exitLockTask(this);
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        String mode = getIntent().getStringExtra(EXTRA_MODE);
        boolean tamper = MODE_TAMPER.equals(mode);
        boolean appOff = MODE_APP.equals(mode);
        boolean install = MODE_INSTALL.equals(mode);
        boolean permission = MODE_PERMISSION.equals(mode);
        boolean bypass = MODE_BYPASS.equals(mode);
        boolean denied = getIntent().getBooleanExtra(EXTRA_DENIED, false);
        // The plain locked cover (bedtime, school, the day's time, a pause), not one of the others.
        lockedMode = !tamper && !appOff && !install && !permission && !bypass;
        String app = getIntent().getStringExtra(EXTRA_APP);
        String appName = app == null || app.isEmpty() ? "This app" : app;

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setGravity(Gravity.CENTER);
        root.setPadding(64, 64, 64, 64);
        root.setBackgroundColor(Color.parseColor("#030711"));

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
            titleText = lockedTitle();
            bodyText = "Calls, texts and Jarvis still work. You can ask a parent for more time in Jarvis.";
        }
        TextView title = text(titleText, 24);
        TextView body = text(bodyText, 16);
        body.setTextColor(Color.parseColor("#94A3B8"));

        EditText user = new EditText(this);
        user.setHint("Parent's username");
        user.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_NORMAL);
        EditText pass = new EditText(this);
        pass.setHint("Parent's password");
        pass.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        for (EditText field : new EditText[] {user, pass}) {
            field.setTextColor(Color.WHITE);
            field.setHintTextColor(Color.parseColor("#64748B"));
        }
        TextView error = text("", 14);
        error.setTextColor(Color.parseColor("#FCD34D"));

        Button unlock = new Button(this);
        unlock.setText("Parent unlock");
        unlock.setOnClickListener(v -> {
            unlock.setEnabled(false);
            String u = user.getText().toString().trim();
            String p = pass.getText().toString();
            new Thread(() -> {
                String problem = GuardApi.parentUnlock(this, u, p);
                runOnUiThread(() -> {
                    unlock.setEnabled(true);
                    pass.setText("");
                    if (problem == null) {
                        finish();
                    } else {
                        error.setText(problem);
                    }
                });
            }).start();
        });

        root.addView(title);
        root.addView(body);
        if (!tamper && !install && !permission && !bypass) {
            Button ask = new Button(this);
            ask.setText("Ask for more time");
            ask.setOnClickListener(v -> {
                // Health holds the "Ask for more time" card.
                startActivity(new Intent(this, MainActivity.class)
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP)
                        .putExtra(PushNotifier.EXTRA_OPEN_PATH, "/health"));
                finish();
            });
            root.addView(ask);
        }
        // No connection: a parent's offline code works instead of their login.
        EditText offline = new EditText(this);
        offline.setHint("Parent's offline code");
        offline.setInputType(InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_VARIATION_PASSWORD);
        offline.setTextColor(Color.WHITE);
        offline.setHintTextColor(Color.parseColor("#64748B"));
        Button useCode = new Button(this);
        useCode.setText("Use offline code");
        useCode.setOnClickListener(v -> {
            useCode.setEnabled(false);
            String code = offline.getText().toString().trim();
            new Thread(() -> {
                String problem = GuardApi.offlineUnlock(this, code);
                runOnUiThread(() -> {
                    useCode.setEnabled(true);
                    offline.setText("");
                    if (problem == null) {
                        finish();
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
        setContentView(root, new ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        // Back (button or gesture) would return to the blocked screen; home is the way out.
        getOnBackPressedDispatcher().addCallback(this, new OnBackPressedCallback(true) {
            @Override
            public void handleOnBackPressed() {
                startActivity(new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
                finish();
            }
        });
    }

    private String lockedTitle() {
        switch (GuardApi.lockReason(this)) {
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

    private TextView text(String value, int sizeSp) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(sizeSp);
        view.setTextColor(Color.WHITE);
        view.setGravity(Gravity.CENTER);
        view.setPadding(0, 16, 0, 16);
        return view;
    }
}
