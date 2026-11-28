package com.jarvisos.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

/**
 * Which clips are worth sending, and under what name.
 *
 * The rules are here rather than in the collector because they decide whether a
 * child's voice note is read at all: a type Whisper cannot decode is not worth
 * a 6 MiB upload, and a name with no extension makes the transcriber guess.
 */
public class AudioMessageScanTest {

    @Test
    public void knowsTheTypesWhisperCanRead() {
        assertTrue(AudioMessageScan.isSupported("audio/amr", null));
        assertTrue(AudioMessageScan.isSupported("audio/3gpp", null));
        assertTrue(AudioMessageScan.isSupported("audio/mp4", null));
        assertTrue(AudioMessageScan.isSupported("audio/ogg", null));
        assertTrue(AudioMessageScan.isSupported("application/ogg", null));
        assertTrue(AudioMessageScan.isSupported("audio/opus", null));
        assertTrue(AudioMessageScan.isSupported("audio/mpeg", null));
        assertTrue(AudioMessageScan.isSupported("audio/aac", null));
        assertTrue(AudioMessageScan.isSupported("audio/wav", null));
        assertTrue(AudioMessageScan.isSupported("audio/webm", null));
        // A parameter on the type is still that type.
        assertTrue(AudioMessageScan.isSupported("audio/amr; codecs=se", null));
    }

    @Test
    public void knowsTheExtensionsToo() {
        // Some apps put only a file name on the notification.
        for (String name : new String[] {"voice.amr", "voice.3gp", "voice.m4a", "voice.ogg",
                "voice.opus", "voice.mp3", "voice.aac", "voice.wav", "voice.webm"}) {
            assertTrue(name, AudioMessageScan.isSupported("", name));
        }
        assertTrue(AudioMessageScan.isSupported("", "/storage/emulated/0/Download/note.OGG"));
    }

    @Test
    public void leavesEverythingElseAlone() {
        assertFalse(AudioMessageScan.isSupported("image/jpeg", "photo.jpg"));
        assertFalse(AudioMessageScan.isSupported("video/mp4", "clip.mp4"));
        assertFalse(AudioMessageScan.isSupported("application/pdf", "doc.pdf"));
        assertFalse(AudioMessageScan.isSupported(null, null));
        assertFalse(AudioMessageScan.isSupported("", ""));
        // A text message's type must not be mistaken for audio.
        assertFalse(AudioMessageScan.isSupported("text/plain", "message.txt"));
    }

    @Test
    public void namesAClipSoTheDecoderIsObvious() {
        // A name the app gave is kept.
        assertEquals("voice.m4a", AudioMessageScan.fileName("voice.m4a", "audio/mp4"));
        // No name at all: the type decides the extension.
        assertEquals("voice-note.amr", AudioMessageScan.fileName(null, "audio/amr"));
        assertEquals("voice-note.ogg", AudioMessageScan.fileName("", "audio/ogg"));
        // A name without an extension gets the type's, never a bare container.
        assertEquals("note.mp3", AudioMessageScan.fileName("note", "audio/mpeg"));
        // An unknown type is Android's own recording format, not .wav.
        assertEquals("voice-note.m4a", AudioMessageScan.fileName(null, "audio/unknown"));
    }

    @Test
    public void aRefusalIsSettledAndATryLaterIsNot() {
        assertTrue(AudioMessageScan.settled(200));
        assertTrue(AudioMessageScan.settled(204));
        assertTrue("the server will never take it", AudioMessageScan.settled(422));
        assertTrue(AudioMessageScan.settled(403));
        assertFalse("rate limited: try again", AudioMessageScan.settled(429));
        assertFalse("the transcriber is busy", AudioMessageScan.settled(503));
        assertFalse("no connection", AudioMessageScan.settled(0));
        assertFalse(AudioMessageScan.settled(500));
    }

    @Test
    public void theBaseTypeIgnoresCaseAndParameters() {
        assertEquals("audio/amr", AudioMessageScan.baseType(" Audio/AMR ; codecs=se"));
        assertEquals("", AudioMessageScan.baseType(null));
    }
}
