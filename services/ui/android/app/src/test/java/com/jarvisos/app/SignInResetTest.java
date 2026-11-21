package com.jarvisos.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotEquals;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

/**
 * The rules behind forgetting a key the server reset: which answers count,
 * how a rejected key is recognised without keeping it, and that the notice
 * goes out once per rejected key.
 */
public class SignInResetTest {

    @Test
    public void onlyA401IsARejection() {
        assertTrue(SignInReset.isRejection(401));
        assertFalse(SignInReset.isRejection(403));
        assertFalse(SignInReset.isRejection(200));
        assertFalse(SignInReset.isRejection(404));
        assertFalse(SignInReset.isRejection(500));
        assertFalse(SignInReset.isRejection(503));
        assertFalse(SignInReset.isRejection(0));
    }

    @Test
    public void theFingerprintIsAStableHashNotTheKey() {
        String fp = SignInReset.fingerprint("example-key");
        assertEquals(64, fp.length());
        assertTrue(fp.matches("[0-9a-f]{64}"));
        assertFalse(fp.contains("example-key"));
        assertEquals(fp, SignInReset.fingerprint("example-key"));
        assertNotEquals(fp, SignInReset.fingerprint("another-key"));
        // The standard SHA-256 test vector, so the hex form is pinned.
        assertEquals("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
                SignInReset.fingerprint("abc"));
    }

    @Test
    public void noKeyHasNoFingerprint() {
        assertNull(SignInReset.fingerprint(null));
        assertNull(SignInReset.fingerprint(""));
    }

    @Test
    public void theNoticeGoesOncePerRejectedKey() {
        String first = SignInReset.fingerprint("first-key");
        String second = SignInReset.fingerprint("second-key");
        // Nothing announced yet.
        assertTrue(SignInReset.shouldNotify(null, first));
        // The same key refused again (say after the app restored it): no second notice.
        assertFalse(SignInReset.shouldNotify(first, first));
        // A key from a later sign-in that is reset in turn gets its own notice.
        assertTrue(SignInReset.shouldNotify(first, second));
        assertFalse(SignInReset.shouldNotify(first, null));
    }
}
