# services/execution/handlers/timer.py
import contextlib
import io
import json
import logging
import math
import re
import struct
import uuid
import wave
from datetime import UTC, datetime, timedelta

import dateparser
import redis.asyncio as redis

from services.common.links import app_link  # shared with geo's place reminders
from services.config import REDIS_URL
from services.execution.schemas import ExecutionResult, TimerRequest

log = logging.getLogger("execution.timer")

HOUSEHOLD_BUCKET = "__household__"
RINGING_TTL_SECONDS = 600

_DURATION_PART = re.compile(
    r"(\d+(?:\.\d+)?)\s*(h(?:ours?|rs?)?|m(?:in(?:ute)?s?)?|s(?:ec(?:ond)?s?)?)(?![a-z])",
    re.I,
)

# Special sounds for emojis: (freq_hz, duration_sec, gap_sec, wave_type)
EMOJI_SOUNDS: dict[str, dict] = {
    "🥚": {
        "label": "Egg Timer",
        "rtttl": "egg:d=16,o=6,b=200:e,p,e,p,e,p,a,4e",
        "notes": [
            (1318.5, 0.08, 0.04, "bell"),
            (1318.5, 0.08, 0.04, "bell"),
            (1318.5, 0.08, 0.04, "bell"),
            (1760.0, 0.35, 0.08, "bell"),
            (1318.5, 0.08, 0.04, "bell"),
            (1760.0, 0.45, 0.05, "bell"),
        ],
    },
    "🍳": {
        "label": "Cooking Timer",
        "rtttl": "cook:d=16,o=6,b=200:e,p,e,p,g,4c7",
        "notes": [
            (1318.5, 0.09, 0.04, "bell"),
            (1318.5, 0.09, 0.04, "bell"),
            (1568.0, 0.14, 0.04, "bell"),
            (2093.0, 0.40, 0.05, "bell"),
        ],
    },
    "⏰": {
        "label": "Alarm Clock",
        "rtttl": "alarm:d=8,o=5,b=180:c6,p,c6,p,c6,p,g,4c6",
        "notes": [
            (1046.5, 0.12, 0.06, "square"),
            (1046.5, 0.12, 0.06, "square"),
            (1046.5, 0.12, 0.06, "square"),
            (783.99, 0.18, 0.08, "square"),
            (1046.5, 0.35, 0.05, "square"),
        ],
    },
    "🔔": {
        "label": "Chime Bell",
        "rtttl": "bell:d=4,o=5,b=160:e,g#,b,2e6",
        "notes": [
            (659.25, 0.22, 0.05, "bell"),
            (830.61, 0.22, 0.05, "bell"),
            (987.77, 0.22, 0.05, "bell"),
            (1318.5, 0.50, 0.05, "bell"),
        ],
    },
    "🍕": {
        "label": "Oven / Pizza",
        "rtttl": "pizza:d=8,o=5,b=180:c,e,g,c6,g,4c6",
        "notes": [
            (523.25, 0.14, 0.03, "triangle"),
            (659.25, 0.14, 0.03, "triangle"),
            (783.99, 0.14, 0.03, "triangle"),
            (1046.5, 0.24, 0.05, "triangle"),
            (783.99, 0.14, 0.03, "triangle"),
            (1046.5, 0.42, 0.05, "bell"),
        ],
    },
    "🍝": {
        "label": "Pasta Timer",
        "rtttl": "pasta:d=8,o=5,b=180:d,f#,a,d6,a,4d6",
        "notes": [
            (587.33, 0.14, 0.03, "triangle"),
            (739.99, 0.14, 0.03, "triangle"),
            (880.00, 0.14, 0.03, "triangle"),
            (1174.7, 0.38, 0.05, "bell"),
        ],
    },
    "☕": {
        "label": "Coffee / Tea",
        "rtttl": "coffee:d=8,o=5,b=150:g,b,d6,4g6",
        "notes": [
            (392.00, 0.18, 0.04, "sine"),
            (493.88, 0.18, 0.04, "sine"),
            (587.33, 0.18, 0.04, "sine"),
            (783.99, 0.45, 0.05, "bell"),
        ],
    },
    "🍵": {
        "label": "Tea Steep",
        "rtttl": "tea:d=4,o=5,b=140:a,c#6,2e6",
        "notes": [
            (440.00, 0.25, 0.05, "bell"),
            (554.37, 0.25, 0.05, "bell"),
            (659.25, 0.50, 0.05, "bell"),
        ],
    },
    "💊": {
        "label": "Medicine Reminder",
        "rtttl": "meds:d=8,o=5,b=160:f,a,c6,p,f,a,4c6",
        "notes": [
            (698.46, 0.16, 0.04, "sine"),
            (880.00, 0.16, 0.04, "sine"),
            (1046.5, 0.28, 0.12, "bell"),
            (698.46, 0.16, 0.04, "sine"),
            (880.00, 0.16, 0.04, "sine"),
            (1046.5, 0.35, 0.05, "bell"),
        ],
    },
    "🏋️": {
        "label": "Workout / Interval",
        "rtttl": "workout:d=16,o=5,b=220:g,g,g,4d6",
        "notes": [
            (783.99, 0.10, 0.04, "square"),
            (783.99, 0.10, 0.04, "square"),
            (783.99, 0.10, 0.04, "square"),
            (1174.7, 0.42, 0.05, "square"),
        ],
    },
    "📚": {
        "label": "Study / Reading",
        "rtttl": "study:d=2,o=5,b=120:a4,e",
        "notes": [
            (432.00, 0.45, 0.06, "bell"),
            (648.00, 0.55, 0.05, "bell"),
        ],
    },
    "🛏️": {
        "label": "Bedtime / Lullaby",
        "rtttl": "bed:d=4,o=5,b=120:e6,c6,a,2g",
        "notes": [
            (1046.5, 0.25, 0.05, "sine"),
            (880.00, 0.25, 0.05, "sine"),
            (659.25, 0.28, 0.05, "sine"),
            (523.25, 0.50, 0.05, "sine"),
        ],
    },
    "🌅": {
        "label": "Sunrise / Wake Up",
        "rtttl": "sunrise:d=8,o=5,b=160:c,e,g,b,c6,e6,4g6",
        "notes": [
            (523.25, 0.14, 0.03, "bell"),
            (659.25, 0.14, 0.03, "bell"),
            (783.99, 0.14, 0.03, "bell"),
            (987.77, 0.14, 0.03, "bell"),
            (1046.5, 0.20, 0.03, "bell"),
            (1318.5, 0.45, 0.05, "bell"),
        ],
    },
    "🧹": {
        "label": "Chores / Laundry",
        "rtttl": "chores:d=8,o=5,b=180:c,d,e,g,a,4c6",
        "notes": [
            (523.25, 0.12, 0.03, "triangle"),
            (587.33, 0.12, 0.03, "triangle"),
            (659.25, 0.12, 0.03, "triangle"),
            (783.99, 0.12, 0.03, "triangle"),
            (880.00, 0.15, 0.03, "triangle"),
            (1046.5, 0.35, 0.05, "bell"),
        ],
    },
    "🚨": {
        "label": "Urgent Siren",
        "rtttl": "siren:d=8,o=5,b=200:a,e6,a,e6,a,e6",
        "notes": [
            (880.0, 0.16, 0.02, "square"),
            (660.0, 0.16, 0.02, "square"),
            (880.0, 0.16, 0.02, "square"),
            (660.0, 0.16, 0.02, "square"),
            (880.0, 0.16, 0.02, "square"),
            (660.0, 0.22, 0.04, "square"),
        ],
    },
    "🎉": {
        "label": "Celebration Fanfare",
        "rtttl": "party:d=8,o=5,b=190:c,c,c,4g,e,4c6",
        "notes": [
            (523.25, 0.10, 0.03, "triangle"),
            (523.25, 0.10, 0.03, "triangle"),
            (523.25, 0.10, 0.03, "triangle"),
            (783.99, 0.25, 0.04, "triangle"),
            (659.25, 0.15, 0.03, "triangle"),
            (1046.5, 0.45, 0.05, "bell"),
        ],
    },
}


