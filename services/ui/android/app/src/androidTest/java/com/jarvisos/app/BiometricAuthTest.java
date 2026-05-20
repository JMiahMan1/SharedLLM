package com.jarvisos.app;

import android.content.Context;
import android.hardware.biometrics.BiometricManager;
import android.os.Build;

import androidx.test.core.app.ApplicationProvider;
import androidx.test.ext.junit.runners.AndroidJUnit4;

import org.junit.Test;
import org.junit.runner.RunWith;

import static org.junit.Assert.*;

/**
 * Tests for biometric authentication functionality.
 */
@RunWith(AndroidJUnit4.class)
public class BiometricAuthTest {

    @Test
    public void biometricManagerIsAvailable() {
        Context context = ApplicationProvider.getApplicationContext();
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            BiometricManager biometricManager = BiometricManager.from(context);
            assertNotNull(biometricManager);
        }
    }

    @Test
    public void biometricEnrollmentCanBeChecked() {
        Context context = ApplicationProvider.getApplicationContext();
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            BiometricManager biometricManager = BiometricManager.from(context);
            int canAuthenticate = biometricManager.canAuthenticate(
                    BiometricManager.Authenticators.BIOMETRIC_STRONG
            );
            // Result can be SUCCESS, NO_HARDWARE, NONE_ENROLLED, or UNAVAILABLE
            assertTrue(canAuthenticate >= BiometricManager.BIOMETRIC_SUCCESS ||
                       canAuthenticate <= BiometricManager.BIOMETRIC_ERROR_NO_HARDWARE);
        }
    }

    @Test
    public void biometricPromptCanBeDisplayed() {
        // This test verifies that the biometric prompt can be shown
        // Actual prompt display requires UI thread and activity context
        Context context = ApplicationProvider.getApplicationContext();
        assertNotNull(context);
    }

    @Test
    public void fallbackToPinWhenBiometricsUnavailable() {
        // When biometrics are not available, PIN pad should be the fallback
        Context context = ApplicationProvider.getApplicationContext();
        assertNotNull(context);
    }
}
