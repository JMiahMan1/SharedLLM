package com.jarvisos.app;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

/** The WAV container the phone records into, uploads and plays back from. */
public class VoiceWavTest {

    @Test
    public void a_header_describes_16k_mono_16_bit() {
        byte[] wav = VoiceWav.wav(new byte[320], 16000);
        assertEquals(VoiceWav.HEADER_BYTES + 320, wav.length);
        assertEquals("RIFF", new String(wav, 0, 4, java.nio.charset.StandardCharsets.US_ASCII));
        assertEquals("WAVE", new String(wav, 8, 4, java.nio.charset.StandardCharsets.US_ASCII));
        assertEquals(16000, VoiceWav.rate(wav));
        // 36 + data, then the fmt chunk says 1 channel of 16 bits at 32000 bytes/s.
        assertEquals(36 + 320, (wav[4] & 0xFF) | ((wav[5] & 0xFF) << 8));
        assertEquals(1, (wav[22] & 0xFF) | ((wav[23] & 0xFF) << 8));
        assertEquals(32000, (wav[28] & 0xFF) | ((wav[29] & 0xFF) << 8)
                | ((wav[30] & 0xFF) << 16) | ((wav[31] & 0xFF) << 24));
    }

    @Test
    public void the_frames_come_back_out_and_a_longer_header_does_not_shift_them() {
        byte[] pcm = new byte[]{1, 2, 3, 4, 5, 6};
        assertArrayEquals(pcm, VoiceWav.pcm(VoiceWav.wav(pcm, 16000)));

        // A "LIST" chunk before the data (Whisper adds them) must be skipped, not trimmed blindly.
        byte[] wav = VoiceWav.wav(pcm, 16000);
        byte[] withList = new byte[wav.length + 12];
        System.arraycopy(wav, 0, withList, 0, 36);
        System.arraycopy("LIST".getBytes(java.nio.charset.StandardCharsets.US_ASCII), 0, withList, 36, 4);
        withList[40] = 4;  // a four-byte list body
        System.arraycopy(wav, 36, withList, 48, 8 + pcm.length);
        assertArrayEquals(pcm, VoiceWav.pcm(withList));
    }

    @Test
    public void a_bare_recording_is_passed_through_untouched() {
        byte[] raw = new byte[64];
        java.util.Arrays.fill(raw, (byte) 9);
        assertArrayEquals(raw, VoiceWav.pcm(raw));   // no RIFF header, nothing to strip
        assertArrayEquals(new byte[0], VoiceWav.pcm(new byte[]{9, 8, 7}));  // too short to be audio
        assertArrayEquals(new byte[0], VoiceWav.pcm(null));
    }

    @Test
    public void loudness_is_the_number_the_turn_rules_judge() {
        assertEquals(0, VoiceWav.rms(new byte[64], 0, 64));
        byte[] quiet = new byte[64];
        byte[] loud = new byte[64];
        for (int i = 0; i + 1 < loud.length; i += 2) {
            loud[i] = (byte) 0xD0;
            loud[i + 1] = 0x07;  // 2000
        }
        assertEquals(2000, VoiceWav.rms(loud, 0, loud.length));
        assertTrue(VoiceWav.rms(loud, 0, loud.length) > VoiceWav.rms(quiet, 0, quiet.length));
    }

    @Test
    public void a_stretch_of_the_recording_can_be_cut_out_by_time() {
        byte[] pcm = new byte[16000 * 2];  // exactly one second
        assertEquals(320, VoiceWav.slice(pcm, 100, 110, 16000).length);
        // Past the end is clamped rather than throwing.
        assertEquals(0, VoiceWav.slice(pcm, 5000, 6000, 16000).length);
        assertEquals(32000, VoiceWav.slice(pcm, 0, 9000, 16000).length);
    }

    @Test
    public void two_reads_join_into_one_turn() {
        assertArrayEquals(new byte[]{1, 2, 3, 4}, VoiceWav.join(new byte[]{1, 2}, new byte[]{3, 4}));
        assertArrayEquals(new byte[]{1}, VoiceWav.join(new byte[]{1}, null));
    }
}