def emoji_options() -> list[dict[str, str]]:
    """The emoji choices the timer UI can render.

    Older payloads used a mapping keyed by emoji and expected a ``name`` field;
    the entries only carry a ``label``. Building the list here keeps that shape
    in one place and makes a missing field harmless.
    """
    return [
        {"emoji": em, "label": str(meta.get("label") or meta.get("name") or em)}
        for em, meta in EMOJI_SOUNDS.items()
        if isinstance(meta, dict)
    ]


# Selectable alarm songs and noises
ALARM_SOUNDS: dict[str, dict] = {
    "classic_alarm": {
        "label": "Classic Alarm",
        "description": "Rhythmic digital dual-beep alarm",
        "rtttl": "classic:d=16,o=5,b=180:a,p,a,4p,a,p,a,4p,a,p,a",
        "notes": [
            (880.0, 0.11, 0.05, "square"),
            (880.0, 0.11, 0.14, "square"),
            (880.0, 0.11, 0.05, "square"),
            (880.0, 0.11, 0.14, "square"),
            (1046.5, 0.14, 0.05, "square"),
            (1046.5, 0.22, 0.05, "square"),
        ],
    },
    "egg_timer": {
        "label": "Egg Timer Ring",
        "description": "Crisp mechanical kitchen egg-timer bell",
        "rtttl": EMOJI_SOUNDS["🥚"]["rtttl"],
        "notes": EMOJI_SOUNDS["🥚"]["notes"],
    },
    "gentle_chime": {
        "label": "Gentle Chime",
        "description": "Soft harmonic bell tones",
        "rtttl": "chime:d=4,o=5,b=140:e,a,c#6,2e6",
        "notes": [
            (659.25, 0.26, 0.04, "bell"),
            (880.00, 0.26, 0.04, "bell"),
            (1108.7, 0.26, 0.04, "bell"),
            (1318.5, 0.55, 0.05, "bell"),
        ],
    },
    "morning_melody": {
        "label": "Morning Melody",
        "description": "Uplifting wake-up arpeggio song",
        "rtttl": "morning:d=8,o=5,b=160:c,d,e,g,e,g,c6,4e6",
        "notes": [
            (523.25, 0.15, 0.03, "bell"),
            (587.33, 0.15, 0.03, "bell"),
            (659.25, 0.15, 0.03, "bell"),
            (783.99, 0.18, 0.04, "bell"),
            (659.25, 0.15, 0.03, "bell"),
            (783.99, 0.18, 0.04, "bell"),
            (1046.5, 0.25, 0.04, "bell"),
            (1318.5, 0.48, 0.05, "bell"),
        ],
    },
    "retro_arcade": {
        "label": "Retro Arcade",
        "description": "8-bit chiptune victory song",
        "rtttl": "arcade:d=16,o=5,b=200:c,e,g,c6,e6,g6,4c7",
        "notes": [
            (523.25, 0.09, 0.02, "square"),
            (659.25, 0.09, 0.02, "square"),
            (783.99, 0.09, 0.02, "square"),
            (1046.5, 0.09, 0.02, "square"),
            (1318.5, 0.12, 0.03, "square"),
            (1568.0, 0.35, 0.05, "square"),
        ],
    },
    "zen_bowl": {
        "label": "Zen Singing Bowl",
        "description": "Warm resonant singing bowl",
        "rtttl": "zen:d=2,o=5,b=100:a4,e",
        "notes": [
            (432.0, 0.55, 0.06, "bell"),
            (648.0, 0.65, 0.05, "bell"),
        ],
    },
    "urgent_siren": {
        "label": "Urgent Siren",
        "description": "Alternating high-low klaxon alert",
        "rtttl": EMOJI_SOUNDS["🚨"]["rtttl"],
        "notes": EMOJI_SOUNDS["🚨"]["notes"],
    },
    "rooster": {
        "label": "Sunrise Fanfare",
        "description": "Bright morning bugle call",
        "rtttl": "bugle:d=8,o=5,b=180:g,c6,e6,g6,e6,4g6",
        "notes": [
            (392.00, 0.12, 0.03, "triangle"),
            (523.25, 0.12, 0.03, "triangle"),
            (659.25, 0.12, 0.03, "triangle"),
            (783.99, 0.25, 0.04, "triangle"),
            (659.25, 0.12, 0.03, "triangle"),
            (783.99, 0.45, 0.05, "triangle"),
        ],
    },
    "emoji_sound": {
        "label": "Emoji Special Sound",
        "description": "Matches the timer's emoji automatically",
        "rtttl": EMOJI_SOUNDS["🔔"]["rtttl"],
        "notes": EMOJI_SOUNDS["🔔"]["notes"],
    },
    "tts_only": {
        "label": "Spoken TTS Only",
        "description": "Spoken announcement without a preceding chime",
        "rtttl": "tts:d=4,o=5,b=160:e,a",
        "notes": [],
    },
}


