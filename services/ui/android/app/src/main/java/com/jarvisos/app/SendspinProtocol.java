package com.jarvisos.app;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

/**
 * The Sendspin messages the native player needs (the protocol the web player
 * speaks through sendspin-js), kept free of Android types so they can be unit
 * tested on the JVM.
 *
 * The native player asks for raw PCM only, so audio needs no decoding: each
 * binary message is one type byte (4 = player audio), an 8-byte big-endian
 * server timestamp in microseconds, then interleaved 16-bit samples.
 */
final class SendspinProtocol {
    static final int AUDIO_CHUNK = 4;
    static final int REQUIRED_LEAD_TIME_MS = 250;
    static final int MIN_BUFFER_MS = 250;
    static final int BUFFER_CAPACITY = 5 * 1024 * 1024;

    private SendspinProtocol() {}

    /** client/hello for the same player id the web player announced. */
    static JSONObject hello(String clientId, String name, String manufacturer, String softwareVersion) throws JSONException {
        JSONArray formats = new JSONArray();
        for (int rate : new int[] {48000, 44100}) {
            formats.put(new JSONObject()
                    .put("codec", "pcm")
                    .put("sample_rate", rate)
                    .put("channels", 2)
                    .put("bit_depth", 16));
        }
        JSONObject player = new JSONObject()
                .put("supported_formats", formats)
                .put("buffer_capacity", BUFFER_CAPACITY)
                .put("supported_commands", new JSONArray().put("volume").put("mute"));
        JSONObject payload = new JSONObject()
                .put("client_id", clientId)
                .put("name", name)
                .put("version", 1)
                .put("supported_roles", new JSONArray().put("player@v1").put("controller@v1").put("metadata@v1"))
                .put("device_info", new JSONObject()
                        .put("product_name", "Jarvis Android")
                        .put("manufacturer", manufacturer)
                        .put("software_version", softwareVersion))
                .put("player@v1_support", player);
        return message("client/hello", payload);
    }

    /** client/state: what this player is doing and its volume. */
    static JSONObject state(boolean healthy, int volume, boolean muted) throws JSONException {
        JSONObject player = new JSONObject()
                .put("state", healthy ? "synchronized" : "error")
                .put("volume", volume)
                .put("muted", muted)
                .put("static_delay_ms", 0)
                .put("required_lead_time_ms", REQUIRED_LEAD_TIME_MS)
                .put("min_buffer_ms", MIN_BUFFER_MS)
                .put("supported_commands", new JSONArray().put("set_static_delay"));
        return message("client/state", new JSONObject().put("player", player));
    }

    /** client/time: one clock probe, stamped with this device's clock. */
    static JSONObject time(long clientTransmittedUs) throws JSONException {
        return message("client/time", new JSONObject().put("client_transmitted", clientTransmittedUs));
    }

    /** client/command as a controller: play, pause, next, previous, stop. */
    static JSONObject command(String command) throws JSONException {
        return message("client/command", new JSONObject().put("controller", new JSONObject().put("command", command)));
    }

    static JSONObject goodbye(String reason) throws JSONException {
        return message("client/goodbye", new JSONObject().put("reason", reason));
    }

    private static JSONObject message(String type, JSONObject payload) throws JSONException {
        return new JSONObject().put("type", type).put("payload", payload);
    }

    /** The PCM layout announced by stream/start. */
    static final class Format {
        final String codec;
        final int sampleRate;
        final int channels;
        final int bitDepth;

        Format(String codec, int sampleRate, int channels, int bitDepth) {
            this.codec = codec;
            this.sampleRate = sampleRate;
            this.channels = channels;
            this.bitDepth = bitDepth;
        }

        /** The stream/start payload's player format, or null when there is none. */
        static Format from(JSONObject streamStartPayload) {
            JSONObject p = streamStartPayload == null ? null : streamStartPayload.optJSONObject("player");
            if (p == null) return null;
            return new Format(p.optString("codec", ""), p.optInt("sample_rate", 0), p.optInt("channels", 0), p.optInt("bit_depth", 0));
        }

        /** Only 16-bit mono/stereo PCM is played natively; it is all this player asks for. */
        boolean playable() {
            return "pcm".equals(codec) && sampleRate > 0 && (channels == 1 || channels == 2) && bitDepth == 16;
        }

        int bytesPerSecond() {
            return sampleRate * channels * (bitDepth / 8);
        }
    }

    /** One audio chunk: when the server wants it heard, and where its samples start. */
    static final class Chunk {
        final long serverTimeUs;
        final byte[] data;
        final int offset;

        Chunk(long serverTimeUs, byte[] data, int offset) {
            this.serverTimeUs = serverTimeUs;
            this.data = data;
            this.offset = offset;
        }

        int length() {
            return data.length - offset;
        }
    }

    /** A binary message as an audio chunk, or null for anything else. */
    static Chunk parseChunk(byte[] message) {
        if (message == null || message.length < 9 || (message[0] & 0xFF) != AUDIO_CHUNK) return null;
        long ts = 0;
        for (int i = 1; i <= 8; i++) ts = (ts << 8) | (message[i] & 0xFFL);
        return new Chunk(ts, message, 9);
    }

    /**
     * Maps the server's clock onto this device's, NTP style, from client/time
     * probes. The probe with the shortest round trip in a burst gives the best
     * estimate, the same choice sendspin-js makes.
     */
    static final class ClockSync {
        private long bestRoundTripUs = Long.MAX_VALUE;
        private long offsetUs;
        private boolean synced;

        /** Start a new burst; the previous offset stays in use until a probe lands. */
        void newBurst() {
            bestRoundTripUs = Long.MAX_VALUE;
        }

        /**
         * One answered probe: t1 sent (device clock), serverReceived and
         * serverTransmitted (server clock), t4 received (device clock).
         */
        void add(long t1, long serverReceived, long serverTransmitted, long t4) {
            long roundTrip = (t4 - t1) - (serverTransmitted - serverReceived);
            if (roundTrip < 0 || roundTrip >= bestRoundTripUs) return;
            bestRoundTripUs = roundTrip;
            offsetUs = ((serverReceived - t1) + (serverTransmitted - t4)) / 2;
            synced = true;
        }

        boolean synced() {
            return synced;
        }

        /** A server timestamp on this device's clock. */
        long toLocalUs(long serverUs) {
            return serverUs - offsetUs;
        }
    }
}
