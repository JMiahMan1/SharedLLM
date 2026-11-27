package com.jarvisos.app;

import android.content.Context;
import android.media.AudioAttributes;
import android.media.AudioFocusRequest;
import android.media.AudioManager;
import android.media.MediaPlayer;
import android.os.Build;

import java.io.File;
import java.io.FileOutputStream;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Speaks one piece of an answer, and gets out of the way the moment a person
 * starts talking again. Audio focus is taken with ducking so music playing on
 * the phone drops instead of competing, and released as soon as the line ends:
 * an assistant that keeps the focus after it has finished talking is the reason
 * a phone cannot play anything afterwards.
 */
final class AssistPlayer {

    interface Done { void finished(); }

    private final Context context;
    private final AudioManager audio;
    private MediaPlayer player;
    private AudioFocusRequest focus;
    private final AtomicBoolean stopped = new AtomicBoolean(false);
    private int counter;

    AssistPlayer(Context context) {
        this.context = context.getApplicationContext();
        this.audio = (AudioManager) this.context.getSystemService(Context.AUDIO_SERVICE);
    }

    /** Play one WAV. Returns false when it could not start. */
    synchronized boolean play(byte[] wav, Done done) {
        stop();
        if (wav == null || wav.length < VoiceWav.HEADER_BYTES) return false;
        File file = new File(context.getCacheDir(), "assist-" + (counter++ % 4) + ".wav");
        try (FileOutputStream out = new FileOutputStream(file)) {
            out.write(wav);
        } catch (Exception e) {
            VoiceClient.log("could not write the line: " + e.getMessage());
            return false;
        }
        takeFocus();
        try {
            player = new MediaPlayer();
            player.setAudioAttributes(new AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ASSISTANT)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build());
            player.setDataSource(file.getAbsolutePath());
            player.setOnCompletionListener(mp -> {
                release();
                if (done != null) done.finished();
            });
            player.setOnErrorListener((mp, what, extra) -> {
                release();
                if (done != null) done.finished();
                return true;
            });
            player.prepare();
            player.start();
            return true;
        } catch (Exception e) {
            VoiceClient.log("could not play the line: " + e.getMessage());
            release();
            return false;
        }
    }

    /** Stop mid-sentence (someone spoke over it, or the person said stop). */
    synchronized void stop() {
        stopped.set(true);
        if (player != null) {
            try {
                player.stop();
            } catch (Exception ignored) {
                // already stopped
            }
        }
        release();
    }

    synchronized boolean playing() {
        return player != null;
    }

    private void takeFocus() {
        if (audio == null) return;
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                focus = new AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK)
                        .setAudioAttributes(new AudioAttributes.Builder()
                                .setUsage(AudioAttributes.USAGE_ASSISTANT)
                                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                                .build())
                        .build();
                audio.requestAudioFocus(focus);
            }
        } catch (Exception ignored) {
            // no focus is better than no answer
        }
    }

    private void release() {
        if (player != null) {
            try {
                player.release();
            } catch (Exception ignored) {
                // released already
            }
            player = null;
        }
        if (audio != null && focus != null && Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            try {
                audio.abandonAudioFocusRequest(focus);
            } catch (Exception ignored) {
                // gone already
            }
        }
        focus = null;
    }
}
