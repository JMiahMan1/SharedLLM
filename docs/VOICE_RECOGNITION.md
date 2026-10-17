# Voice recognition at the voice satellites

When someone speaks to a Home Assistant voice satellite, Jarvis can tell who
it is and answer as them: their devices, their roles, their calendar. Someone
it does not recognise is answered as before, by the household account.

## How it works

1. The satellite streams the command to Home Assistant, which sends the
   audio to its speech-to-text. That is Jarvis
   (`services/execution/wyoming_stt.py`, a Wyoming server on port 10310).
2. Jarvis passes every event, unchanged, to the real speech-to-text
   (`VOICE_STT_UPSTREAM`, Home Assistant's Whisper), so the transcript is
   exactly what it was.
3. While Whisper works, Jarvis computes a voice signature of the same audio
   (`services/execution/voiceprint.py`) and asks Identity whose enrolled voice
   it is (`/api/voice/identify`). It waits at most 2 seconds for that.
4. Before the transcript goes back, a confident match is stored in Redis
   under the transcript's words for 20 seconds
   (`services/common/voice_speaker.py`).
5. Home Assistant sends the words to the gateway as a chat request. The
   gateway looks them up once and resolves the request as that person
   (`_recognised_speaker` in `services/gateway/main.py`).

The audio is never stored. A request that names its own user (an API key,
`rag_user`) is never changed, and a client can no longer name a `voice_id`
itself: only the proxy's match sets it.

## Enrolling

Each person records three short samples in Settings (Vocal Signature). A match
needs a score of 0.80 and a clear lead over the next person
(`services/identity/voice.py`); short commands score lower, so a one-word
command is often "not sure" and answered as the household.

## What a voice cannot do

A voice can be imitated or replayed, so a request recognised by voice never
unlocks or opens a lock, opens a cover such as the garage, or disarms the
alarm, even for an admin (`authorize_action` in
`services/execution/ha_client.py`).

## Setting it up

1. Make Home Assistant's Whisper reachable from the Jarvis server: in Home
   Assistant, Settings > Add-ons > Whisper > Configuration > Network, set
   10300.
2. In `.env`: `VOICE_STT_UPSTREAM=192.168.2.20:10300`, then restart execution.
   The log says "Voice recognition speech-to-text on :10310".
3. In Home Assistant: add the Wyoming Protocol integration with host
   `192.168.2.205`, port `10310`, and choose it as the speech-to-text of the
   voice assistant pipeline the satellites use.

Undo: set the pipeline's speech-to-text back to Whisper.
