package com.jarvisos.app;

import android.service.voice.VoiceInteractionService;

/**
 * The system's handle on Jarvis as a Digital Assistant / Voice Input.
 *
 * Android only lists an app under Settings -> Default digital assistant (and
 * only lets a phone's side button or Home gesture hand over the assistant key)
 * when the app declares a voice interaction service. Without one, "Jarvis" simply
 * never appeared in that list, whatever the ASSIST intent filter said.
 *
 * The service itself does nothing: the work happens in
 * {@link JarvisVoiceInteractionSessionService} when the system asks for a session.
 */
public final class JarvisVoiceInteractionService extends VoiceInteractionService {
}
