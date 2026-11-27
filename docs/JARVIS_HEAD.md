# The Jarvis Head

One face, drawn the same way everywhere: the phone, the tablet on the wall, a
watch, an ESPHome OLED, a tray icon. `services/ui/src/components/voice/jarvisFace.ts`
is the face's *behaviour* with no React in it; `JarvisHead.tsx` is one renderer for
it; this page is the spec so another renderer can be written for any surface.

The face **is the app's own mascot**, not a new character. Every path below is
copied from `services/ui/android/app/src/main/res/drawable/ic_launcher_foreground.xml`
- the mark on the launcher icon - in the icon's own `0 0 108 108` grid. The
component only moves those shapes.

```
        ⚡ crest            lightning bolt, above the head
     ╭───────────╮         shell    rounded head, accent gradient
   ▐ │  ●     ●  │ ▌       ears     the icon's two side accents, one each side
   ▐ │           │ ▌       visor    deep indigo band over both eyes
   ▐ │   ▁▃▅▃▁   │ ▌       eyes     #EDE9FE ovals, a sparkle on each
     ╰───────────╯         mouth    a smile, or the voice bars in a dark well
         ·  ·  ·           cheeks   two soft pink patches
```

## Why he looks like this

The mascot is friendly by construction and it is the shape people already have on
their home screen, so the assistant should not look like somebody else.

Two earlier drafts are recorded here because both mistakes were reported by the
person using the phone:

1. A heraldic **shield** with the mouth and voice bars painted on it *in the accent
   colour*: a speaking face was pixel-identical to a silent one.
2. A dark **angular helmet** with black pupils and furrowed brows, whose right ear
   was drawn at `x=92` while the shell already reached `x=106` - the ear was behind
   the head. It read as mean, and it looked nothing like the icon.

So: no brows, no black pupils, ears symmetrical about `x = 54` and **outside** the
shell, a sparkle on *both* eyes, and anything that must be legible (the voice bars)
sits on its own dark well rather than on the accent. Tests pin each one.

## Silhouette (108-unit view box)

| Part | Geometry | Notes |
|---|---|---|
| Shell | `M30,54 C30,37 40,27 54,27 C68,27 78,37 78,54 L78,65 C78,74 71,80 62,80 L46,80 C37,80 30,74 30,65 Z` | Gradient across it, light at the top left (`--site-accent-light` → `--site-accent`), default `#A78BFA → #7C3AED`. |
| Top light | `M40,40 C45,33 63,33 68,40 C60,36 48,36 40,40 Z` | `#EDE9FE` at 0.55 - the sheen that stops the head reading as a flat blob. |
| Ears | `x=25` and `x=74`, `y=48`, `9 × 15`, `rx=3` | Symmetrical about 54 (`54 − 25 = 83 − 54`) and clear of the shell (30..78). Opacity `0.35 + ear × 0.65`; a blurred copy when `ear > 0.35`. |
| Visor | `M39,45 h30 a7,7 0 0,1 7,7 v6 a7,7 0 0,1 −7,7 h−30 a7,7 0 0,1 −7,−7 v−6 a7,7 0 0,1 7,−7 Z` | `#1E1B4B` at 0.92. It covers `x 32..76`, `y 45..65` - both eyes live inside it. |
| Eyes | centres `(46,55)`, `(62,55)`, `rx=4.2`, `ry=5.4 × eyeOpen` (floor 0.4) | `#EDE9FE`. A glance moves the whole eye by `lookX × 0.9`; `lookY` moves the sparkle by `lookY × 1.2`. |
| Sparkles | `(cx + 1.4, cy − 2.4 + lookY × 1.2)`, `r = 1.5 × max(0.55, eyeOpen)` | White at 0.92, **on both eyes**. Dropped in `compact`. |
| Happy eyes | `M(cx − 4.2), (cy + 1) Q cx, (cy − 3.4) (cx + 4.2), (cy + 1)` | Drawn when `squint > 0.5`, stroke 2.1, `#EDE9FE`. |
| Cheeks | `(39,64)` and `(69,64)`, `rx=3 ry=2` | `rgba(240,171,252, 0.12 + cheek × 0.45)`. Outside the visor, where the icon puts them. |
| Mouth | `M48,70 Q54,(70 + mouthCurve × 4.5) 60,70` | Stroke `2.4 + mouthOpen × 1.8`, round caps, `#DDD6FE` (or `#FEF3C7` when amber). |
| Speaking well | rect `x=44 y=66.5 w=20 h=11 rx=5.5` | `#1E1B4B` at 0.95. This box exists so the bars never sit on the purple head. |
| Voice bars | 5 bars, width 2, height `2 + bar × 6`, centres 3.4 apart, centred `y=72` | White, opacity `0.82 + bar × 0.18`. Inside the well at every height. |
| Thinking dots | 3 circles at `y=72`, 5 apart, `r = 1.2 + dot × 1.1` | Replace the mouth while thinking. |
| Crest | `M58,12 L46,26 H52.5 L49,38 L63,22 H56.5 L60,12 Z` | `#C4B5FD` (lavender, as on the icon), or amber for `error`. Group opacity `0.3 + crest × 0.7`; a blurred copy plus a `#F5F3FF` outline when not `compact`. |
| Glow | circle `r=40` at `(54,54)` | Radial accent at `opacity = glow`. Dropped in `compact`. |

