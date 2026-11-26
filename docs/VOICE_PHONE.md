# The phone as a voice assistant

Jarvis on the phone is a real assistant, not a command box: press the mic (or the
entry point you assigned), speak, hear the answer in your own voice. Everything
runs on the household's own hardware - our STT, our model (alpaca), our TTS - with
no cloud round trip and no subscription.

## Why it is built this way

The 2026 reviews of Siri AI, Gemini for Home and Alexa+ agree on what went wrong
when the big three rebuilt their assistants: simple things got **slower**, they
sometimes **answer with silence**, they get **confused about which device** you
are talking to, and a probabilistic model improvising over a lamp is worse than a
switch. Three rules follow, and they are the design:

1. **Deterministic first, model second.** Household verbs (lights, media, volume,
   timers, weather, "is the garage open") are answered by the gateway's fast paths
   before any model is called. The model does language, not the lamp.
2. **Never silence.** Every turn is acknowledged out loud: "One moment." if the
   answer has not started within 900 ms, "I didn't catch that." on an empty
   recording, "I can't reach the house right now." offline. A spinner is not an
   answer.
3. **The phone knows it is the phone.** The app sends its own device context
   (`source: android`, device name, conversation id). It can never claim to be the
   office Echo, so a timer or a speaker can't land in the wrong room.

## The three verbs

| Route | Does |
|---|---|
| `POST /api/voice/listen` | Base64 WAV in, text out (Whisper through execution). 8 MiB cap. |
| `POST /api/chat` | The existing turn, with `stream: true`, `speech_stream: true` and a phone `voice_context` - the same sentence-by-sentence NDJSON the Echo satellites use. |
| `POST /api/voice/speak` | One sentence of text in, WAV out, in the signed-in person's own voice. |

`services/common/voice_phone.py` decides what a phone may claim: `area`,
`area_id`, `device_name`, `asked`, `extra_system_prompt`, `conversation_id`,
`whispered` - bounded, and never `satellite` or `media_player`, which belong to
the hub's own satellites. Without a signed-in caller it returns nothing.

## On the phone

- `AssistService` (foreground, `microphone`): 16 kHz mono capture, 20 ms frames,
  noise-floor tracking, 200 ms of speech to start, 700 ms of quiet to end, 15 s
  cap. While the answer plays it listens for 250 ms of sustained speech and stops
  speaking if you talk over it (barge-in) - something none of the cloud
  assistants do on a phone.
- `VoiceClient`: listen -> chat (streaming) -> speak per sentence, so the first
  sentence is spoken while the model is still writing the second.
- `AssistPlayer`: `USAGE_ASSISTANT`, takes transient audio focus with ducking, so
  music dips instead of fighting the answer.
- `JarvisAssistPlugin` + `src/plugins/jarvisAssist.ts`: the React side drives it
  and listens for `state`, `level`, `transcript`, `sentence`, `reply`, `error`.

`VoiceTurnRules` and `VoiceWav` are plain Java with no Android imports, so the
turn-taking rules (speech start, end of turn, barge-in, wake-phrase match,
sentence splitting) and the WAV handling are unit-tested on the JVM.

## Entry points

Today: the mic button on the Dashboard, and the overlay it opens. A turn also
restarts listening when the reply ends in a question, so follow-ups work.

Not built yet, and deliberately not claimed anywhere in the UI: the Android
assistant role (`android.app.role.ASSISTANT` + a `VoiceInteractionService`, which
is what makes long-press-home work), a hardware/media-button receiver, a Quick
Settings tile, a home-screen widget, and a real on-device wake word. The service
already accepts a wake flag and checks `VoiceTurnRules.wake()` on the transcript,
but nothing feeds it continuous audio yet - a wake word needs an on-device
detector, not a stream of recordings to the house.

Until then the honest advice is: assign the assistant to a button the system
already offers, or use the dashboard mic.

## Settings

`jarvis_wake_phrase` (default `hey jarvis`) is stored on the device; the overlay's
"Type instead" fallback uses the ordinary chat turn when the microphone is not
available.

## Tests

- `services/common/tests/test_voice_phone.py` - what a phone may and may not claim.
- `services/gateway/tests/test_voice_phone.py` - listen/speak end to end with a
  faked execution service, the identity order, and the refusals.
- `services/ui/android/app/src/test/java/com/jarvisos/app/VoiceTurnRulesTest.java`
  and `VoiceWavTest.java` - the turn-taking and audio-format rules.
- `services/ui/src/plugins/jarvisAssist.test.ts` - the web fallback never throws.
