# Jarvis's head

One character, drawn by one function, so the phone, the wall tablet, a watch and
an ESPHome display never look like four different assistants.

The reference implementation is `services/ui/src/components/voice/jarvisFace.ts`
(pure model) and `JarvisHead.tsx` (SVG renderer). Anything else — a Wear OS
face, an Arduino sketch, a tray icon — should read the model, not copy the SVG.

## The silhouette

The head is the **shield from `services/ui/public/jarvis-icon.svg`**, unchanged in
shape. That matters: the launcher icon, the notification icon and the face are
the same outline, so he is recognisable before he animates at all. Inside the
shield:

| Part | Where |
|---|---|
| shield | `M60 6 L104 21 L104 62 C104 89 82 108 60 116 C38 108 16 89 16 62 L16 21 Z` (120-unit box) |
| visor | rounded rect `x=26 y=36 w=68 h=34 r=16`, dark |
| eyes | ellipses at `(46, 53)` and `(74, 53)`, `rx=8`, `ry=9 × eyeOpen` |
| pupils | `r=3.1` at the eye centre plus the look offset |
| mouth | curve at `y=84`, half-width `11 + 2 × mouthCurve`, sag `5.5 × mouthCurve` |
| voice bars | five `4 × (3 + 13 × bar)` rounded bars, 8 apart, centred, baseline `y=91` |

At 32 px and below, drop the glow, the second shield stroke and the pupils
(`compact` in the React component). The eyes and mouth still read.

## The contract

`faceFor(state, level, tickMs) -> JarvisExpression`

- **Deterministic.** No randomness, no clock reads inside the model. The same
  three arguments always produce the same twelve numbers. Port it as it stands and
  every device agrees.
- `level` is the microphone level **0..100**; `tickMs` is milliseconds since the
  face appeared. Freeze `tickMs` for a still frame.
- Every field is bounded, so a renderer can map it to pixels, PWM or 1-bit LCD
  without knowing what it means.

| Field | Range | Meaning |
|---|---|---|
| `eyeOpen` | 0..1 | lid position, 1 = wide awake |
| `lookX`, `lookY` | -1..1 | pupil offset, +y is **up** |
| `mouthOpen` | 0..1 | lip separation (also widens the mouth stroke) |
| `mouthCurve` | -1..1 | -1 frown, 0 flat, 1 smile |
| `bars` | 5 × 0..1 | voice bars; **empty** unless speaking |
| `glow` | 0..1 | visor/accent brightness |
| `tilt` | -12..12° | head rotation |
| `bob` | -1..1 | vertical offset, a head unit |
| `squint` | 0..1 | 1 = happy `^ ^` eyes (draw arcs, not ellipses) |
| `accent` | `accent` \| `amber` | household accent, or amber for a fault |
| `label` | string | one word for a screen reader or a serial log |

## States

| State | What he looks like | Why |
|---|---|---|
| `idle` | eye 0.92, blinks once per 4.2 s, breathes once per 4 s | alive, not busy |
| `listening` | eyes wide (1), looking slightly up, glow and mouth follow `level` | you can see you are being heard |
| `thinking` | eye 0.66, glances left and right on a 1.4 Hz sine, visor pulses | working, not stuck |
| `speaking` | eye 0.84, five moving bars, slow nod | the words are his, and they are moving |
| `happy` | `squint` 1 (arc eyes), mouthCurve 1, glow 0.72 | a short flourish after an answer |
| `error` | tilt -7°, amber, eyes low and half shut, mouth -0.7 | a fault is visible from across the room |
| `sleeping` | eyes shut, glow 0.1, slow breath | resting, still present |

**Reactions never interrupt work.** `faceStateFor(state, reaction)` keeps
`listening`, `thinking` and `speaking` above a happy flourish; only `error` may
cover them, because a failure the person cannot see is a failure they will
repeat. The UI shows `happy` for 1.8 s after a reply and `error` for 2.4 s.

## Motion rules

- 30 fps is enough. Do not run the face at 60.
- Honour "reduce motion": render a single frame at `tickMs = 0`. No blink, no
  breathing, no nod — the expression is still correct.
- Blink is 140 ms of a 4200 ms cycle (`blinkAt`). Anything longer reads as a
  slow fade or as tiredness.
- Voice bars are `|sin(5.3t + 1.7i) + 0.6·sin(11.1t + 3.1i)| / 1.6` — two
  unrelated speeds, so the pattern does not repeat on the bar's own period. Bars
  never fall to zero: a closed bar looks broken rather than quiet.
- Palette: `--site-accent` for the household's colour, `#f59e0b` for amber. Dark
  and light backgrounds both work; the visor is always dark, so the eyes always
  have something to sit on.

## Porting to constrained hardware

An ESPHome display or a 64×32 OLED cannot draw this SVG, but it does not need to:

1. Reimplement `faceFor` (it is ~90 lines of arithmetic — sine, clamp, switch).
   Check it against the tests in `jarvisFace.test.ts`, which are the reference.
2. Draw: shield outline (or just a rounded rect at tiny sizes), two filled eye
   ellipses, one mouth curve or five bars.
3. Feed it the states the assistant already publishes — `idle`, `listening`,
   `thinking`, `speaking` — plus the microphone level if the device has one.

A watch face, a TV overlay or a desktop tray icon should import the model
directly rather than re-derive it; that is the whole point of keeping it free of
React.

## Where it is used

- `VoiceAssistantOverlay.tsx` — the phone/tablet assistant sheet (replaces the
  glowing sphere).
- Any future Echo-Show-style kiosk surface: render `<JarvisHead state level />`
  and forward the same `AssistState` events.
