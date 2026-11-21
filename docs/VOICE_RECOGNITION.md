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

The audio is never stored. A request that presents a real API key is never
changed, and a client can no longer name a `voice_id` itself: only the proxy's
match sets it. A satellite request that names nobody is answered by the
**non-admin household** identity, which has the shared Home Assistant, media,
audiobook, Nextcloud and Skylight credentials but no administrator powers and
no personal accounts. An unrecognised speaker therefore cannot administer the
system, even though they can still ask the house to do ordinary things.

## Enrolling

Each person records three short samples in Settings (Vocal Signature). A match
needs a score of 0.80 and a clear lead over the next person
(`services/identity/voice.py`); short commands score lower, so a one-word
command is often "not sure" and answered as the household.

## Refreshing a voice by talking

Once enrolled, a person can add to their own profile without opening Settings.
At a satellite, say **"Jarvis, please learn my voice"** (or "please update my
voice signature", "please refresh my voice", "please retrain my voice"). Use a
full sentence: the request itself has to be long enough for Jarvis to recognise
the speaker. Jarvis confirms and then learns from the next three commands that
it confidently recognises as that person.

Each take still has to look like the enrolled voice: it needs a match score of
at least 0.85, at least one second of speech, agreement with the existing
profile, and it must not be a near-duplicate of a sample already stored. The
newest six samples are kept. The learning window lasts five minutes; say
"stop learning my voice" to close it early. Only the signature is stored, never
the audio.

This refreshes an existing profile. A voice Jarvis does not recognise yet can
be taught at a satellite assigned to that person (next section).

## Teaching your voice at your own satellite

A profile recorded on a phone or laptop microphone often does not match the
same person at a far-field satellite, and someone who never enrolled is not
recognised at all, so the refresh above could never start for them. A
satellite assigned to exactly one person (Admin > Users & Devices, the
`assist_satellite.*` entity) can teach that person's voice instead:

1. At the satellite, say **"Jarvis, learn my voice"** (any phrase from the
   list above).
2. Jarvis answers with the person's name and asks for a sentence, then keeps
   listening. Say it (the exact words do not matter, only the voice).
3. Jarvis asks for the next one, four in all, then says it has learned the
   voice. "Stop learning my voice" ends it early; the sentences already said
   are kept.

How it works (`services/common/voice_enroll.py`): the gateway opens a session
for that satellite in Redis (five minutes, renewed with every sentence). While
any session is open, the speech-to-text proxy keeps each command's signature
for 20 seconds under its transcript; the gateway takes it from there and hands
it to Identity (`POST /api/voice/enroll`). Each take has to match the earlier
takes of the same session, so someone else cutting in is refused. Three takes
with too little speech in a row end the session.

Nothing proves who is speaking at a satellite, so a take never simply joins the
owner's profile:

- **The owner already has a voice profile:** the session's first take must
  sound like it (the same `CONSISTENCY_FLOOR` every take is checked against),
  then each take like the session's earlier ones. Those are stored in the
  profile with `source: satellite`. A stranger at the owner's satellite is
  refused at the first sentence.
- **The owner has none:** the takes are held apart (`VoicePendingEnrolment`,
  one row per session, kept seven days) and nothing is recognised from them.
  When the session ends the owner gets a notice in their own app ("It was me" /
  "Not me", the `voice_pending_decide` callback), and Identity, Vocal Signature
  lists them with Approve and Discard. Only the owner, signed in, can approve
  them (`POST /api/users/me/voice/pending/{id}`; `DELETE` discards). Approving
  never evicts samples already in a profile: if the owner enrolled elsewhere
  meanwhile, the takes must match it and only fill free places.

A satellite with nobody assigned, or shared by several people, says so and how
to fix it. Someone the satellite already recognises as a *different* enrolled
person gets the refresh above for their own profile.

## Checking what happens

Execution logs every recognition at INFO, without the signature:

```
[voice] recognised sam (score 0.91, threshold 0.8, margin 0.12; 3.2s of speech; embed 540 ms, total 610 ms)
[voice] speaker not recognised: best sam 0.71 (threshold 0.8, margin None; ...)
[voice] speaker not recognised: nobody has enrolled a voice (...)
```

`docker logs sharedllm_execution 2>&1 | grep "\[voice\]"` shows them, and
`[VoiceTeaching]` lines in the gateway follow a teaching session. The speaker
encoder loads when execution starts ("speaker encoder ready in ...s"); before
that, the first command after a restart spent about 3 seconds loading it and
missed the 2 second budget.

## What a voice cannot do

A voice can be imitated or replayed, so a request recognised by voice never
unlocks or opens a lock, opens a cover such as the garage, or disarms the
alarm, even for an admin (`authorize_action` in
`services/execution/ha_client.py`).

## Setting it up

1. Make Home Assistant's Whisper reachable from the Jarvis server: in Home
   Assistant, Settings > Add-ons > Whisper > Configuration > Network, set
   10300.
2. In `.env`: `VOICE_STT_UPSTREAM=192.0.2.20:10300`, then restart execution.
   The log says "Voice recognition speech-to-text on :10310".
   Several servers can be listed, comma-separated, fastest first
   (`VOICE_STT_UPSTREAM=192.0.2.43:10300,127.0.0.1:10300`): each command goes
   to the first that answers within 1.5 s, and a fallback is logged as a
   warning.
3. On the Jarvis server, allow only Home Assistant through the firewall. Use
   Home Assistant's address in place of `192.0.2.20`:
   ```
   sudo firewall-cmd --permanent --add-rich-rule='rule family=ipv4 source address=192.0.2.20 port port=10310 protocol=tcp accept'
   sudo firewall-cmd --reload
   ```
   Without this, Home Assistant reports that it cannot connect even though
   the port is listening.
4. In Home Assistant: add the Wyoming Protocol integration with host
   `192.0.2.205`, port `10310`, and choose it as the speech-to-text of the
   voice assistant pipeline the satellites use.

Undo: set the pipeline's speech-to-text back to Whisper.