def extract_emoji(text: str | None, explicit_emoji: str | None = None) -> str | None:
    """Return explicit_emoji if given, else the first known emoji in text."""
    if explicit_emoji and explicit_emoji.strip():
        return explicit_emoji.strip()
    if not text:
        return None
    for em in EMOJI_SOUNDS:
        if em in text:
            return em
    return None


def resolve_sound_spec(sound: str | None = None, emoji: str | None = None) -> dict:
    """Resolve the note pattern and RTTTL string for (sound, emoji)."""
    if emoji and emoji in EMOJI_SOUNDS and (not sound or sound == "emoji_sound"):
        return EMOJI_SOUNDS[emoji]
    if sound and sound in ALARM_SOUNDS:
        if sound == "emoji_sound" and emoji and emoji in EMOJI_SOUNDS:
            return EMOJI_SOUNDS[emoji]
        return ALARM_SOUNDS[sound]
    if emoji and emoji in EMOJI_SOUNDS:
        return EMOJI_SOUNDS[emoji]
    return ALARM_SOUNDS["classic_alarm"]


def get_rtttl_for_timer(sound: str | None = None, emoji: str | None = None) -> str:
    spec = resolve_sound_spec(sound, emoji)
    return str(spec.get("rtttl") or ALARM_SOUNDS["classic_alarm"]["rtttl"])


