package com.jarvisos.app;

import android.media.AudioAttributes;
import android.media.AudioFormat;
import android.media.AudioTrack;
import android.os.Build;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.SystemClock;
import android.util.Log;

import org.json.JSONException;
import org.json.JSONObject;

import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;

import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import okhttp3.WebSocket;
import okhttp3.WebSocketListener;
import okio.ByteString;

/**
 * The phone's Music Assistant player, spoken natively once the app is swiped
 * away (P8-T2).
 *
 * The web player lives in the WebView, which dies with the app's task. When
 * that happens mid-song, {@link MediaPlaybackService} starts this player under
 * the same Sendspin player id, through the same gateway socket the WebView
 * used, and asks Music Assistant to carry on. It requests raw PCM so audio
 * plays straight into an {@link AudioTrack}; each chunk is written when the
 * server's timestamp says it is due. Opening the app again hands playback
 * back to the web player.
 */
final class NativeSendspinPlayer {
    private static final String TAG = "NativeSendspin";
    private static final long TIME_BURST_INTERVAL_MS = 10_000;
    private static final int TIME_BURST_SIZE = 8;
    private static final long STATE_INTERVAL_MS = 5_000;

    interface Listener {
        /** Music Assistant reported what is playing. */
        void onMetadata(String title, String artist);

        /** The group started or stopped playing. */
        void onPlaying(boolean playing);

        /** The connection ended for good (closed, refused or failed). */
        void onClosed(String reason);
    }

    private final String socketUrl;
    private final String playerId;
    private final String name;
    private final Listener listener;
    private final OkHttpClient client = new OkHttpClient.Builder()
            .pingInterval(20, TimeUnit.SECONDS)
            .readTimeout(0, TimeUnit.MILLISECONDS)
            .build();
    private final HandlerThread controlThread = new HandlerThread("sendspin-control");
    private final Handler control;
    private final SendspinProtocol.ClockSync clock = new SendspinProtocol.ClockSync();
    private final LinkedBlockingQueue<Tagged> chunks = new LinkedBlockingQueue<>();

    private volatile WebSocket socket;
    private volatile boolean closed;
    private volatile int volume;
    private volatile boolean muted;
    private volatile SendspinProtocol.Format format;
    /** Bumped by stream/start, stream/clear and stream/end: older chunks are dropped. */
    private volatile int generation;
    private volatile boolean resumeWanted;
    private Thread writer;
    private AudioTrack track;
    private int burstLeft;
    private long probeSentUs = -1;

    NativeSendspinPlayer(String socketUrl, String playerId, String name, int volume, boolean resume, Listener listener) {
        this.socketUrl = socketUrl;
        this.playerId = playerId;
        this.name = name;
        this.volume = volume;
        this.resumeWanted = resume;
        this.listener = listener;
        controlThread.start();
        control = new Handler(controlThread.getLooper());
    }

    /** This device's monotonic clock in microseconds (the clock the server's offset is measured against). */
    private static long nowUs() {
        return SystemClock.elapsedRealtimeNanos() / 1000;
    }

    void connect() {
        Request request = new Request.Builder().url(socketUrl).build();
        socket = client.newWebSocket(request, new WebSocketListener() {
            @Override
            public void onOpen(WebSocket ws, Response response) {
                send(() -> SendspinProtocol.hello(playerId, name, Build.MANUFACTURER, Build.VERSION.RELEASE));
            }

            @Override
            public void onMessage(WebSocket ws, String text) {
                control.post(() -> handleText(text));
            }

            @Override
            public void onMessage(WebSocket ws, ByteString bytes) {
                SendspinProtocol.Chunk chunk = SendspinProtocol.parseChunk(bytes.toByteArray());
                if (chunk != null && format != null) chunks.offer(new Tagged(chunk, generation));
            }

            @Override
            public void onClosed(WebSocket ws, int code, String reason) {
                finish("closed " + code);
            }

            @Override
            public void onFailure(WebSocket ws, Throwable t, Response response) {
                finish("failed: " + t.getMessage());
            }
        });
    }

