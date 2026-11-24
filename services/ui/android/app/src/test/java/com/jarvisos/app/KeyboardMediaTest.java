package com.jarvisos.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class KeyboardMediaTest {
    // android.text.InputType values (the unit-test android.jar returns constants as compiled in).
    private static final int TEXT = 0x1;
    private static final int MULTI_LINE = 0x20000;
    private static final int PASSWORD = 0x80;
    private static final int WEB_PASSWORD = 0xe0;
    private static final int URI = 0x10;
    private static final int NUMBER = 0x2;

    @Test
    public void aChatBoxTakesPictures() {
        assertTrue(KeyboardMedia.acceptsMedia(TEXT | MULTI_LINE));
        // A textarea with capitalisation and auto-correct, as the WebView reports it.
        assertTrue(KeyboardMedia.acceptsMedia(0x24001 | MULTI_LINE));
    }

    @Test
    public void singleLineFieldsAndPasswordsDoNot() {
        assertFalse(KeyboardMedia.acceptsMedia(TEXT));
        assertFalse(KeyboardMedia.acceptsMedia(TEXT | URI));
        assertFalse(KeyboardMedia.acceptsMedia(TEXT | PASSWORD | MULTI_LINE));
        assertFalse(KeyboardMedia.acceptsMedia(TEXT | WEB_PASSWORD | MULTI_LINE));
        assertFalse(KeyboardMedia.acceptsMedia(NUMBER | MULTI_LINE));
        // The sign-in username field seen on the emulator (0x2000a1: web email address).
        assertFalse(KeyboardMedia.acceptsMedia(0x2000a1));
    }

    @Test
    public void aGifStaysAGif() {
        assertEquals("image/gif", KeyboardMedia.choose(new String[] {"image/png", "image/gif"}));
        assertEquals("image/webp", KeyboardMedia.choose(new String[] {"image/webp"}));
        assertEquals("image/jpeg", KeyboardMedia.choose(new String[] {" IMAGE/JPEG "}));
    }

    @Test
    public void otherTypesAreRefused() {
        assertNull(KeyboardMedia.choose(new String[] {"video/mp4", "image/webp.wasticker"}));
        assertNull(KeyboardMedia.choose(new String[] {}));
        assertNull(KeyboardMedia.choose(null));
        assertNull(KeyboardMedia.choose(new String[] {null}));
    }

    @Test
    public void namesFollowTheType() {
        assertEquals("keyboard.gif", KeyboardMedia.fileName("image/gif"));
        assertEquals("keyboard.webp", KeyboardMedia.fileName("image/webp"));
        assertEquals("keyboard.png", KeyboardMedia.fileName("image/png"));
        assertEquals("keyboard.jpg", KeyboardMedia.fileName("image/jpeg"));
    }
}