The whole group is transformed as
`rotate(tilt, 54, 54) translate(0, bob × 108)` - head tilt in degrees, nod in
fractions of the grid.

`compact` (24-32 px) drops the glow, the ear halos, the sparkles, the crest halo
and the crest outline. It keeps the shell, the visor, both eyes, both ears, the
crest and the five voice bars, because those are the parts that say who he is and
what he is doing.

## The contract

```ts
faceFor(state: FaceState, level = 0, tickMs = 0): JarvisExpression
faceStateFor(state: FaceState, reaction: 'happy' | 'error' | null): FaceState
levelToUnit(level: number): number        // 0..100 -> 0..1, NaN -> 0
blinkAt(tickMs: number): number           // 0..1, 140 ms of a 4200 ms cycle
voiceBar(tickMs: number, index: number): number
thinkingDot(tickMs: number, index: number): number
bounded(value: number, lo: number, hi: number): number
```

Rules a second renderer must keep:

1. **Pure.** The same `(state, level, tick)` always produces the same expression.
   No `Date.now()`, no `Math.random()`, no accumulation between frames.
2. **Bounded.** Every number stays in range (0..1, or −1..1 for `lookX`/`lookY`/
   `mouthCurve`, tilt within ±12). A renderer can multiply any field by a length
   without checking.
3. **Driven by a clock you pass in.** 30 fps is enough; the UI uses a
   `requestAnimationFrame` loop throttled to 33 ms. With `prefers-reduced-motion`
   the UI freezes `tickMs = 0` and draws one still frame - which is a valid,
   complete face, not a broken one.

## Expression channels

| Field | Range | Means |
|---|---|---|
| `eyeOpen` | 0..1 | Lid position. 0 is asleep, 1 is awake, above 1 is wide-eyed attention. |
| `lookX`, `lookY` | −1..1 | Where he is looking. There are no pupils to move, so `lookX` shifts the eye and `lookY` lifts the sparkle. |
| `squint` | 0..1 | Above 0.5 the ovals become `^ ^` arcs. |
| `mouthCurve` | −1..1 | −1 frown, 0 flat, +1 smile. |
| `mouthOpen` | 0..1 | Mouth thickness while silent; the mean of the bars while speaking. |
| `bars` | 0..1 each | Empty unless speaking. Five values, the voice. |
| `dots` | 0..1 each | Empty unless thinking. Three values. |
| `ear` | 0..1 | Side-accent light: how much he is paying attention. |
| `crest` | 0..1 | Crest brightness - idle shimmer, bright in attention, amber on trouble. |
| `cheek` | 0..1 | Rosiness. |
| `glow` | 0..1 | The soft halo behind the head. |
| `tilt` | −12..12 | Head rotation in degrees. Never more: he tilts, he does not loll. |
| `bob` | ±0.04 | Vertical nod, in fractions of the grid. |
| `accent` | `'accent' \| 'amber'` | Amber only for `error`; everything else uses the site accent. |