    /** Ask Music Assistant for play, pause, next, previous or stop. */
    void command(String command) {
        send(() -> SendspinProtocol.command(command));
    }

    /** Say goodbye and release everything. */
    void close() {
        if (closed) return;
        send(() -> SendspinProtocol.goodbye("restart"));
        WebSocket ws = socket;
        if (ws != null) ws.close(1000, "handed back");
        finish("handed back");
    }

    boolean isPlaying() {
        return format != null && !closed;
    }

    private interface Message {
        JSONObject build() throws JSONException;
    }

    private void send(Message message) {
        WebSocket ws = socket;
        if (ws == null || closed) return;
        try {
            ws.send(message.build().toString());
        } catch (JSONException e) {
            Log.w(TAG, "could not build a message: " + e.getMessage());
        }
    }

    private void handleText(String text) {
        JSONObject msg;
        try {
            msg = new JSONObject(text);
        } catch (JSONException e) {
            return;
        }
        JSONObject payload = msg.optJSONObject("payload");
        switch (msg.optString("type")) {
            case "server/hello":
                sendState();
                startTimeSync();
                control.postDelayed(this::stateTick, STATE_INTERVAL_MS);
                // Music Assistant lists a new player a moment after the
                // handshake; ask it to carry on once it has.
                if (resumeWanted) control.postDelayed(this::resumeIfQuiet, 2_500);
                break;
            case "server/time":
                onServerTime(payload);
                break;
            case "stream/start":
                startStream(SendspinProtocol.Format.from(payload));
                break;
            case "stream/clear":
                generation++;
                chunks.clear();
                AudioTrack t = track;
                if (t != null) {
                    t.pause();
                    t.flush();
                    t.play();
                }
                break;
            case "stream/end":
                endStream();
                break;
            case "server/command":
                onServerCommand(payload == null ? null : payload.optJSONObject("player"));
                break;
            case "server/state":
                JSONObject metadata = payload == null ? null : payload.optJSONObject("metadata");
                if (metadata != null && metadata.has("title")) {
                    listener.onMetadata(metadata.optString("title", ""), metadata.optString("artist", ""));
                }
                break;
            case "group/update":
                String state = payload == null ? "" : payload.optString("playback_state", "");
                if (!state.isEmpty()) listener.onPlaying("playing".equals(state));
                break;
            default:
                break;
        }
    }

    /** Resume the queue the web player was playing, tried twice in case the first came too early. */
    private void resumeIfQuiet() {
        if (closed || format != null || !resumeWanted) return;
        command("play");
        control.postDelayed(() -> {
            if (!closed && format == null && resumeWanted) command("play");
            resumeWanted = false;
        }, 3_000);
    }

    private void stateTick() {
        if (closed) return;
        sendState();
        control.postDelayed(this::stateTick, STATE_INTERVAL_MS);
    }

    private void sendState() {
        send(() -> SendspinProtocol.state(true, volume, muted));
    }

    private void startTimeSync() {
        if (closed) return;
        clock.newBurst();
        burstLeft = TIME_BURST_SIZE;
        probe();
        control.postDelayed(this::startTimeSync, TIME_BURST_INTERVAL_MS);
    }

    private void probe() {
        if (closed || burstLeft <= 0) return;
        burstLeft--;
        probeSentUs = nowUs();
        long sent = probeSentUs;
        send(() -> SendspinProtocol.time(sent));
    }

    private void onServerTime(JSONObject payload) {
        if (payload == null) return;
        long t4 = nowUs();
        long t1 = payload.optLong("client_transmitted", -1);
        if (t1 != probeSentUs) return;
        clock.add(t1, payload.optLong("server_received"), payload.optLong("server_transmitted"), t4);
        probe();
    }

    private void onServerCommand(JSONObject player) {
        if (player == null) return;
        String command = player.optString("command");
        if ("volume".equals(command) && player.has("volume")) {
            volume = player.optInt("volume", volume);
        } else if ("mute".equals(command) && player.has("mute")) {
            muted = player.optBoolean("mute", muted);
        }
        applyVolume();
        sendState();
    }

