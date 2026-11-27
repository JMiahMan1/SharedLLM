package com.jarvisos.app;

import java.io.ByteArrayOutputStream;

/**
 * PCM16 mono and the 44-byte WAV header around it: what the phone records, what
 * it uploads, and what it plays back.
 *
 * <p>Whisper wants a container, the audio server answers with one, and
 * {@link android.media.AudioTrack} wants the raw frames. All three conversions
 * are here so no byte is guessed at in the service, and every one is JVM-tested.
 */
public final class VoiceWav {

    /** "RIFF" + size + "WAVE" + "fmt " (16 bytes) + "data" + size. */
    public static final int HEADER_BYTES = 44;

    private VoiceWav() {}

    /** A complete WAV for mono 16-bit frames. */
    public static byte[] wav(byte[] pcm, int rate) {
        byte[] out = new byte[HEADER_BYTES + (pcm == null ? 0 : pcm.length)];
        writeHeader(out, pcm == null ? 0 : pcm.length, rate);
        if (pcm != null && pcm.length > 0) System.arraycopy(pcm, 0, out, HEADER_BYTES, pcm.length);
        return out;
    }

    private static void writeHeader(byte[] out, int dataBytes, int rate) {
        int channels = 1;
        int bits = 16;
        int byteRate = rate * channels * bits / 8;
        putAscii(out, 0, "RIFF");
        putInt(out, 4, 36 + dataBytes);
        putAscii(out, 8, "WAVE");
        putAscii(out, 12, "fmt ");
        putInt(out, 16, 16);              // PCM chunk size
        putShort(out, 20, 1);             // PCM, uncompressed
        putShort(out, 22, channels);
        putInt(out, 24, rate);
        putInt(out, 28, byteRate);
        putShort(out, 32, channels * bits / 8);  // block align
        putShort(out, 34, bits);
        putAscii(out, 36, "data");
        putInt(out, 40, dataBytes);
    }

    /**
     * The PCM inside a WAV, found by walking the chunks rather than assuming the
     * header is 44 bytes: the audio server and Whisper both add chunks of their
     * own, and trimming 44 blindly plays a click or loses the first frame.
     */
    public static byte[] pcm(byte[] wav) {
        if (wav == null || wav.length < 12) return new byte[0];
        if (!"RIFF".equals(ascii(wav, 0, 4)) || !"WAVE".equals(ascii(wav, 8, 4))) return wav;
        int at = 12;
        while (at + 8 <= wav.length) {
            String id = ascii(wav, at, 4);
            int size = readInt(wav, at + 4);
            int body = at + 8;
            if (size < 0 || body > wav.length) break;
            if ("data".equals(id)) {
                int end = Math.min(wav.length, body + size);
                byte[] out = new byte[Math.max(0, end - body)];
                System.arraycopy(wav, body, out, 0, out.length);
                return out;
            }
            at = body + size + (size % 2);  // chunks are word-aligned
        }
        return new byte[0];
    }

    /** The sample rate a WAV declares, or 0 when it does not say. */
    public static int rate(byte[] wav) {
        if (wav == null || wav.length < 28) return 0;
        if (!"RIFF".equals(ascii(wav, 0, 4))) return 0;
        int at = 12;
        while (at + 8 <= wav.length) {
            String id = ascii(wav, at, 4);
            int size = readInt(wav, at + 4);
            if ("fmt ".equals(id) && at + 16 <= wav.length) return readInt(wav, at + 12);
            at = at + 8 + size + (size % 2);
        }
        return 0;
    }

    /** Loudness of one stretch of PCM16 frames, the number the turn rules judge. */
    public static int rms(byte[] pcm, int offset, int length) {
        if (pcm == null) return 0;
        int start = Math.max(0, offset);
        int end = Math.min(pcm.length, start + Math.max(0, length));
        long sum = 0;
        int samples = 0;
        for (int i = start; i + 1 < end; i += 2) {
            int sample = (short) ((pcm[i] & 0xFF) | (pcm[i + 1] << 8));
            sum += (long) sample * sample;
            samples++;
        }
        if (samples == 0) return 0;
        return (int) Math.sqrt((double) sum / samples);
    }

    /** The frames between two times, clamped to what was recorded. */
    public static byte[] slice(byte[] pcm, int fromMs, int toMs, int rate) {
        if (pcm == null) return new byte[0];
        int bytesPerMs = rate * 2 / 1000;
        int start = Math.max(0, Math.min(pcm.length, fromMs * bytesPerMs));
        int end = Math.max(start, Math.min(pcm.length, toMs * bytesPerMs));
        byte[] out = new byte[end - start];
        System.arraycopy(pcm, start, out, 0, out.length);
        return out;
    }

    /** Joined PCM, for the frames of a turn that span several reads. */
    public static byte[] join(byte[] first, byte[] second) {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        if (first != null) out.write(first, 0, first.length);
        if (second != null) out.write(second, 0, second.length);
        return out.toByteArray();
    }

    private static String ascii(byte[] b, int at, int len) {
        if (b == null || at < 0 || at + len > b.length) return "";
        StringBuilder sb = new StringBuilder(len);
        for (int i = 0; i < len; i++) sb.append((char) (b[at + i] & 0xFF));
        return sb.toString();
    }

    private static void putAscii(byte[] out, int at, String value) {
        for (int i = 0; i < value.length(); i++) out[at + i] = (byte) value.charAt(i);
    }

    private static void putInt(byte[] out, int at, int value) {
        out[at] = (byte) (value & 0xFF);
        out[at + 1] = (byte) ((value >> 8) & 0xFF);
        out[at + 2] = (byte) ((value >> 16) & 0xFF);
        out[at + 3] = (byte) ((value >> 24) & 0xFF);
    }

    private static void putShort(byte[] out, int at, int value) {
        out[at] = (byte) (value & 0xFF);
        out[at + 1] = (byte) ((value >> 8) & 0xFF);
    }

    private static int readInt(byte[] b, int at) {
        if (b == null || at < 0 || at + 4 > b.length) return 0;
        return (b[at] & 0xFF) | ((b[at + 1] & 0xFF) << 8) | ((b[at + 2] & 0xFF) << 16) | ((b[at + 3] & 0xFF) << 24);
    }
}
