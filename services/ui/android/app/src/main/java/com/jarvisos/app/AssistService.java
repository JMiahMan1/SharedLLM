package com.jarvisos.app;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ServiceInfo;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.os.Build;
import android.os.IBinder;
import android.os.SystemClock;
import android.util.Log;

import androidx.core.app.NotificationCompat;
import androidx.core.app.ServiceCompat;
import androidx.core.content.ContextCompat;

import java.io.ByteArrayOutputStream;
import java.util.ArrayDeque;
import java.util.Deque;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * The phone as a satellite of the house, without a satellite: it hears a turn,
 * asks the gateway the same question an Echo would ask, and speaks the answer
 * sentence by sentence in the person's own voice.
 *
 * Why a foreground service of type {@code microphone}: Android only lets an app
 * hold the microphone off-screen that way, and the recognised "one moment"
 * needs the answer to arrive while the person is still looking at the phone.
 * The microphone is closed the instant a turn ends, so nothing is recorded while
 * the house is thinking or talking - except the barging-in check, which is
 * deliberate: talking over Jarvis has to stop it.
 *
 * Everything here is measured and decided by {@link VoiceTurnRules}, which is
 * plain Java and unit-tested; this class only moves bytes.
 */
public final class AssistService extends Service {

    private static final String TAG = "AssistService";
    static final String ACTION_TURN = "com.jarvisos.app.assist.TURN";
    static final String ACTION_STOP = "com.jarvisos.app.assist.STOP";
    static final String ACTION_STOP_SPEAKING = "com.jarvisos.app.assist.STOP_SPEAKING";
    static final String EXTRA_WAKE = "wake";
    static final String EXTRA_REASON = "reason";

    static final String STATE_IDLE = "idle";
    static final String STATE_LISTENING = "listening";
    static final String STATE_THINKING = "thinking";
    static final String STATE_SPEAKING = "speaking";

    static final String EV_STATE = "state";
    static final String EV_LEVEL = "level";
    static final String EV_TRANSCRIPT = "transcript";
    static final String EV_SENTENCE = "sentence";
    static final String EV_REPLY = "reply";
    static final String EV_ERROR = "error";

    /** One listener for the WebView (through the plugin); events carry their own type. */
    interface Listener {
        void onEvent(String type, String text, int level);
    }

    private static final CopyOnWriteArrayList<Listener> LISTENERS = new CopyOnWriteArrayList<>();
    private static volatile boolean running;
    private static volatile String state = STATE_IDLE;

    static void addListener(Listener listener) {
        if (listener != null) LISTENERS.addIfAbsent(listener);
    }

    static void removeListener(Listener listener) {
        LISTENERS.remove(listener);
    }

    static boolean running() {
        return running;
    }

    static String state() {
        return state;
    }

    private static void emit(String type, String text, int level) {
        for (Listener listener : LISTENERS) {
            try {
                listener.onEvent(type, text == null ? "" : text, level);
            } catch (Exception ignored) {
                // a dead WebView must not stop the turn
            }
        }
    }

    private static void setState(String next) {
        state = next;
        emit(EV_STATE, next, 0);
    }

    private final ExecutorService work = Executors.newSingleThreadExecutor();
    private final AtomicBoolean alive = new AtomicBoolean(false);
    private final Deque<String> sentences = new ArrayDeque<>();
    private final Object sentenceLock = new Object();

    private VoiceClient client;
    private AssistPlayer player;
    private AudioRecord record;
    private Thread capture;
    private volatile boolean wakeMode;
    private volatile int floor;
    private volatile String conversation = VoiceClient.newConversation();
    private volatile int spoken;
    private volatile boolean streamDone;
    private volatile String lastReply = "";
    private volatile long firstSentenceAt;

    @Override
    public void onCreate() {
        super.onCreate();
        client = new VoiceClient(this);
        player = new AssistPlayer(this);
        channel();
        startSpeaker();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent == null ? ACTION_TURN : String.valueOf(intent.getAction());
        if (ACTION_STOP.equals(action)) {
            stopSelf();
            return START_NOT_STICKY;
        }
        if (ACTION_STOP_SPEAKING.equals(action)) {
            player.stop();
            if (!wakeMode) stopSelf();
            return START_NOT_STICKY;
        }
        wakeMode = intent != null && intent.getBooleanExtra(EXTRA_WAKE, false);
        if (!alive.get()) {
            alive.set(true);
            running = true;
            foreground();
            startCapture();
        }
        beginTurn();
        return START_NOT_STICKY;
    }