def synthesize_alarm_wav(
    sound: str | None = None,
    emoji: str | None = None,
    sample_rate: int = 24000,
) -> bytes:
    """Synthesize a 16-bit mono PCM WAV buffer for the given alarm sound or emoji."""
    spec = resolve_sound_spec(sound, emoji)
    notes = spec.get("notes") or ALARM_SOUNDS["classic_alarm"]["notes"]
    samples: list[int] = []

    for freq, dur, gap, wave_type in notes:
        n_samples = max(1, int(dur * sample_rate))
        attack = max(1, int(0.012 * sample_rate))
        for i in range(n_samples):
            t = i / sample_rate
            phase = 2.0 * math.pi * freq * t
            if wave_type == "square":
                raw = 0.65 * (1.0 if math.sin(phase) >= 0 else -1.0)
            elif wave_type == "triangle":
                raw = 0.85 * (2.0 / math.pi) * math.asin(math.sin(phase))
            elif wave_type == "bell":
                # Fundamental + 2nd and 3rd harmonics with exponential bell decay
                decay = math.exp(-3.5 * (i / n_samples))
                raw = (
                    0.65 * math.sin(phase)
                    + 0.25 * math.sin(2.0 * phase) * decay
                    + 0.10 * math.sin(3.0 * phase) * (decay * decay)
                )
            else:
                raw = math.sin(phase)

            env = i / attack if i < attack else math.exp(-2.8 * ((i - attack) / max(1, n_samples - attack)))
            val = max(-1.0, min(1.0, raw * env * 0.45))
            samples.append(int(val * 32767))

        gap_samples = max(0, int(gap * sample_rate))
        samples.extend([0] * gap_samples)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return buf.getvalue()


def _read_pcm16_mono(wav_bytes: bytes, target_rate: int = 24000) -> list[int]:
    """Read arbitrary WAV bytes into 16-bit mono PCM samples at target_rate."""
    try:
        import numpy as np
        import soundfile as sf

        data, sr = sf.read(io.BytesIO(wav_bytes), dtype="float32", always_2d=True)
        mono = data.mean(axis=1)
        if sr != target_rate and len(mono) > 1:
            duration = len(mono) / sr
            new_len = max(1, int(duration * target_rate))
            mono = np.interp(
                np.linspace(0, len(mono) - 1, new_len),
                np.arange(len(mono)),
                mono,
            )
        clipped = np.clip(mono, -1.0, 1.0)
        return [int(x * 32767) for x in clipped]
    except Exception:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            raw = wf.readframes(wf.getnframes())
            sw = wf.getsampwidth()
            ch = wf.getnchannels()
            if sw == 2:
                count = len(raw) // 2
                vals = list(struct.unpack(f"<{count}h", raw[: count * 2]))
                if ch > 1:
                    vals = vals[::ch]
                return vals
    return []


def combine_wav_bytes(first_wav: bytes, second_wav: bytes, pause_sec: float = 0.2, sample_rate: int = 24000) -> bytes:
    """Splice two WAV buffers together with a brief silence between them."""
    if not first_wav:
        return second_wav
    if not second_wav:
        return first_wav
    try:
        s1 = _read_pcm16_mono(first_wav, sample_rate)
        s2 = _read_pcm16_mono(second_wav, sample_rate)
        if not s1:
            return second_wav
        if not s2:
            return first_wav
        silence = [0] * int(pause_sec * sample_rate)
        combined = s1 + silence + s2
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(struct.pack(f"<{len(combined)}h", *combined))
        return buf.getvalue()
    except Exception as e:
        log.warning(f"[timer] failed to splice WAV buffers, using second_wav: {e}")
        return second_wav


def normalize_target_devices(
    target_device: str | None = None,
    target_devices: list[str] | None = None,
) -> list[str]:
    """Normalize single and multi-device target fields into a deduplicated list."""
    out: list[str] = []
    seen: set[str] = set()
    raw_items: list[str] = []
    if target_devices:
        for item in target_devices:
            if isinstance(item, str):
                raw_items.extend(part.strip() for part in item.split(","))
    if target_device and isinstance(target_device, str):
        raw_items.extend(part.strip() for part in target_device.split(","))
    for item in raw_items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _parse_duration(text: str | None) -> timedelta | None:
    """"10m", "1h30m", "1 hour 20 minutes", "90 sec" -> a timedelta; else None.

    Every part counts: reading only the first turned "1h30m" into one hour.
    A bare number is minutes, which is what people mean by "timer 10".
    """
    if not text or not text.strip():
        return None
    stripped = text.strip()
    if re.fullmatch(r"\d+(?:\.\d+)?", stripped):
        return timedelta(minutes=float(stripped))
    parts = _DURATION_PART.findall(stripped)
    if not parts or re.search(r"\d\s*(am|pm)\b|:\d", stripped, re.I):
        return None
    total = timedelta()
    for value, unit in parts:
        n = float(value)
        u = unit[0].lower()
        total += timedelta(hours=n) if u == "h" else timedelta(minutes=n) if u == "m" else timedelta(seconds=n)
    return total if total > timedelta() else None




