#!/usr/bin/env python3
"""Compose HomeHub's "Game-show thinking" hold music with MusicGen, once, and keep it in the repo.

The clip is ORIGINAL: MusicGen writes it from a style prompt (no melody is given to it), and the prompt names
no show or theme. Listen to the result before committing it; if it brings a known tune to mind, change the seed
(or the prompt) and compose again.

    python scripts/compose_hold_music.py --audio-url http://<alpaca host>:8082
    python scripts/compose_hold_music.py --audio-url ... --seed 4242 --out /tmp/try.wav   # try another seed

It asks the audio server's ``POST /api/music`` (MusicGen, at most 30 s at 32 kHz; it shares the GPU with the
household's voice model, so compose rarely), then makes the clip HomeHub plays: mono, 24 kHz (Kokoro's rate, so
the usual stream needs no resampling), silence trimmed, its end crossfaded into its start so it loops, at the hold
music's loudness (``services/homehub/hold_music.py``). Beside it, ``hold_music_gameshow.json`` records the prompt,
seed and length it was made from, so it can be made again.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from services.homehub import hold_music  # noqa: E402

RECIPE = {
    "prompt": "playful game show thinking music, light pizzicato strings and a ticking clock, "
              "bright major key, bouncy and curious, steady medium tempo, background bed, no vocals",
    "duration_s": 24,
    "seed": 5150,
}
OUT_RATE = 24000


def compose(audio_url: str, recipe: dict, timeout: float) -> bytes:
    body = json.dumps(recipe).encode()
    req = urllib.request.Request(f"{audio_url.rstrip('/')}/api/music", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    b64 = data.get("audio_b64") or ""
    if not b64:
        raise SystemExit(f"the audio server returned no audio: {str(data)[:200]}")
    return base64.b64decode(b64)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--audio-url", default=os.getenv("ALPACA_AUDIO_URL", ""),
                    help="the alpaca audio server (default: $ALPACA_AUDIO_URL)")
    ap.add_argument("--seed", type=int, default=RECIPE["seed"])
    ap.add_argument("--prompt", default=RECIPE["prompt"])
    ap.add_argument("--duration", type=int, default=RECIPE["duration_s"])
    ap.add_argument("--out", type=Path, default=hold_music.GAMESHOW_CLIP)
    ap.add_argument("--raw", type=Path, help="also keep MusicGen's own output here, to listen to")
    ap.add_argument("--timeout", type=float, default=600.0)
    args = ap.parse_args()
    if not args.audio_url:
        ap.error("--audio-url (or ALPACA_AUDIO_URL) is required")
    recipe = {"prompt": args.prompt, "duration_s": args.duration, "seed": args.seed}
    raw = compose(args.audio_url, recipe, args.timeout)
    if args.raw:
        args.raw.write_bytes(raw)
    x, rate = hold_music.read_wav(raw)
    pcm = hold_music.prepare(x, rate, OUT_RATE)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    import numpy as np

    args.out.write_bytes(hold_music._wav_bytes(np.frombuffer(pcm, "<i2") / 32767, OUT_RATE))
    sidecar = {**recipe, "model": "musicgen (alpaca audio-server POST /api/music)", "rate": OUT_RATE,
               "loop_crossfade_s": hold_music.LOOP_CROSSFADE_S, "rms_dbfs": hold_music.TARGET_DBFS}
    args.out.with_suffix(".json").write_text(json.dumps(sidecar, indent=2) + "\n")
    print(f"{args.out}: {len(pcm) / 2 / OUT_RATE:.1f} s at {OUT_RATE} Hz, "
          f"{hold_music.rms_dbfs(pcm):.1f} dBFS RMS (seed {args.seed})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