    @Override
    public void onDestroy() {
        alive.set(false);
        running = false;
        wakeMode = false;
        stopCapture();
        if (player != null) player.stop();
        work.shutdownNow();
        setState(STATE_IDLE);
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    // ---- the turn -----------------------------------------------------------

    private void beginTurn() {
        if (!alive.get()) return;
        synchronized (sentenceLock) {
            sentences.clear();
        }
        streamDone = false;
        lastReply = "";
        firstSentenceAt = 0;
        spoken = 0;
        setState(STATE_LISTENING);
    }

    /** A finished recording: words, then the house, then the answer as it is written. */
    private void finishTurn(byte[] pcm) {
        setState(STATE_THINKING);
        byte[] wav = VoiceWav.wav(pcm, VoiceTurnRules.RATE);
        work.execute(() -> {
            String text;
            try {
                text = client.listen(wav);
            } catch (Exception e) {
                emit(EV_ERROR, VoiceTurnRules.ack(VoiceTurnRules.Ack.OFFLINE), 0);
                backToIdle();
                return;
            }
            if (text.isEmpty()) {
                say(VoiceTurnRules.ack(VoiceTurnRules.Ack.EMPTY));
                backToIdle();
                return;
            }
            emit(EV_TRANSCRIPT, text, 0);
            if (VoiceTurnRules.stopPhrase(text)) {
                player.stop();
                emit(EV_REPLY, "Stopped.", 0);
                backToIdle();
                return;
            }
            if (wakeMode && !VoiceTurnRules.wake(text, wakePhrase())) {
                // Nothing was asked; keep the microphone open and wait again.
                beginTurn();
                return;
            }
            ask(VoiceTurnRules.withoutWake(text, wakePhrase()));
        });
    }

    private void ask(String question) {
        firstSentenceAt = 0;
        // Never silence: if the house has not started answering in a moment, say so.
        final boolean[] heard = {false};
        work.execute(() -> {
            try {
                Thread.sleep(900);
            } catch (InterruptedException e) {
                return;
            }
            if (firstSentenceAt == 0) {
                heard[0] = true;
                say(VoiceTurnRules.ack(VoiceTurnRules.Ack.THINKING));
            }
        });
        work.execute(() -> client.chat(question, conversation, new VoiceClient.Stream() {
            @Override
            public void sentence(String said) {
                if (firstSentenceAt == 0) firstSentenceAt = SystemClock.elapsedRealtime();
                queue(said);
            }

            @Override
            public void done(String reply) {
                lastReply = reply == null ? "" : reply;
                emit(EV_REPLY, lastReply, 0);
                streamDone = true;
                synchronized (sentenceLock) {
                    sentenceLock.notifyAll();
                }
            }

            @Override
            public void failed(String reason) {
                if (VoiceClient.signInProblem(reason)) {
                    emit(EV_ERROR, "Sign in to Jarvis again on this phone.", 0);
                } else if (firstSentenceAt == 0) {
                    emit(EV_ERROR, VoiceTurnRules.ack(VoiceTurnRules.Ack.NO_ANSWER), 0);
                }
                streamDone = true;
                synchronized (sentenceLock) {
                    sentenceLock.notifyAll();
                }
            }
        }));
    }

    /** One sentence ready to say; the speaker thread turns it into audio and plays it. */
    private void queue(String said) {
        if (said == null || said.trim().isEmpty()) return;
        synchronized (sentenceLock) {
            sentences.addLast(said.trim());
            sentenceLock.notifyAll();
        }
    }

    private void say(String line) {
        if (line == null || line.trim().isEmpty()) return;
        queue(line);
    }

    /**
     * Speaks queued sentences in order. Every line is fetched while the previous
     * one is playing, so the pause between sentences is the audio's own, not the
     * network's.
     */
    private void startSpeaker() {
        Thread speaker = new Thread(() -> {
            while (alive.get() || !Thread.currentThread().isInterrupted()) {
                String line;
                synchronized (sentenceLock) {
                    while (sentences.isEmpty() && !streamDone && alive.get()) {
                        try {
                            sentenceLock.wait(400);
                        } catch (InterruptedException e) {
                            return;
                        }
                    }
                    line = sentences.pollFirst();
                    if (line == null) {
                        if (streamDone && alive.get()) {
                            afterAnswer();
                        }
                        streamDone = false;
                        continue;
                    }
                }
                byte[] wav;
                try {
                    wav = client.speak(line);
                } catch (Exception e) {
                    VoiceClient.log("could not speak a line: " + e.getMessage());
                    continue;
                }
                if (wav.length <= VoiceWav.HEADER_BYTES || !alive.get()) continue;
                spoken++;
                setState(STATE_SPEAKING);
                emit(EV_SENTENCE, line, 0);
                CountDownLatch finished = new CountDownLatch(1);
                if (player.play(wav, finished::countDown)) {
                    try {
                        finished.await(60, TimeUnit.SECONDS);
                    } catch (InterruptedException e) {
                        return;
                    }
                }
            }
        }, "assist-speaker");
        speaker.setDaemon(true);
        speaker.start();
    }

    /** After the last word: listen again if it asked something, else let go. */
    private void afterAnswer() {
        boolean followUp = lastReply.trim().endsWith("?");
        if (wakeMode || followUp) {
            beginTurn();
        } else {
            stopSelf();
        }
    }

    private void backToIdle() {
        if (wakeMode) {
            beginTurn();
        } else {
            stopSelf();
        }
    }

    private String wakePhrase() {
        String phrase = getSharedPreferences(VoiceClient.PREFS, MODE_PRIVATE)
                .getString("jarvis_wake_phrase", "hey jarvis");
        return phrase == null || phrase.trim().isEmpty() ? "hey jarvis" : phrase.trim();
    }

    // ---- the microphone -----------------------------------------------------

    private boolean hasMic() {
        return ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
                == PackageManager.PERMISSION_GRANTED;
    }

    private void startCapture() {
        if (!hasMic()) {
            emit(EV_ERROR, "Jarvis needs the microphone.", 0);
            stopSelf();
            return;
        }
        int min = AudioRecord.getMinBufferSize(VoiceTurnRules.RATE, AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT);
        int buffer = Math.max(min, VoiceTurnRules.FRAME_BYTES * 8);
        try {
            record = new AudioRecord.Builder()
                    .setAudioSource(MediaRecorder.AudioSource.VOICE_RECOGNITION)
                    .setAudioFormat(new AudioFormat.Builder()
                            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                            .setSampleRate(VoiceTurnRules.RATE)
                            .setChannelMask(AudioFormat.CHANNEL_IN_MONO)
                            .build())
                    .setBufferSizeInBytes(buffer)
                    .build();
            record.startRecording();
        } catch (Exception e) {
            emit(EV_ERROR, "Jarvis could not open the microphone.", 0);
            stopSelf();
            return;
        }
        capture = new Thread(this::captureLoop, "assist-capture");
        capture.setDaemon(true);
        capture.start();
    }

    private void stopCapture() {
        if (record != null) {
            try {
                record.stop();
            } catch (Exception ignored) {
                // never started
            }
            try {
                record.release();
            } catch (Exception ignored) {
                // released already
            }
            record = null;
        }
        if (capture != null) {
            capture.interrupt();
            capture = null;
        }
    }

    /**
     * Twenty milliseconds at a time: is this speech, has the turn ended, and is
     * someone talking over the answer. The pre-roll keeps the first syllable,
     * which is the one a threshold always clips.
     */
    private void captureLoop() {
        byte[] frame = new byte[VoiceTurnRules.FRAME_BYTES];
        Deque<byte[]> preRoll = new ArrayDeque<>();
        ByteArrayOutputStream turn = new ByteArrayOutputStream();
        int speechMs = 0;
        int silenceMs = 0;
        int totalMs = 0;
        int loudMs = 0;
        while (alive.get() && !Thread.currentThread().isInterrupted()) {
            int read;
            try {
                read = record.read(frame, 0, frame.length);
            } catch (Exception e) {
                break;
            }
            if (read <= 0) continue;
            int rms = VoiceWav.rms(frame, 0, read);
            floor = VoiceTurnRules.nextFloor(floor, rms);
            boolean loud = VoiceTurnRules.speech(rms, floor);
            emit(EV_LEVEL, "", Math.min(100, rms / 40));

            if (STATE_SPEAKING.equals(state)) {
                loudMs = loud ? loudMs + VoiceTurnRules.FRAME_MS : 0;
                if (VoiceTurnRules.bargeIn(loudMs)) {
                    // Talking over the answer wins: stop, and hear the new question.
                    player.stop();
                    loudMs = 0;
                    speechMs = 0;
                    silenceMs = 0;
                    totalMs = 0;
                    turn.reset();
                    preRoll.clear();
                    setState(STATE_LISTENING);
                }
                continue;
            }

            if (!alive.get()) break;
            if (!STATE_LISTENING.equals(state)) {
                speechMs = 0;
                silenceMs = 0;
                totalMs = 0;
                turn.reset();
                preRoll.clear();
                continue;
            }

            totalMs += VoiceTurnRules.FRAME_MS;
            if (speechMs == 0) {
                preRoll.addLast(java.util.Arrays.copyOf(frame, read));
                while (preRoll.size() > 15) preRoll.removeFirst();
            }
            if (loud) {
                if (speechMs == 0) {
                    for (byte[] kept : preRoll) turn.write(kept, 0, kept.length);
                    preRoll.clear();
                }
                speechMs += VoiceTurnRules.FRAME_MS;
                silenceMs = 0;
                turn.write(frame, 0, read);
            } else if (speechMs > 0) {
                silenceMs += VoiceTurnRules.FRAME_MS;
                turn.write(frame, 0, read);
            }

            if (speechMs >= VoiceTurnRules.MIN_SPEECH_MS && VoiceTurnRules.endOfTurn(speechMs, silenceMs, totalMs)) {
                byte[] pcm = turn.toByteArray();
                turn.reset();
                preRoll.clear();
                speechMs = 0;
                silenceMs = 0;
                totalMs = 0;
                finishTurn(pcm);
            } else if (VoiceTurnRules.nothingHeard(speechMs) && totalMs > 8000) {
                // Nothing said: with the wake word on, keep waiting quietly.
                if (!wakeMode) {
                    emit(EV_ERROR, VoiceTurnRules.ack(VoiceTurnRules.Ack.EMPTY), 0);
                    stopSelf();
                }
                turn.reset();
                speechMs = 0;
                silenceMs = 0;
                totalMs = 0;
                preRoll.clear();
            }
        }
    }

    // ---- the notification ---------------------------------------------------

    private void channel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return;
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager == null || manager.getNotificationChannel("voice_assistant") != null) return;
        NotificationChannel channel = new NotificationChannel("voice_assistant", "Jarvis listening",
                NotificationManager.IMPORTANCE_LOW);
        channel.setDescription("Shown while Jarvis is listening or answering");
        channel.setShowBadge(false);
        manager.createNotificationChannel(channel);
    }

    private void foreground() {
        Intent open = new Intent(this, MainActivity.class);
        open.putExtra("open", "/assistant");
        PendingIntent pending = PendingIntent.getActivity(this, 0, open,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Notification notification = new NotificationCompat.Builder(this, "voice_assistant")
                .setSmallIcon(android.R.drawable.ic_btn_speak_now)
                .setContentTitle("Jarvis")
                .setContentText(wakeMode ? "Listening for \"Hey Jarvis\"" : "Listening")
                .setContentIntent(pending)
                .setOngoing(true)
                .setSilent(true)
                .setPriority(NotificationCompat.PRIORITY_LOW)
                .build();
        int type = Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q
                ? ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE : 0;
        ServiceCompat.startForeground(this, 7711, notification, type);
    }

    static void turn(Context context, boolean wake) {
        Intent intent = new Intent(context, AssistService.class);
        intent.setAction(ACTION_TURN);
        intent.putExtra(EXTRA_WAKE, wake);
        ContextCompat.startForegroundService(context, intent);
    }

    static void stopSpeaking(Context context) {
        Intent intent = new Intent(context, AssistService.class);
        intent.setAction(ACTION_STOP_SPEAKING);
        ContextCompat.startForegroundService(context, intent);
    }

    static void stop(Context context) {
        context.stopService(new Intent(context, AssistService.class));
        running = false;
        state = STATE_IDLE;
        Log.d(TAG, "stopped");
    }
}
