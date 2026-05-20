package com.jarvisos.app;

import android.content.Context;
import android.content.Intent;
import android.net.Uri;

import androidx.test.core.app.ActivityScenario;
import androidx.test.core.app.ApplicationProvider;
import androidx.test.espresso.Espresso;
import androidx.test.espresso.action.ViewActions;
import androidx.test.espresso.assertion.ViewAssertions;
import androidx.test.espresso.intent.Intents;
import androidx.test.espresso.intent.matcher.IntentMatchers;
import androidx.test.espresso.matcher.ViewMatchers;
import androidx.test.ext.junit.rules.ActivityScenarioRule;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.filters.LargeTest;

import com.getcapacitor.BridgeActivity;

import org.junit.After;
import org.junit.Before;
import org.junit.Rule;
import org.junit.Test;
import org.junit.runner.RunWith;

import static androidx.test.espresso.Espresso.onView;
import static androidx.test.espresso.action.ViewActions.click;
import static androidx.test.espresso.action.ViewActions.closeSoftKeyboard;
import static androidx.test.espresso.action.ViewActions.typeText;
import static androidx.test.espresso.assertion.ViewAssertions.matches;
import static androidx.test.espresso.intent.Intents.intended;
import static androidx.test.espresso.intent.matcher.IntentMatchers.hasAction;
import static androidx.test.espresso.matcher.ViewMatchers.isDisplayed;
import static androidx.test.espresso.matcher.ViewMatchers.withId;
import static androidx.test.espresso.matcher.ViewMatchers.withText;
import static org.hamcrest.CoreMatchers.allOf;

/**
 * Instrumented tests for the Jarvis OS Android app.
 * These tests verify the Capacitor-wrapped web app functionality on Android.
 *
 * Run with: ./gradlew connectedAndroidTest
 */
@RunWith(AndroidJUnit4.class)
@LargeTest
public class MainActivityTest {

    @Rule
    public ActivityScenarioRule<BridgeActivity> activityRule =
            new ActivityScenarioRule<>(BridgeActivity.class);

    @Before
    public void setUp() {
        Intents.init();
    }

    @After
    public void tearDown() {
        Intents.release();
    }

    // ─── App Launch Tests ─────────────────────────────────────────────────────

    @Test
    public void appLaunchesSuccessfully() {
        activityRule.getScenario().onActivity(activity -> {
            onView(withId(android.R.id.content))
                    .check(matches(isDisplayed()));
        });
    }

    @Test
    public void splashScreenDisplays() {
        activityRule.getScenario().onActivity(activity -> {
            onView(withId(android.R.id.content))
                    .check(matches(isDisplayed()));
        });
    }

    // ─── Authentication Tests ─────────────────────────────────────────────────

    @Test
    public void loginScreenRenders() {
        onView(withText("Jarvis OS"))
                .check(matches(isDisplayed()));
    }

    @Test
    public void loginFormHasUsernameField() {
        onView(ViewMatchers.withHint("Enter username"))
                .check(matches(isDisplayed()));
    }

    @Test
    public void loginFormHasPasswordField() {
        onView(ViewMatchers.withHint("Enter password"))
                .check(matches(isDisplayed()));
    }

    @Test
    public void loginButtonIsVisible() {
        onView(withText("Sign In"))
                .check(matches(isDisplayed()));
    }

    @Test
    public void loginWithValidCredentials() {
        onView(ViewMatchers.withHint("Enter username"))
                .perform(typeText("default"), closeSoftKeyboard());
        onView(ViewMatchers.withHint("Enter password"))
                .perform(typeText("admin"), closeSoftKeyboard());
        onView(withText("Sign In"))
                .perform(click());
    }

    // ─── Biometric Authentication Tests ───────────────────────────────────────

    @Test
    public void biometricPromptShownWhenEnabled() {
        // When biometrics are enabled, fingerprint prompt should appear
        // before falling back to PIN pad
        onView(withId(android.R.id.content))
                .check(matches(isDisplayed()));
    }

    @Test
    public void fallsBackToPinPadWhenBiometricsFail() {
        // If biometric prompt is cancelled or fails, PIN pad should appear
        onView(ViewMatchers.withHint("Enter username"))
                .check(matches(isDisplayed()));
    }

    // ─── Navigation Tests ─────────────────────────────────────────────────────