async def notify_timer_done(user: str, timer_data: dict) -> bool:
    """Put "<title> is done" on the person's phone (category ``timers``).

    When ``scope == 'household'`` (set up by an admin as a family reminder or
    household alarm), notifies every member of the household. Its buttons are
    gateway push callbacks (services/gateway/push_callbacks.py) that act as the
    person: Stop, Snooze 5 min and +1 min.
    """
    from services.common import notify

    title = str(timer_data.get("title") or "Timer").strip() or "Timer"
    raw_type = timer_data.get("type")
    kind = "alarm" if raw_type == "alarm" else ("reminder" if raw_type == "reminder" else "timer")
    timer_id = timer_data.get("id")
    emoji = timer_data.get("emoji")
    sound = timer_data.get("sound")
    scope = timer_data.get("scope")
    target_devices = timer_data.get("target_devices")

    again: dict = {
        "title": title,
        "type": "alarm" if kind == "alarm" else "timer",
        "target_device": timer_data.get("target_device"),
    }
    if target_devices:
        again["target_devices"] = target_devices
    if emoji:
        again["emoji"] = emoji
    if sound:
        again["sound"] = sound
    if timer_data.get("esphome_script"):
        again["esphome_script"] = timer_data.get("esphome_script")
    if scope == "household":
        again["scope"] = "household"
    # A reminder made from a note opens that note; a snooze keeps the link.
    link = app_link(timer_data.get("url")) or "/communication"
    if link != "/communication":
        again["url"] = link

    extra_data: dict = {}
    if emoji:
        extra_data["emoji"] = emoji
    if sound:
        extra_data["sound"] = sound
    if scope == "household":
        extra_data["scope"] = "household"
    if kind == "alarm" or (target_devices and any(d in ("phone", "mobile", "apk") for d in target_devices)):
        extra_data["ring_phone"] = True

    display_prefix = f"{emoji} " if emoji and not title.startswith(str(emoji)) else ""
    notice_title = f"{display_prefix}{kind.title()}: {title}"
    notice_body = f"{title} is done" if kind == "timer" else title
    priority = 5 if kind == "alarm" else 4

    if scope == "household":
        members = await notify.family()
        creator = timer_data.get("created_by")
        for candidate in (user, creator):
            if candidate and candidate != HOUSEHOLD_BUCKET and candidate not in members:
                members.append(candidate)
        sent_any = False
        for member in members:
            ok = await notify.send(notify.notice(
                member, "timers", notice_title, notice_body,
                url=link, kind="alarm" if kind == "alarm" else "timer",
                priority=priority, tag=f"timer:{timer_id}",
                data=extra_data or None,
                actions=[
                    notify.callback_action("Stop", "timer_stop", {} if timer_data.get("recurrence") else {"id": timer_id}),
                    notify.callback_action("Snooze 5 min", "timer_snooze", again),
                    notify.callback_action("+1 min", "timer_add_minute", again),
                ],
                once=f"timer:{timer_id}:{timer_data.get('expires_at')}:{member}",
            ))
            sent_any = sent_any or ok
        return sent_any

    return await notify.send(notify.notice(
        user, "timers", notice_title, notice_body,
        url=link, kind="alarm" if kind == "alarm" else "timer",
        priority=priority, tag=f"timer:{timer_id}",
        data=extra_data or None,
        actions=[
            # A recurring alarm stays scheduled: Stop only clears this ring.
            notify.callback_action("Stop", "timer_stop", {} if timer_data.get("recurrence") else {"id": timer_id}),
            notify.callback_action("Snooze 5 min", "timer_snooze", again),
            notify.callback_action("+1 min", "timer_add_minute", again),
        ],
        # One notice per firing, however often the scheduler retries it.
        once=f"timer:{timer_id}:{timer_data.get('expires_at')}",
    ))


async def get_redis():
    return redis.from_url(REDIS_URL, decode_responses=True)