    private void applyVolume() {
        AudioTrack t = track;
        if (t != null) t.setVolume(muted ? 0f : Math.max(0, Math.min(100, volume)) / 100f);
    }

    private void startStream(SendspinProtocol.Format next) {
        if (next == null || !next.playable()) {
            Log.w(TAG, "unplayable stream format: " + (next == null ? "none" : next.codec));
            send(() -> SendspinProtocol.state(false, volume, muted));
            return;
        }
        SendspinProtocol.Format current = format;
        boolean sameLayout = current != null && current.sampleRate == next.sampleRate && current.channels == next.channels;
        format = next;
        resumeWanted = false;
        if (sameLayout && track != null) return;
        releaseTrack();
        generation++;
        chunks.clear();
        int channelMask = next.channels == 1 ? AudioFormat.CHANNEL_OUT_MONO : AudioFormat.CHANNEL_OUT_STEREO;
        int minBuffer = AudioTrack.getMinBufferSize(next.sampleRate, channelMask, AudioFormat.ENCODING_PCM_16BIT);
        track = new AudioTrack.Builder()
                .setAudioAttributes(new AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_MEDIA)
                        .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                        .build())
                .setAudioFormat(new AudioFormat.Builder()
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .setSampleRate(next.sampleRate)
                        .setChannelMask(channelMask)
                        .build())
                .setBufferSizeInBytes(Math.max(minBuffer * 2, next.bytesPerSecond() / 2))
                .setTransferMode(AudioTrack.MODE_STREAM)
                .build();
        applyVolume();
        track.play();
        startWriter();
        listener.onPlaying(true);
    }

    private void endStream() {
        generation++;
        chunks.clear();
        format = null;
        releaseTrack();
        listener.onPlaying(false);
        sendState();
    }

    /** A chunk tagged with the stream generation it arrived in. */
    private static final class Tagged {
        final SendspinProtocol.Chunk chunk;
        final int generation;

        Tagged(SendspinProtocol.Chunk chunk, int generation) {
            this.chunk = chunk;
            this.generation = generation;
        }
    }

    private void startWriter() {
        if (writer != null) return;
        writer = new Thread(() -> {
            int waitedFor = -1;
            while (!closed) {
                Tagged tagged;
                try {
                    tagged = chunks.poll(500, TimeUnit.MILLISECONDS);
                } catch (InterruptedException e) {
                    return;
                }
                AudioTrack t = track;
                if (tagged == null || t == null) continue;
                SendspinProtocol.Chunk next = tagged.chunk;
                int gen = tagged.generation;
                if (gen != generation) continue;
                // After a start or a seek, hold the first chunk until it is due;
                // from then on the blocking write keeps pace by itself.
                if (gen != waitedFor && clock.synced()) {
                    long waitUs = clock.toLocalUs(next.serverTimeUs) - nowUs();
                    if (waitUs < -SendspinProtocol.REQUIRED_LEAD_TIME_MS * 1000L) continue; // already late: skip ahead
                    if (waitUs > 0) SystemClock.sleep(Math.min(waitUs / 1000, 5_000));
                    waitedFor = gen;
                }
                try {
                    t.write(next.data, next.offset, next.length());
                } catch (IllegalStateException e) {
                    // The track was released under us (stream end / close).
                }
            }
        }, "sendspin-audio");
        writer.start();
    }

    private void releaseTrack() {
        AudioTrack t = track;
        track = null;
        if (t == null) return;
        try {
            t.pause();
            t.flush();
            t.release();
        } catch (IllegalStateException e) {
            // Already released.
        }
    }

    private synchronized void finish(String reason) {
        if (closed) return;
        closed = true;
        format = null;
        chunks.clear();
        releaseTrack();
        Thread w = writer;
        if (w != null) w.interrupt();
        control.removeCallbacksAndMessages(null);
        controlThread.quitSafely();
        client.dispatcher().executorService().shutdown();
        Log.i(TAG, "native player ended: " + reason);
        listener.onClosed(reason);
    }
}
