package com.jarvisos.app;

import android.content.Intent;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.View;
import android.view.ViewGroup;

import androidx.activity.ComponentActivity;
import androidx.activity.OnBackPressedCallback;

/**
 * The Accessibility cover: what the screen guard shows over a screen it takes
 * off (a tamper page, a blocked app, a new app waiting for a parent, a
 * permission a parent approves first, a bypass app), and the plain locked
 * cover on a phone that is not a device owner. The screen itself is built by
 * {@link GuardScreenView}, shared with the full-screen lock overlay
 * ({@link GuardOverlay}).
 *
 * This is a normal activity, so Home and Back leave it; the full-screen lock on
 * a phone that is not a device owner is the overlay instead, which cannot be
 * left that way. On a device owner the locked cover is pinned with Android's
 * lock-task (kiosk) mode, and a screen-time lock is Android's own lock screen
 * (see {@link DeviceOwnerRules}).
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
    /** The app's package and name, for the app-off, install and permission screens. */
    static final String EXTRA_PACKAGE = "package";
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
     * this is false and the screen is only the Accessibility cover.
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
            // Not allowlisted (older Android, or no longer device owner): the cover still shows.
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
        // The plain locked cover (bedtime, school, the day's time, a pause), not one of the others.
        lockedMode = !tamper && !appOff && !install && !permission && !bypass;

        View content = GuardScreenView.build(this, mode, getIntent().getStringExtra(EXTRA_PACKAGE),
                getIntent().getStringExtra(EXTRA_APP), getIntent().getBooleanExtra(EXTRA_DENIED, false),
                new GuardScreenView.Host() {
                    @Override
                    public void onUnlocked() {
                        finish();
                    }

                    @Override
                    public void onOpenJarvis() {
                        // Jarvis is always allowed; the lock comes back when the
                        // child leaves it. Asking for more time and messaging a
                        // parent stay on this screen, painted over the lock.
                        startActivity(new Intent(GuardScreenActivity.this, MainActivity.class)
                                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP)
                                .putExtra(PushNotifier.EXTRA_OPEN_PATH, "/"));
                        finish();
                    }
                });
        setContentView(content, new ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        // Back (button or gesture) would return to the blocked screen; home is the way out.
        getOnBackPressedDispatcher().addCallback(this, new OnBackPressedCallback(true) {
            @Override
            public void handleOnBackPressed() {
                startActivity(new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
                finish();
            }
        });
    }
}
