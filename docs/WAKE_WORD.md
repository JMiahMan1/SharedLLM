# Wake word: sensitivity now, a household "Hey Jarvis" model later

## How a wake word model reaches an Echo

The Echos (hassmic, echo-dot-assist) run microWakeWord on the device (`wake_mww.c`). The Echo never fetches a
model or a manifest by itself: HomeHub pushes them.

1. HomeHub downloads ESPHome's v2 model once (`<id>.json` and `<id>.tflite` from
   `esphome/micro-wake-word-models`) into `<data>/hassmic_wake_models/`, with the sha256 of the whole download.
2. When an Echo is switched to microWakeWord (or brought up on it), `HassmicManager.ensure_models` uploads each
   model in the entry option `echo_wake_models` that the Echo lacks: `POST /api/mww/add/<id>` with
   `"<manifest length>\n<manifest JSON><model.tflite>"`, signed with the Echo's session key.
3. The Echo checks the model loads, writes its own `manifest.json` (`mww_store.c`, cutoff kept to two decimals,
   clamped to 0.50 to 0.99) and reloads its wake words at once (`core_wake_models_changed`).
4. A model already on the Echo is tuned without an upload: `POST /api/mww/edit/<id>` with `cutoff=0.94` and
   `window=4` lines. The Echo rewrites the manifest and reloads. No reflash, no reboot.

## Sensitivity (per Echo, HomeHub device page)

Devices > the Echo > Wake word > Sensitivity. Saved as the entry option `echo_wake_sensitivity` and applied to
every wanted, installed model; a model installed later gets a manifest already at that level
(`device_manifest`: the published manifest with the new cutoff and window and a `homehub` section saying what it
was made from; same `.tflite`). On every bring-up HomeHub puts back the level if the Echo's values differ. With
no level chosen in HomeHub, nothing is touched.

| Level | Cutoff | Window | hey_jarvis | okay_nabu | hey_mycroft | alexa |
|---|---|---|---|---|---|---|
| Standard | published | published (5) | 0.97 / 5 | 0.97 / 5 | 0.95 / 5 | 0.90 / 5 |
| More sensitive | published - 0.03 | published | 0.94 / 5 | 0.94 / 5 | 0.92 / 5 | 0.87 / 5 |
| Most sensitive | published - 0.06 | published - 1 | 0.91 / 4 | 0.91 / 4 | 0.89 / 4 | 0.84 / 4 |

Floors: no level goes below a cutoff of 0.80 or a window of 3 (a model published below them is left as it is).

Why these values:

- A detection is the mean of the last `sliding_window_size` probabilities (one every 30 ms) above
  `probability_cutoff`, compared in 255ths on the Echo (0.97 is 247/255, 0.94 is 239, 0.91 is 232).
- ESPHome's own manifests sit between 0.90 (alexa) and 0.97 (hey_jarvis): the model author moves the cutoff by
  0.02 to 0.07 between models to put each at a similar false-accept rate. Steps of 0.03 and 0.06 stay inside the
  range the author already uses, so they move along each model's curve rather than off it.
- microWakeWord's published ROC curves (`benchmarks/hey_jarvis_roc_curve.png`, Picovoice's adult test set) are
  steep: "Hey Jarvis" reaches 0 false rejects at about 0.12 false accepts per hour. Adults are already heard; the
  misses are voices the model was not trained on (children: higher pitch and formants, shorter vowels). Lowering the
  cutoff is how those get through, and the price is more false accepts, most of all from speech-like audio (TV).
- A window of 4 instead of 5 averages 120 ms instead of 150 ms, so a short, quick "Hey Jarvis" is not diluted by
  the frames around it. It also makes a single loud TV syllable count for more, which is why it is only in Most.

The device page shows a plain warning, logs each change (`wake word sensitivity changed from ... to ...` and the
per-model cutoff/window), and lists the recent wake-ups the Echo logged (`wake: <word> (microwakeword, mean
N/255)`) with the probability and the cutoff at the time. A wake-up that passed with little to spare means the
setting is close; many nobody asked for mean it is too sensitive. Near misses are not logged by the Echo.

## Plan: a custom "Hey Jarvis" model that hears children (not started)

The setting above trades false accepts for children's recall. A model trained with children's voices moves the
whole curve instead. Plan:

### 1. Positives (about 30,000 to 50,000 clips)

- **TTS on the alpaca audio-server** (`POST /api/tts`), in a quiet window:
  - Kokoro: every voice in the registry, blends of two (`af_x,am_y`), speeds 0.8 to 1.3, phrasings "Hey Jarvis",
    "Hey, Jarvis", "Hey Jarvis!", "Hay Jarvis", "Hey Jarvi".
  - Piper (`voice: "piper:<name>"`): every installed voice, `length_scale` 0.8 to 1.3, plus
    `piper-sample-generator`'s multi-speaker model with random speaker embeddings (what microWakeWord's official
    models use).
