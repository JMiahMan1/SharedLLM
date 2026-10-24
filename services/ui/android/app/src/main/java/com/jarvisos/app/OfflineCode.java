package com.jarvisos.app;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/**
 * A parent's offline unlock code, checked on the child's phone with no
 * connection. The phone holds only PBKDF2-SHA256 verifiers (salt, iterations,
 * hash), received inside a real online parent unlock; the code itself never
 * leaves the parent. PBKDF2 is done here with HmacSHA256 because the
 * PBKDF2WithHmacSHA256 factory needs Android 8 and Jarvis runs on 7.
 */
final class OfflineCode {

    private OfflineCode() {}

    /** True when ``code`` matches the verifier. Malformed verifiers never match. */
    static boolean matches(String code, String saltHex, int iterations, String hashHex) {
        if (code == null || code.isEmpty() || iterations < 1 || iterations > 2_000_000) return false;
        try {
            byte[] expected = fromHex(hashHex);
            byte[] actual = pbkdf2(code.getBytes(StandardCharsets.UTF_8), fromHex(saltHex), iterations, expected.length);
            return expected.length > 0 && MessageDigest.isEqual(expected, actual);
        } catch (Exception e) {
            return false;
        }
    }

    static byte[] pbkdf2(byte[] password, byte[] salt, int iterations, int length) throws Exception {
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(password, "HmacSHA256"));
        byte[] out = new byte[length];
        int blocks = (length + 31) / 32;
        for (int block = 1; block <= blocks; block++) {
            mac.update(salt);
            mac.update(new byte[] {(byte) (block >>> 24), (byte) (block >>> 16), (byte) (block >>> 8), (byte) block});
            byte[] u = mac.doFinal();
            byte[] t = u.clone();
            for (int i = 1; i < iterations; i++) {
                u = mac.doFinal(u);
                for (int j = 0; j < t.length; j++) t[j] ^= u[j];
            }
            System.arraycopy(t, 0, out, (block - 1) * 32, Math.min(32, length - (block - 1) * 32));
        }
        return out;
    }

    static byte[] fromHex(String hex) {
        if (hex == null || hex.length() % 2 != 0) throw new IllegalArgumentException("hex");
        byte[] out = new byte[hex.length() / 2];
        for (int i = 0; i < out.length; i++) {
            out[i] = (byte) Integer.parseInt(hex.substring(i * 2, i * 2 + 2), 16);
        }
        return out;
    }
}
