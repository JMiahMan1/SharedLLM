"""A short window in which Jarvis learns a recognised person's voice.

At a voice satellite, "learn my voice" makes the gateway mark that person here
for a few minutes. The speech-to-text proxy is the only service holding the
command audio, so it reads the mark when the same voice is recognised again and
offers that take to Identity as another sample. The mark expires on its own, so
a forgotten session never keeps learning from ordinary conversation.

Only an already-enrolled voice can be refreshed this way: without a match there
is no trustworthy way to say whose profile a new take belongs to.
"""

#: How long a spoken "learn my voice" stays open. Long enough for a few
#: commands, short enough that a later conversation is not learned by accident.
TTL_SECONDS = 300

#: How many confident commands one session keeps.
TARGET_SAMPLES = 3

#: Identification accepts a match at 0.80. A learning take has to clear a
#: higher bar because a wrong sample would quietly poison the profile.
MIN_SCORE = 0.85


def key(username: str) -> str:
    """The Redis record saying ``username`` asked Jarvis to learn their voice."""
    return f"voice:learning:{username}"


def encode(remaining: int = TARGET_SAMPLES) -> str:
    """A session's remaining count, stored as a plain integer so it can be decremented."""
    return str(max(0, int(remaining)))


def remaining(raw: str | None) -> int | None:
    """The session's remaining samples, or None when no session is open."""
    try:
        left = int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        return None
    return left if left > 0 else None
