# The Jarvis Head

One face, drawn the same way everywhere: the phone, the tablet on the wall, a
watch, an ESPHome OLED, a tray icon. `services/ui/src/components/voice/jarvisFace.ts`
is the face's *behaviour* with no React in it; `JarvisHead.tsx` is one renderer for
it; this page is the spec so another renderer can be written for any surface.

```
  ╭───────────────────────────╮
  │  ┌─────────────────────┐  │   shell   dark slate, glowing accent rim
  ◖  │   ●         ●       │  ◗   visor   full-face dark glass
  │  │                     │  │   eyes    inside the visor
  ◖  │      ▁▃▅▃▁          │  ◗   bars    the voice, inside the visor
  │  └─────────────────────┘  │   pods    ear-pods, pulse with sound
  ╰───────────────────────────╯   pip     a small status light on the crown
```

## Why he looks like this

The first version was a heraldic shield with the mouth and voice bars painted on
top of it **in the accent colour, on an accent-coloured shield**: a speaking face
looked exactly like a silent one. That is the bug this shape fixes. The face is now
a rounded companion head (a squircle, not a shield) and *every* luminous part of it
- eyes, brows, mouth, voice bars, thinking dots - sits **inside a dark glass visor**.
Nothing that carries meaning is ever drawn on the accent.

Design intent: a friendly companion machine (Vector, Cozmo, Eve), not a badge and
not a robot skull. Rounded, slightly sculpted, one continuous surface, with the
glass face as the only high-contrast area. It must read at 32 px and at 200 px.

## Silhouette (120-unit view box)

| Part | Geometry | Notes |
|---|---|---|
| Shell | `x=14 y=18 w=92 h=84 rx=36` | Fastest way to spot a renderer bug: `rx` must be a large fraction of the short side. A shield with a point is wrong. |
| Rim | inner rect inset 1.5, stroke accent | `opacity = 0.22 + glow * 0.5`. This is the head's "mood ring", and it goes amber for `error`. |
| Visor | `x=22 y=32 w=76 h=62 rx=26` | Dark glass, `#0b0d12 → #131926`. **Must enclose the mouth** (bottom edge 94, mouth around 78). |
| Eyes | centres `(46,54)` and `(74,54)`, `rx=8`, `ry=8.6 * eyeOpen` | Pupils `r=3.1` offset by `lookX`/`lookY`. |
| Brows | `M38 → L52` and mirrored, stroke 2 | Drawn only for a worried or cross face: `brow` beyond ±0.15, and a worried brow reaches -1. |
| Mouth | arc at `y=78`, half-width `11 + 2*mouthCurve`, sag `5.5*mouthCurve`, stroke `2.6 + mouthOpen*4.2` | Replaced entirely while speaking. |
| Voice bars | 5 bars, 8 apart, centred `x=60`, base `y=76` | Accent halo `w=5.6` plus a **white core** `w=2.7` (`#ffffff`) - the white core is what reads as speech. |
| Thinking dots | 3 circles at `x=52/60/68`, `y=76`, `r = 1.6 + dot*1.5` | Replace the mouth while thinking. |
| Ear-pods | `w=14 h=30 r=7` at `x=4` and `x=92`, `y=46` | Dark capsule with an accent inner capsule at `opacity 0.18 + pod*0.82`. |
| Status pip | circle at `(60,25)` `r=2.2` plus two crown arcs | `opacity` from `pip`; the blurred halo is dropped in `compact`. |

`compact` (24-32 px) drops the glow, the pod halos, the inner sheen, the pupils
and the eye highlight - and keeps the shell, the visor, both eyes and the five
speaking bars, because those are the parts that tell you what he is doing.

## The contract

```ts
faceFor(state: FaceState, level = 0, tickMs = 0): JarvisExpression
faceStateFor(state: FaceState, reaction: 'happy' | 'error' | null): FaceState
levelToUnit(level: number): number        // 0..100 -> 0..1, NaN -> 0
blinkAt(tickMs: number): number           // 0..1, 140 ms of a 4200 ms cycle
voiceBar(tickMs: number, index: number): number
thinkingDot(tickMs: number, index: number): number
```

Rules a second renderer must keep:

1. **Pure.** The same `(state, level, tick)` always produces the same expression.
   No `Date.now()`, no `Math.random()`, no accumulation between frames.
2. **Bounded.** Every number stays in range (0..1, or -1..1 for `lookX/lookY/brow`,
   tilt within ±12). A renderer can multiply any field by a length without checking.
3. **Driven by a clock you pass in.** 30 fps is enough; the UI uses a
   `requestAnimationFrame` loop throttled to 33 ms. With `prefers-reduced-motion`
   the UI freezes `tickMs = 0` and draws one still frame - which is a valid,
   complete face, not a broken one.

## Expression channels

| Field | Range | Means |
|---|---|---|
| `eyeOpen` | 0..1 | Lid position. 0 is asleep; 1 is wide awake. |
| `lookX`, `lookY` | -1..1 | Pupil offset, in units of about 3 px in the 120 box. |
| `squint` | 0..1 | Happy arcs instead of round eyes. |
| `brow` | -1..1 | -1 worried (inner ends up), +1 cross (inner ends down), 0 relaxed/no brows. |
| `mouthCurve` | -1..1 | -1 frown, 0 flat, +1 smile. |
| `mouthOpen` | 0..1 | Lip thickness/opening. |
| `bars` | `number[]` | Five voice-bar heights; empty when not speaking. |
| `dots` | `number[]` | Three thinking-dot pulses; empty unless thinking. |
| `glow` | 0..1 | Rim and shell halo. |
| `pod` | 0..1 | Ear-pod light. |
| `pip` | 0..1 | Crown status light. |
| `bob` | ~±0.03 | Vertical breathing offset. |
| `tilt` | ±12° | Head tilt. |
| `accent` | `'accent' \| 'amber'` | Amber only for `error`. |
| `label` | string | `ready`, `listening`, `thinking`, `speaking`, `happy`, `trouble`, `resting`. |