async def record_ringing_timer(
    r,
    timer_data: dict,
    esphome_devices: list[str],
    media_entities: list[str] | None = None,
) -> None:
    """Record an actively ringing/flashing timer so Stop can silence its ESPHome and TV/media devices."""
    tid = timer_data.get("id")
    if not tid:
        return
    record = {
        "id": tid,
        "title": timer_data.get("title"),
        "user_id": timer_data.get("user_id"),
        "scope": timer_data.get("scope", "user"),
        "esphome_devices": esphome_devices,
        "media_entities": list(media_entities or []),
        "started_at": datetime.now(UTC).isoformat(),
    }
    with contextlib.suppress(Exception):
        await r.set(f"timer_ringing:{tid}", json.dumps(record), ex=RINGING_TTL_SECONDS)


async def stop_ringing_devices(
    r,
    *,
    timer_id: str | None = None,
    user_id: str | None = None,
    device_name: str | None = None,
    ha_url: str | None = None,
    ha_token: str | None = None,
) -> list[str]:
    """Stop any active ESPHome screen flash / alarm AND any ringing TV / HA media_player."""
    import os

    from services.execution import esphome_client
    from services.execution.announce_handlers import stop_tv_or_media_alert

    eff_ha_url = ha_url or os.environ.get("HA_URL", "")
    eff_ha_token = ha_token or os.environ.get("HA_TOKEN", "")

    stopped_devices: list[str] = []
    try:
        keys = await r.keys("timer_ringing:*")
    except Exception:
        keys = []

    for raw_k in keys:
        key = raw_k.decode() if isinstance(raw_k, bytes) else raw_k
        raw = await r.get(key)
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        if timer_id and rec.get("id") != timer_id:
            continue
        if (
            user_id
            and not timer_id
            and rec.get("scope") != "household"
            and rec.get("user_id") != user_id
        ):
            continue
        devs = list(rec.get("esphome_devices") or [])
        media_ents = list(rec.get("media_entities") or [])
        if device_name:
            if device_name.startswith("media_player."):
                if device_name not in media_ents:
                    media_ents.append(device_name)
            elif device_name not in devs:
                devs.append(device_name)
        for dev in devs:
            with contextlib.suppress(Exception):
                await esphome_client.stop_device_alert(dev)
                stopped_devices.append(dev)
        for ment in media_ents:
            if eff_ha_url and eff_ha_token:
                with contextlib.suppress(Exception):
                    await stop_tv_or_media_alert(eff_ha_url, eff_ha_token, ment)
                    if ment not in stopped_devices:
                        stopped_devices.append(ment)
        with contextlib.suppress(Exception):
            await r.delete(key)

    if device_name and device_name not in stopped_devices:
        if device_name.startswith("media_player.") and eff_ha_url and eff_ha_token:
            with contextlib.suppress(Exception):
                await stop_tv_or_media_alert(eff_ha_url, eff_ha_token, device_name)
                stopped_devices.append(device_name)
        else:
            with contextlib.suppress(Exception):
                await esphome_client.stop_device_alert(device_name)
                stopped_devices.append(device_name)

    return stopped_devices


