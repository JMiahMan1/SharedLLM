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

## Who a spoken request is from

The order is fixed, and every request logs one line saying which applied:

1. **The identified speaker**, when their voice clears the calibrated threshold
   and leads the next person by the calibrated margin.
2. **Else the person the satellite is assigned to** (Admin > Users & Devices >
   Voice satellites, speakers & TVs). The request is answered as them, but never
   with administrator powers, and like a voice it cannot unlock, open or disarm
   anything (`identified_by: "device"`).
3. **Else the default user**: a shared or unassigned satellite.

Every voice satellite, speaker and TV HomeHub knows appears in identity as a
device by itself (`GET /hub/voice/devices`, copied every ten minutes and on
"Refresh from HomeHub"; `services/identity/hub_devices.py`). Each is keyed by
its hardware (its MAC, else an integration identifier, else HomeHub's device
id), so renaming it or its entity keeps the assignment. New devices arrive
shared. The gateway sends the satellite's entity as `voice_device` only when no
voice was recognised; identity looks it up (cached 30 seconds, cleared on every
change). `DeviceAssignment` is something else: who may *control* an entity.
Every discovered entity is granted to the default user there, so it says
nothing about whose satellite it is and is no longer read for that.

```
[VoiceDecision] decision=device-assigned user=kit satellite=assist_satellite.kids_room top=kit 0.66 second=- threshold=0.8 margin=None speech=1.9s
```

`docker logs sharedllm_gateway 2>&1 | grep VoiceDecision` measures accuracy;
Admin > People shows the same counts per person ("Recognised by voice", "By
their satellite", "As the default user").

## Enrolling

Each person records three short samples in Settings (Vocal Signature), on their
own phone, somewhere quiet. A take with too little speech, too much background
noise (speech under 12 dB above it) or clipping is refused with what to change
(`services/execution/voiceprint.py` `check_quality`). More takes can be added
any time; the newest six are kept.

## The threshold is calibrated on the household

Identity scores every enrolled sample against the rest of its own person's
samples (same speaker) and against every other person's profile (other
speakers), and sets the threshold just above the highest other-speaker score,
between 0.72 and 0.90, and the margin to half the typical lead
(`voice.calibrate`). It is redone whenever a profile changes and stored in the
`voice_calibration` setting; Admin > Users & Devices > Voice recognition shows
it and the two score spreads. With only one person enrolled there is no one to
separate them from, so the threshold only stays just under their own scores.

When the two best candidates are a child and an adult the bar is higher (twice
the margin and 0.03 more score): a weak score says nothing rather than name a
child as a parent or a parent as a child.

## Candidate samples from assigned satellites

A command heard at a satellite assigned to one person that was not recognised,
but scored at least 0.55 against that person's profile (and nobody else
scored higher, with at least 1.5 s of speech), is kept as a candidate sample
(`POST /api/voice/candidate`, the `VoiceCandidate` table, at most twelve per
person). It is never used until an admin approves it in Voice recognition;
approved ones join the profile as `source: auto`, have their own cap of six and
never push out a sample the person recorded. "Remove auto-added" takes them all
out again. This is how a phone-enrolled profile learns how its owner sounds at
the far-field satellite they use.

## A voice nobody enrolled

A command whose voice is far from every enrolled one is answered as usual (the satellite's person, else
the default user) and, at most once a day per voice and satellite, Jarvis adds one line after the
answer: "By the way, I don't think I know your voice yet. Want me to learn it? Just say 'learn my
voice'." That wording is only used where "learn my voice" works (a satellite assigned to one person
with no voice profile yet); anywhere else it suggests asking a grown-up to set it up in the Jarvis app.
A whispered command, or one already asking to be learned, is never invited.

"Far from every enrolled one" is stricter than "not recognised": the best score must be under 0.55
(the candidate-sample floor, so nothing that could be a sample of someone is called new) and at least
0.15 under the threshold, with 2 s of speech at least 12 dB over the room. Unsure between two enrolled
people, or a little under the threshold, is never a new voice. On the live satellites (one profile,
2026-11) unrecognised commands scored in two groups, 0.45 to 0.55 and 0.58 to 0.70; 0.55 sits between.

Execution keeps such a command's signature for 20 seconds under its transcript
(`voice_speaker.looks_new`); the gateway hands it to identity (`POST /api/voice/new_voice`), which
checks it again and counts it in an anonymous cluster per satellite in Redis (`new_voices.py`: commands
at least 0.70 alike join one voice, a week's visits, when it was last asked; expires a week after it
was last heard). A cluster has no name, is never used to answer as anyone and never becomes a
profile: only "learn my voice" (the teaching session above, held for the owner's approval) or an admin
approving samples makes one. Admin > Users & Devices > Voice recognition lists a voice heard three
times or more this week, as counts only ("A new voice talked to the Office Echo 5 times this week"),
with Dismiss. Gateway logs `[VoiceNewVoice] satellite=... visits_week=N invited=yes|no`.

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

## The household's words

Whisper hears a name it has never seen as ordinary words ("plate random late"
for an artist, "so fast" for a playlist, "Wilder Hart" for a book). Its
`initial_prompt` biases it towards the words it is shown, and the Wyoming
Whisper image (rhasspy/wyoming-whisper 3.8.1) has no per-request prompt: it
builds one on every utterance from a Home Assistant names API (`--hass-api`,
`--hass-token`), fetched while the person is still talking and awaited at most
a second at the end, so it adds no wait. Jarvis serves that API itself:

* execution builds the vocabulary from the integrations every 30 minutes
  (`services/execution/stt_vocabulary.py`, `GET /execute/voice/vocabulary`):
  what was played most recently first, then the family's first names, Music
  Assistant's artists, playlists and stations (most played first),
  Audiobookshelf's titles, series and authors (newest first) and the book
  library's newest 40 titles, taken in turns, then the rooms and the players'
  names. Nothing is written down anywhere: a new artist is in the next build.
* the gateway answers the Whisper server's names commands from it
  (`services/gateway/stt_vocabulary_routes.py`, `ws://<server>:11435/stt-vocabulary/websocket`),
  read-only, for a caller inside the home network presenting a Jarvis API key.
* the Whisper server keeps as many names as fit its 200-token budget
  (`--hass-prompt-max-tokens`), in that order.

Setting it up: make an API key (any account; an admin can make one for the
household) and put it in `.env` as `WHISPER_VOCABULARY_KEY`; the `whisper`
service then reads the names from this host's gateway. Any other Wyoming
Whisper on the LAN (for example a faster machine first in
`VOICE_STT_UPSTREAM`) takes the same two settings:

```text
WYO_WHISPER_HASS_API=http://<jarvis-server>:11435/stt-vocabulary
WYO_WHISPER_HASS_TOKEN=<the key>
WYO_WHISPER_HASS_REFRESH_SECONDS=60
```

Its log says "Biasing toward names from ..." at start.

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