## The seven states

| State | What a person sees | Channels |
|---|---|---|
| `idle` | Breathing, an occasional blink, a soft rim. | `glow 0.3±0.08` over 6 s, `bob ±0.02`, `eyeOpen 0.92` minus the blink, `pod ≈ 0.13`, `pip ≈ 0.4`. |
| `listening` | Wide attentive eyes that still blink, ear-pods answering the room, mouth slightly open. | `eyeOpen 1 - blink`, `lookY 0.18` (looking *at* you), `pod 0.35 + level*0.65`, `glow 0.45 + level*0.5`, `mouthOpen 0.08 + level*0.22`. |
| `thinking` | Eyes glance aside, a small concerned brow, three dots ticking at the mouth, slow tilt. | `eyeOpen 0.66`, `lookX sin(1.4t)*0.55`, `lookY 0.28`, `brow -0.3`, dots, `tilt sin(0.9t)*2.5`, glow pulse over 1.6 s. |
| `speaking` | Five bright bars with white cores, blinking eyes, a nod, ear-pods in time with the voice. | `eyeOpen 0.84*(1-blink)`, `bars max(0.12, voiceBar)`, `mouthOpen 0.25 + mean*0.75`, `pod 0.35 + mean*0.65`, `bob sin(2.2t)*0.018`, `tilt sin(1.1t)*1.6`, `pip 0.9`. |
| `happy` | `^ ^` eyes and a luminous smile. | `squint 1`, `eyeOpen 0.34`, `mouthCurve 1`, `glow 0.72`, `pod 0.45 + breathe(1.4)*0.25`, `brow 0`. |
| `error` | Amber rim, worried brows, a frown, a tilt away. | `accent 'amber'`, `brow -0.85`, `mouthCurve -0.7`, `eyeOpen 0.5`, `lookY -0.3`, `tilt -7`, `pip 0.95`. |
| `sleeping` | Peaceful closed lids, dim slow breathing. | `eyeOpen 0`, `glow 0.1`, `pod 0.05`, `pip 0.1`, 6 s breath. |

**Blinking never stops while he is busy.** Listening and speaking both keep the
blink, because a face that stops blinking reads as a frozen picture. Thinking
blinks too; sleeping and happy do not (their lids are already doing something).

## Reactions

`faceStateFor(state, reaction)` decides what he shows when a turn ends:

- `error` always wins. Something went wrong; say so plainly.
- `listening`, `thinking` and `speaking` outrank a happy flourish. You never want
  a grin in the middle of a question you are still answering.
- `idle` + `happy` → `happy`.

The UI holds `happy` for 1.8 s after a reply and `error` for 2.4 s after a failure,
then falls back to whatever the turn state is.

## Motion

- **30 fps.** Anything faster is wasted on a face this simple; 20 fps is visible.
- **Blink:** 140 ms near the end of a 4200 ms cycle, as a sine pulse, so it eases in
  and out rather than snapping.
- **Voice bars:** `clamp01(|sin(5.3t + 1.7i) + 0.6·sin(11.1t + 3.1i)| / 1.6)` - two
  incommensurate sines per bar and a phase per index, so the five never march in
  step and never repeat quickly. `i` is the bar index, so a renderer with a
  different bar count still gets variety.
- **Thinking dots:** `clamp01(0.3 + 0.7·wave³)` where `wave = (sin(3.4t - 0.9i)+1)/2`;
  the cube keeps them mostly low with a clear rise, which reads as a pulse rather
  than three lamps blinking.
- **Palette:** accent from the surface (`--site-accent`, default `#8b5cf6`);
  shell `#1c2331 → #0b0e14`; visor `#0b0d12 → #131926`; amber `#f59e0b`/`#b45309`;
  voice cores `#ffffff`.

## Porting it to a constrained surface

1. Port `faceFor` (Math only - it is ~120 lines, no imports) and call it with your
   own clock. Do not port the React component.
2. Draw the six shapes from the silhouette table into the surface's own coordinate
   box. Scale, do not redraw: proportions are what make it him.
3. Keep the visor dark and everything luminous inside it. **What breaks a port is
   a monochrome display:** on a 1-bit OLED, draw the visor as filled (or inverted)
   and the eyes/mouth/bars as holes, and choose state by *shape* (lid height, bar
   count, dot count) rather than by colour. The status pip is the one element to
   drop there.

Measured budgets: the 120-unit geometry needs ~40 vertices for the shell, 2 ellipses,
5 rects and one arc - comfortably inside an ESPHome `display` lambda or a watch face.

## Where it is used

- `services/ui/src/components/voice/VoiceAssistantOverlay.tsx` - the assistant sheet,
  driven by the real turn state and mic level, with `happy`/`error` reactions.
- Any future kiosk/tablet surface: the component takes `size` and `compact`, so it
  works as a 32 px icon and as a 240 px centrepiece. (There is no kiosk screen in
  this repo yet - `DisplayKiosk` does not exist.)