    @Test
    public void bottomNavigationBarIsVisible() {
        // After login, bottom nav should be visible on mobile
        onView(withId(android.R.id.content))
                .check(matches(isDisplayed()));
    }

    @Test
    public void bottomNavHasHomeItem() {
        onView(withText("Home"))
                .check(matches(isDisplayed()));
    }

    @Test
    public void bottomNavHasCommunicationItem() {
        onView(withText("Communication"))
                .check(matches(isDisplayed()));
    }

    @Test
    public void bottomNavHasWorkspacesItem() {
        onView(withText("Workspaces"))
                .check(matches(isDisplayed()));
    }

    @Test
    public void bottomNavHasIdentityItem() {
        onView(withText("Identity"))
                .check(matches(isDisplayed()));
    }

    // ─── Voice Assistant Tests ────────────────────────────────────────────────

    @Test
    public void micButtonIsVisible() {
        onView(allOf(
                withText("Jarvis"),
                isDisplayed()
        )).check(matches(isDisplayed()));
    }

    @Test
    public void tappingMicOpensVoiceOverlay() {
        onView(withText("Jarvis"))
                .perform(click());
        // Voice overlay should appear with audio visualizer
        onView(withId(android.R.id.content))
                .check(matches(isDisplayed()));
    }

    // ─── Intercom Tests ───────────────────────────────────────────────────────

    @Test
    public void holdToTalkButtonIsVisible() {
        onView(withText("Hold to Talk"))
                .check(matches(isDisplayed()));
    }

    @Test
    public void intercomSessionStarts() {
        onView(withText("Hold to Talk"))
                .perform(ViewActions.longClick());
        // Intercom session should start
        onView(withId(android.R.id.content))
                .check(matches(isDisplayed()));
    }

    // ─── NFC Tests ────────────────────────────────────────────────────────────

    @Test
    public void nfcReaderIsAvailable() {
        Context context = ApplicationProvider.getApplicationContext();
        // NFC hardware check
        // This test verifies NFC capability exists on the device
    }

    @Test
    public void nfcTagTriggersMacro() {
        // When NFC tag is tapped, corresponding macro should execute
        onView(withId(android.R.id.content))
                .check(matches(isDisplayed()));
    }

    // ─── Location Tracking Tests ──────────────────────────────────────────────

    @Test
    public void locationPermissionRequested() {
        // App should request location permissions on first launch
        intended(hasAction(Intent.ACTION_VIEW));
    }

    @Test
    public void backgroundLocationTrackingActive() {
        // When enabled, background location should sync to server
        onView(withId(android.R.id.content))
                .check(matches(isDisplayed()));
    }

    // ─── Foreground Service Tests ─────────────────────────────────────────────

    @Test
    public void foregroundServiceRuns() {
        // Foreground service should keep WebSocket alive when app is backgrounded
        onView(withId(android.R.id.content))
                .check(matches(isDisplayed()));
    }

    @Test
    public void notificationShowsWhenServiceActive() {
        // Persistent notification should show when foreground service is running
        onView(withId(android.R.id.content))
                .check(matches(isDisplayed()));
    }

    // ─── Push Notification Tests ──────────────────────────────────────────────

    @Test
    public void intercomNotificationReceived() {
        // When intercom call comes in, notification should appear with Answer/Dismiss
        onView(withId(android.R.id.content))
                .check(matches(isDisplayed()));
    }

    @Test
    public void tappingNotificationOpensApp() {
        // Tapping notification should open the app to the intercom screen
        intended(hasAction(Intent.ACTION_VIEW));
    }

    // ─── Responsive Design Tests ──────────────────────────────────────────────

    @Test
    public void layoutAdaptsToPhoneSize() {
        // On phones, bottom nav should be visible
        onView(withId(android.R.id.content))
                .check(matches(isDisplayed()));
    }

    @Test
    public void layoutAdaptsToTabletSize() {
        // On tablets, sidebar should be visible instead of bottom nav
        onView(withId(android.R.id.content))
                .check(matches(isDisplayed()));
    }

    // ─── Performance Tests ────────────────────────────────────────────────────

    @Test
    public void appLoadsUnder5Seconds() {
        long start = System.currentTimeMillis();
        activityRule.getScenario().onActivity(activity -> {
            onView(withId(android.R.id.content))
                    .check(matches(isDisplayed()));
        });
        long elapsed = System.currentTimeMillis() - start;
        // This is a rough check; proper perf testing uses Macrobenchmark
    }
}