## The seven states

| State | What you see | Why |
|---|---|---|
| `idle` | Slow breathing bob, a blink every 4.2 s, crest shimmering, ears nearly out. | A face that is perfectly still looks switched off. |
| `listening` | Eyes wide (`1.12`) and still blinking, ears, crest, glow and mouth all following the microphone level, a small upward look. | He is leaning in. The ears and crest brighten with the room because that is the only feedback the person gets that the microphone is live. |
| `thinking` | Eyes narrowed to `0.82` and glancing side to side (`lookX = sin(t × 1.3) × 0.7`), three dots pulsing where the mouth was, a 2.5° sway. | Neck muscles move when a person thinks; a fixed stare reads as a hung app. |
| `speaking` | Five white bars in the dark well, a nod, a gentle sway, blink, ears and crest following the voice. | The bars are the proof that this is his voice and not a stuck screen. |
| `happy` | `^ ^` eyes, warm rosy cheeks, crest up, a wider smile. | The 1.8 s reaction after a reply. |
| `error` | Amber crest and mouth, a small **−5°** tilt, a shallower smile. | An apology, not an accusation. No brows, ever - the drawn brows are what made the helmet look mean. |
| `sleeping` | Eyes closed, faint breathing glow, crest dim. | Always-on tablets sit in this state all night. |

Reaction precedence (`faceStateFor`): `error` wins over everything; `listening`,
`thinking` and `speaking` outrank a `happy` flourish; `happy` only shows when the
face is otherwise `idle`. The UI shows `happy` for 1.8 s after a reply and `error`
for 2.4 s after a failure.

## Motion

- **30 fps.** Faster burns battery for motion nobody can see.
- **Blink:** 140 ms of every 4200 ms cycle, and it never stops while listening or
  speaking - a face that stops blinking reads as a frozen picture.
- **Voice bars:** `|sin(5.3t + 1.7i) + 0.6·sin(11.1t + 3.1i)| / 1.6`, clamped, with a
  floor of 0.12 so a quiet moment still shows a mouth. The two incommensurate
  frequencies keep the pattern from looking like a loop.
- **Thinking dots:** `0.3 + 0.7 × wave³` where `wave = (sin(3.4t − 0.9i) + 1) / 2`.
- **Reduced motion:** one still frame at `tickMs = 0`. Everything the face can say
  is still visible in that frame.

## Porting to another surface

1. **Copy the numbers, not the code.** The paths and constants above are the face.
   `jarvisFace.ts` is ~200 lines of arithmetic with no dependencies; port it
   directly or transpile once.
2. **Map the channels to whatever the panel can show.** An ESPHome OLED is 1-bit
   and 128 px wide: draw the shell, visor, eyes and a mouth, and pick the state by
   **shape** (eyes wide / eyes narrowed + dots / bars / `^ ^` / closed) rather than
   by colour. A watch face has colour but little room: shell + eyes + a 3-bar
   version of the voice bars is enough.
3. **Fake the motion you cannot run.** A microcontroller can advance `tickMs` from
   its own frame counter and call `faceFor` on every frame; that is the whole
   animation. If it cannot manage 30 fps, call it at 5 fps and stretch the blink
   window - a slow blink still reads as a blink.

Where it is used today:

| Surface | Renderer | State source |
|---|---|---|
| In-app assistant sheet | `services/ui/src/components/voice/JarvisHead.tsx` | `VoiceAssistantOverlay.tsx`: turn state, mic `level`, 1.8 s/2.4 s reactions |
| Android launcher icon and Quick Settings tile | `res/drawable/ic_launcher_foreground.xml`, `res/drawable/ic_jarvis_tile.xml` | static |

Not wired yet: the tablet/`Echo Show` kiosk and any ESPHome display. The
component takes a `size`, so a 240 px wall tablet and a 32 px header chip are the
same drawing.
