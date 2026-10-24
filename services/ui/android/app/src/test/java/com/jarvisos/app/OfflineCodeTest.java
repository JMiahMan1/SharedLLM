package com.jarvisos.app;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class OfflineCodeTest {

    // Python: hashlib.pbkdf2_hmac('sha256', b'48291735', bytes.fromhex(SALT), 1000).hex()
    private static final String SALT = "00112233445566778899aabbccddeeff";
    private static final String HASH = "31162af95465801a18d499fffe7b447bd59602480d40e629c3a1e1afded25335";

    @Test
    public void theRightCodeMatchesTheServersVerifier() {
        assertTrue(OfflineCode.matches("48291735", SALT, 1000, HASH));
    }

    @Test
    public void aWrongCodeDoesNot() {
        assertFalse(OfflineCode.matches("48291736", SALT, 1000, HASH));
        assertFalse(OfflineCode.matches("", SALT, 1000, HASH));
    }

    @Test
    public void aBrokenVerifierNeverMatches() {
        assertFalse(OfflineCode.matches("48291735", "zz", 1000, HASH));
        assertFalse(OfflineCode.matches("48291735", SALT, 0, HASH));
        assertFalse(OfflineCode.matches("48291735", SALT, 1000, ""));
    }
}