- **Child-like voices**: no Kokoro voice is a child (every male voice measures 88-166 Hz). Shift pitch up 4 to 8
  semitones and formants up 1.15x to 1.3x (vocal tract length scaling), separately and together, from the higher
  female and blended voices; the arcade's paper boy (`am_eric,af_kore` resampled 1.32x) is a working example. Keep
  a third of positives child-shifted.
- **Optional real recordings**: a few dozen takes of each child saying it in different rooms, with the parents'
  consent, kept on the training box only (never in git, never in a cloud service), and used only for evaluation if
  possible, so the score is honest.
- **Augmentation** (microWakeWord's `Augmentation` / audiomentations): room impulse responses (far field, 1 to 5 m),
  household noise and music beds at SNR 0 to 20 dB, TV/radio speech underneath, gain -10 to +10 dB, the Echo's
  far-field front end where possible (record a handful of clips through an Echo's `micAsr` to check the spectrum).

### 2. Negatives

- microWakeWord's pre-generated negative features on Hugging Face (`kahrendt/microwakeword`: speech, dinner party,
  no speech, background), as the official models use.
- Household-specific hard negatives: TV and streaming audio (a few hours captured from the living-room TV's output,
  kept local), children's speech without the phrase, and near phrases ("Hey Charles", "Harvest", "Jars", "Hey
  Jordan", "Garvis") generated with the same TTS and shifts.

### 3. Training (microWakeWord recipe)

- `microwakeword` at a pinned commit, the `basic_training_notebook` recipe as a script: 40 mel features at 10 ms,
  MixedNet streaming model, SpecAugment, `negative_class_weight` raised for the TV negatives, best weights chosen
  first by false accepts per hour on the ambient set, then by recall. Quantise to int8 and export streaming
  `.tflite`. Check it loads in hassmic's interpreter (`tests/unit/mww_ref.py` in echo-dot-assist) before any Echo.
- **Where and when**: the RTX 4060 (8 GB) is shared with household voice, and llama-server holds about 6.5 GB.
  Train only in a quiet window (for example 01:00 to 06:00), with `PROXY_HOME_MODEL` paused so the proxy does not
  reload the chat model under it, the training container capped to the GPU memory left, and stopped at 06:00
  (checkpoint every 1,000 steps so a night can resume). No coding jobs on alpaca at the same time.

### 4. Evaluation

- **False accepts per hour**: run the streaming model over at least 24 hours of held-out ambient audio (DiPCo or
  the dinner-party set plus 10+ hours of the household's own TV audio) with the Echo's exact rule (window mean over
  cutoff, 1 s hold after a detection), and plot false accepts per hour against the cutoff.
- **False rejects**: held-out TTS positives, held-out child-shifted positives, and the real child takes if any,
  each reported separately, at the cutoff that gives 0.5 false accepts per hour or less.
- Ship only if child recall beats `hey_jarvis` at Most sensitive with fewer false accepts; then a week on one Echo
  with the wake-up diagnostics before the others.

### 5. Serving it through HomeHub

The household model is one more model id (for example `hey_jarvis_home`): its `.json` manifest (with its own
published cutoff and window, from the evaluation) and `.tflite` placed in HomeHub's model cache (or a local model
URL beside `WAKE_MODELS_URL`, with its sha256), then added to the Echo's `echo_wake_models` option. HomeHub uploads
it like the official ones, the sensitivity levels apply to its published cutoff, and "Hey Jarvis" and "Alexa"
stay available as they are.

### Estimate

| Step | Effort | GPU time (RTX 4060) |
|---|---|---|
| Positive generation (Kokoro + Piper, ~40k clips) and shifts | 1 day | ~1 h (TTS) |
| Negatives (download, TV capture, near phrases) | 0.5 to 1 day | ~0.5 h (near-phrase TTS) |
| Augmentation and feature generation | 0.5 day | CPU only, ~2 to 3 h |
| Training (2 to 3 runs of ~20k to 40k steps) | 1 day | ~2 to 3 h per run, so 6 to 8 h over 2 or 3 nights |
| Evaluation (24 h+ ambient, plots) and hassmic interpreter check | 1 day | ~0.5 h |
| HomeHub serving, one-Echo trial, rollout | 0.5 day plus a week of observation | none |

Total: about 4 to 5 days of work and 8 to 10 GPU hours, spread over 3 or 4 quiet nights.