async def handle_timer(req: TimerRequest) -> ExecutionResult:
    action = req.action
    log.info(f"[timer] Action: {action}")

    r = await get_redis()

    try:
        if action == "add":
            raw_scope = (req.scope or "").strip().lower()
            is_household = raw_scope in ("household", "family")
            if is_household and not req.user_context.is_admin:
                return ExecutionResult(
                    status="FAILURE",
                    message="Only an admin can set household timers or reminders.",
                    service="timer_add",
                )

            # 1. Resolve Time
            # Single timestamp convention: UTC-aware everywhere. Legacy Redis
            # entries written as naive-local are handled by the scheduler.
            now = datetime.now(UTC)
            expires_at = now

            duration = _parse_duration(req.duration_str)
            # The Communication page has one box for "10m" *or* "3pm": a value
            # that is not a duration is a clock time.
            time_str = req.time_str or (req.duration_str if req.duration_str and duration is None else None)
            if duration is not None:
                expires_at += duration
            elif time_str:
                dt = dateparser.parse(time_str, settings={"PREFER_DATES_FROM": "future"})
                if dt:
                    # Naive wall-clock input ("3pm") means the container's
                    # local zone; normalize everything to UTC for storage.
                    if dt.tzinfo is None:
                        dt = dt.astimezone()
                    expires_at = dt.astimezone(UTC)
                    if expires_at <= now:
                        expires_at += timedelta(days=1)

            if expires_at == now:
                return ExecutionResult(status="FAILURE", message="Could not determine timer duration/time.", service="timer_add")

            # 2. Calculate duration in seconds for progress display
            duration_sec = max(1, int((expires_at - now).total_seconds()))

            # 3. Normalize multi-device targets, emoji, sound, and scope
            user_id = req.user_context.user
            timer_id = str(uuid.uuid4())
            devices = normalize_target_devices(req.target_device, req.target_devices)
            primary_device = req.target_device if req.target_device is not None else (devices[0] if len(devices) == 1 else (", ".join(devices) if devices else None))
            title = req.title or f"{req.type.title()} {timer_id[:4]}"
            emoji = extract_emoji(title, req.emoji)

            timer_obj: dict = {
                "id": timer_id,
                "user_id": user_id,
                "type": req.type,
                "title": title,
                "expires_at": expires_at.isoformat(),
                "active": True,
                "recurrence": req.recurrence,
                "target_device": primary_device,
                "duration_sec": duration_sec,
            }
            if devices:
                timer_obj["target_devices"] = devices
            if is_household:
                timer_obj["scope"] = "household"
                timer_obj["created_by"] = user_id
            else:
                timer_obj["scope"] = "user"
            if emoji:
                timer_obj["emoji"] = emoji
            if req.sound:
                timer_obj["sound"] = req.sound
            if req.tts_enabled is not None:
                timer_obj["tts_enabled"] = req.tts_enabled
            if req.esphome_script:
                timer_obj["esphome_script"] = req.esphome_script
            if req.flash_screen is not None:
                timer_obj["flash_screen"] = req.flash_screen

            link = app_link(req.url)
            if link:
                timer_obj["url"] = link

            bucket = HOUSEHOLD_BUCKET if is_household else user_id
            await r.set(f"timer:{bucket}:{timer_id}", json.dumps(timer_obj))

            local_expires = expires_at.astimezone()
            scope_note = " (household)" if is_household else ""
            msg = f"Set {req.type}{scope_note} '{timer_obj['title']}' for {local_expires.strftime('%I:%M %p')}."
            return ExecutionResult(
                status="SUCCESS",
                message=msg,
                service="timer_add",
                detail={
                    "timer_id": timer_id,
                    "scope": timer_obj["scope"],
                    "target_devices": devices,
                    "expires_at": timer_obj["expires_at"],
                },
            )

        elif action == "list":
            user_id = req.user_context.user
            timers = [t for _, t in await _load_timers(r, user_id)]

            if not timers:
                return ExecutionResult(status="SUCCESS", message="No active timers.", service="timer_list")

            lines = [
                f"- {t.get('emoji') + ' ' if t.get('emoji') else ''}{t['title']}: {t['expires_at']}"
                + (" [Household]" if t.get("scope") == "household" else "")
                for t in timers
            ]
            return ExecutionResult(status="SUCCESS", message="Active Timers:\n" + "\n".join(lines), service="timer_list")

        elif action == "delete":
            # Prefer exact id match; fall back to title substring match.
            user_id = req.user_context.user
            ha_url = req.user_context.ha_url
            ha_token = req.user_context.ha_token
            deleted_count = 0
            for k, t in await _load_timers(r, user_id):
                if t.get("scope") == "household" and not req.user_context.is_admin and t.get("user_id") != user_id:
                    continue
                if req.id:
                    if t.get("id") == req.id:
                        await r.delete(k)
                        await stop_ringing_devices(r, timer_id=req.id, user_id=user_id, ha_url=ha_url, ha_token=ha_token)
                        deleted_count += 1
                elif req.title and req.title.lower() in t["title"].lower():
                    await r.delete(k)
                    await stop_ringing_devices(r, timer_id=t.get("id"), user_id=user_id, ha_url=ha_url, ha_token=ha_token)
                    deleted_count += 1

            if deleted_count > 0:
                return ExecutionResult(status="SUCCESS", message=f"Deleted {deleted_count} timer(s).", service="timer_delete")
            if req.id:
                await stop_ringing_devices(r, timer_id=req.id, user_id=user_id, ha_url=ha_url, ha_token=ha_token)
            return ExecutionResult(status="FAILURE", message="No matching timer found.", service="timer_delete")

        elif action == "stop":
            user_id = req.user_context.user
            stopped_devs = await stop_ringing_devices(
                r,
                timer_id=req.id,
                user_id=user_id,
                device_name=req.target_device,
                ha_url=req.user_context.ha_url,
                ha_token=req.user_context.ha_token,
            )
            # If a one-off timer is still in Redis, delete it; leave recurring alarms scheduled.
            if req.id or req.title:
                for k, t in await _load_timers(r, user_id):
                    if _targets(t, req):
                        if not t.get("recurrence"):
                            await r.delete(k)
                        break
            return ExecutionResult(
                status="SUCCESS",
                message="Stopped.",
                service="timer_stop",
                detail={"stopped_devices": stopped_devs},
            )

        elif action == "pause":
            user_id = req.user_context.user
            for k, t in await _load_timers(r, user_id):
                if t.get("active") and _targets(t, req):
                    t["active"] = False
                    t["paused_at"] = datetime.now(UTC).isoformat()
                    await r.set(k, json.dumps(t))
                    return ExecutionResult(status="SUCCESS", message=f"Paused timer '{t['title']}'.", service="timer_pause", detail={"timer_id": t["id"]})
            return ExecutionResult(status="FAILURE", message="No active matching timer found to pause.", service="timer_pause")

        elif action == "resume":
            user_id = req.user_context.user
            for k, t in await _load_timers(r, user_id):
                if not t.get("active") and t.get("paused_at") and _targets(t, req):
                    try:
                        paused_at = datetime.fromisoformat(t["paused_at"])
                        expires_at = datetime.fromisoformat(t["expires_at"])
                        now = datetime.now(UTC)
                        if not paused_at.tzinfo:
                            paused_at = paused_at.replace(tzinfo=UTC)
                        if not expires_at.tzinfo:
                            expires_at = expires_at.replace(tzinfo=UTC)
                        if not now.tzinfo:
                            now = now.replace(tzinfo=UTC)

                        elapsed_paused = now - paused_at
                        new_expires = expires_at + elapsed_paused
                        t["expires_at"] = new_expires.isoformat()
                    except Exception as err:
                        log.error(f"Error recalculating timer expiry on resume: {err}")

                    t["active"] = True
                    t.pop("paused_at", None)
                    await r.set(k, json.dumps(t))
                    return ExecutionResult(status="SUCCESS", message=f"Resumed timer '{t['title']}'.", service="timer_resume", detail={"timer_id": t["id"]})
            return ExecutionResult(status="FAILURE", message="No paused matching timer found to resume.", service="timer_resume")

        elif action == "reset":
            user_id = req.user_context.user
            for k, t in await _load_timers(r, user_id):
                if _targets(t, req):
                    dur = max(1, int(t.get("duration_sec") or 60))
                    now = datetime.now(UTC)
                    t["expires_at"] = (now + timedelta(seconds=dur)).isoformat()
                    t["active"] = True
                    t.pop("paused_at", None)
                    await r.set(k, json.dumps(t))
                    return ExecutionResult(
                        status="SUCCESS",
                        message=f"Reset timer '{t['title']}'.",
                        service="timer_reset",
                        detail={"timer_id": t["id"], "expires_at": t["expires_at"]},
                    )
            return ExecutionResult(status="FAILURE", message="No matching timer found to reset.", service="timer_reset")

        return ExecutionResult(status="FAILURE", message=f"Unknown timer action: {action}", service="timer")

    except Exception as e:
        log.error(f"Timer error: {e}")
        return ExecutionResult(status="FAILURE", message=f"Timer error: {e!s}", service="timer")


