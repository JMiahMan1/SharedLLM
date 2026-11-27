package com.jarvisos.app;

import android.content.Context;
import android.os.Bundle;
import android.service.voice.VoiceInteractionSession;
import android.service.voice.VoiceInteractionSessionService;

/**
 * What the system calls when Jarvis is chosen as the assistant.
 *
 * The session is deliberately empty of Android's own UI: Jarvis draws its own
 * screen, with the head, the live words and the spoken answer, and mixing that
 * with the system's panel produced two assistants at once. So the session closes
 * immediately after opening the real one.
 */
public final class JarvisVoiceInteractionSessionService extends VoiceInteractionSessionService {

    @Override
    public VoiceInteractionSession onNewSession(Bundle args) {
        return new JarvisSession(this);
    }

    private static final class JarvisSession extends VoiceInteractionSession {

        JarvisSession(Context context) {
            super(context);
        }

        @Override
        public void onShow(Bundle args, int showFlags) {
            super.onShow(args, showFlags);
            try {
                startAssistantActivity(AssistActivity.askJarvis(getContext()));
            } catch (RuntimeException ignored) {
                // Nothing to show (a locked device, a refused background start): the
                // session still hides, so the phone is not left with a dead panel.
            }
            hide();
        }
    }
}
