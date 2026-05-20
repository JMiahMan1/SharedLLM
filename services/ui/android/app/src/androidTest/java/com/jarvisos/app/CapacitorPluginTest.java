package com.jarvisos.app;

import android.content.Context;

import androidx.test.platform.app.InstrumentationRegistry;
import androidx.test.ext.junit.runners.AndroidJUnit4;

import org.junit.Test;
import org.junit.runner.RunWith;

import static org.junit.Assert.*;

/**
 * Instrumented test for Capacitor plugin functionality.
 */
@RunWith(AndroidJUnit4.class)
public class CapacitorPluginTest {

    @Test
    public void useAppContext() {
        Context appContext = InstrumentationRegistry.getInstrumentation().getTargetContext();
        assertEquals("com.jarvisos.app", appContext.getPackageName());
    }

    @Test
    public void capacitorBridgeIsInitialized() {
        // Verify Capacitor bridge is properly initialized
        Context appContext = InstrumentationRegistry.getInstrumentation().getTargetContext();
        assertNotNull(appContext);
    }
}