def _targets(timer: dict, req) -> bool:
    """Whether a pause/resume/reset/stop request names this timer.

    An id is exact (the dashboard always sends one); otherwise the title is a
    case-insensitive substring, and no title at all means "any". Pause used to
    ignore the id, so pausing "Timer 1" could pause "Timer 10", and resume
    ignored the title too and woke whichever paused timer came first.
    """
    if req.id:
        return timer.get("id") == req.id
    return not req.title or req.title.lower() in str(timer.get("title", "")).lower()


async def _load_timers(r, user_id: str | None) -> list[tuple[str, dict]]:
    """Every stored timer as (key, timer), for one user (plus household) or everyone.

    Timers live at ``timer:<user>:<id>`` (and ``timer:__household__:<id>`` for
    admin-created household reminders/alarms). The automation service keeps each
    timer's run history beside it as a Redis list at ``timer:<user>:<id>:history``,
    which the same ``timer:<user>:*`` pattern also matches; reading that as a
    string raised WRONGTYPE and failed every list, pause and delete once a timer
    had fired. Only three-part keys are timers.
    """
    patterns = [f"timer:{user_id}:*", f"timer:{HOUSEHOLD_BUCKET}:*"] if user_id else ["timer:*:*"]
    out: list[tuple[str, dict]] = []
    seen_keys: set[str] = set()
    for pattern in patterns:
        for k in await r.keys(pattern):
            key = k.decode() if isinstance(k, bytes) else k
            if key in seen_keys or key.count(":") != 2:
                continue
            seen_keys.add(key)
            data = await r.get(key)
            if not data:
                continue
            try:
                out.append((key, json.loads(data)))
            except ValueError:
                log.warning(f"[timer] skipping unreadable timer {key}")
    return out


async def get_active_timers(user_id: str | None = None):
    r = await get_redis()
    return [t for _, t in await _load_timers(r, user_id)]
