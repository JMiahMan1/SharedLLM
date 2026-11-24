package com.jarvisos.app;

import android.text.InputType;

/**
 * Which pictures the keyboard may insert into a chat box, and how big.
 *
 * Plain Java with no Android calls beyond constants, so the rules have unit
 * tests (KeyboardMediaTest). The WebView that shows Jarvis says nothing about
 * pictures when a text box is focused, so Gboard (Tenor GIFs) and the Samsung
 * Keyboard (GIPHY GIFs and stickers) answered "This app doesn't support GIF
 * insertion here" in every chat. {@link JarvisWebView} now names these types
 * for the boxes a message is written in, and {@link KeyboardMediaPlugin}
 * hands what the keyboard sends to the page as a pasted file.
 */
final class KeyboardMedia {
    private KeyboardMedia() {}

    /** In order of preference: an animated GIF stays a GIF when the keyboard offers it in several forms. */
    static final String[] MIME_TYPES = {"image/gif", "image/webp", "image/png", "image/jpeg"};

    /**
     * The most the page is handed. It travels to the page as base64 over the
     * Capacitor bridge, so it is kept far below the private chat's 25 MB
     * picture limit; a keyboard GIF is a few MB at most.
     */
    static final int MAX_BYTES = 15 * 1024 * 1024;

    /**
     * Whether a focused box is one a message is written in: a multi-line text
     * box (every chat composer is a textarea). A sign-in field, a password or
     * an address bar never offers GIFs.
     */
    static boolean acceptsMedia(int inputType) {
        if ((inputType & InputType.TYPE_MASK_CLASS) != InputType.TYPE_CLASS_TEXT) return false;
        int variation = inputType & InputType.TYPE_MASK_VARIATION;
        if (variation == InputType.TYPE_TEXT_VARIATION_PASSWORD
                || variation == InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD
                || variation == InputType.TYPE_TEXT_VARIATION_WEB_PASSWORD) {
            return false;
        }
        return (inputType & InputType.TYPE_TEXT_FLAG_MULTI_LINE) != 0;
    }

    /** The first type of ours the keyboard offers, or null when it offers none of them. */
    static String choose(String[] offered) {
        if (offered == null) return null;
        for (String wanted : MIME_TYPES) {
            for (String type : offered) {
                if (type != null && wanted.equalsIgnoreCase(type.trim())) return wanted;
            }
        }
        return null;
    }

    /** A file name for the page (it shows nowhere in a private chat; a family chat uploads it). */
    static String fileName(String mime) {
        switch (mime) {
            case "image/gif": return "keyboard.gif";
            case "image/webp": return "keyboard.webp";
            case "image/png": return "keyboard.png";
            default: return "keyboard.jpg";
        }
    }
}
