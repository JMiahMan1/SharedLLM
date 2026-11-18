"""Teaching a person's voice from the voice satellite assigned to them.

"Learn my voice" at a satellite (``voice_learning``) only refreshes a voice
Jarvis already recognises. A person enrolled from a browser microphone, or not
at all, is never recognised at a far-field satellite, so that path could never
start for them. This one starts from the satellite instead: a satellite assigned
to exactly one person learns that person's voice, and only theirs.

The gateway opens a session for the satellite and asks for a few sentences. The
speech-to-text proxy (``services/execution/wyoming_stt.py``) is the only service
holding the audio, so while any session is open it keeps each command's
signature for a few seconds under the transcript (``take_key``), and the
gateway, which knows which satellite the request came from, takes it from there
and hands it to Identity (``/api/voice/enroll``). The signature is a speaker
embedding, never the audio, and it lives only until the gateway reads it.
"""
import hashlib
import json

from services.common import voice_speaker

#: How long a teaching session waits for the person's next sentence.
TTL_SECONDS = 300
#: Sentences a session records. One more than identity's minimum profile, so a
#: single poor take does not decide the profile.
TAKES = 4
#: Takes in a row that produced no usable signature before the session gives up.
MAX_MISSES = 3
#: While any session is open the proxy keeps each command's signature for this
#: long: the gateway reads it as soon as the transcript's chat request arrives.
TAKE_TTL_SECONDS = voice_speaker.TTL_SECONDS
#: Set while any session is open, so the proxy keeps signatures only then.
OPEN_KEY = "voice:enroll:open"

#: What Jarvis asks the person to say, one per take. Long enough to give a few
#: seconds of speech, plain enough to say without reading.
SENTENCES = (
    "The morning sun came through the kitchen window and warmed the table.",
    "We walked down to the river after dinner and counted the ducks on the water.",
    "Please remind me to water the garden when the weather turns warm again.",
    "My favourite time of the day is the quiet hour just before everyone wakes up.",
    "The library down the road opens at nine and closes late on Thursdays.",
    "After the rain stopped, the children ran outside to jump in the puddles.",
)


def session_key(satellite: str) -> str:
    """The Redis record of the teaching session running at ``satellite``."""
    return "voice:enroll:session:" + satellite


def take_key(text: str) -> str | None:
    """Where the proxy keeps the signature of the command that said ``text``."""
    words = voice_speaker.normalize(text)
    return "voice:enroll:take:" + hashlib.sha256(words.encode()).hexdigest() if words else None


def new_session(username: str, session_id: str) -> dict:
    return {"username": username, "session": session_id, "got": 0, "misses": 0}


def encode(state: dict) -> str:
    return json.dumps(state)


def decode(raw: str | bytes | bytearray | None) -> dict | None:
    """A stored session, or None when it is missing or malformed."""
    if not isinstance(raw, (str, bytes, bytearray)) or not raw:
        return None
    try:
        state = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(state, dict) or not isinstance(state.get("username"), str) or not state["username"]:
        return None
    if not isinstance(state.get("session"), str) or not state["session"]:
        return None
    for field in ("got", "misses"):
        if not isinstance(state.get(field), int):
            state[field] = 0
    return state


def sentence(index: int) -> str:
    return SENTENCES[index % len(SENTENCES)]


def encode_take(signature: dict) -> str | None:
    """The part of a proxy signature the gateway needs, or None when there is none."""
    embedding = signature.get("embedding") if isinstance(signature, dict) else None
    if not isinstance(embedding, list) or not embedding:
        return None
    return json.dumps({
        "embedding": embedding,
        "speech_seconds": signature.get("speech_seconds"),
        "model": signature.get("model"),
    })


def decode_take(raw: str | bytes | bytearray | None) -> dict | None:
    if not isinstance(raw, (str, bytes, bytearray)) or not raw:
        return None
    try:
        take = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(take, dict) or not isinstance(take.get("embedding"), list) or not take["embedding"]:
        return None
    return take
