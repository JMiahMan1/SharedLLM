/**
 * An edit, written down: what was done to a picture, never the picture itself.
 *
 * The editor never changes the original. It returns the edited picture and
 * this recipe, so whoever keeps the original (the private gallery, later) can
 * store "original + recipe + edited" and either start a new edit from the
 * original or put the original back. A recipe is plain JSON with nothing but
 * numbers, short strings and ids; `parseRecipe` checks one that comes back
 * from storage before it is trusted.
 *
 * Order of operations, always the same: orient (rotate, then flip), crop,
 * colour (filter, then adjustments), overlays.
 */

export const RECIPE_VERSION = 1;

export type FilterId =
  | 'warm'
  | 'cool'
  | 'vintage'
  | 'mono'
  | 'noir'
  | 'vivid'
  | 'film'
  | 'fade'
  | 'glow'
  | 'sunset'
  | 'ocean'
  | 'pop';

export interface Adjustments {
  /** -100..100, 0 is unchanged. */
  brightness: number;
  contrast: number;
  saturation: number;
  /** Below 0 is cooler (bluer), above warmer (more orange). */
  warmth: number;
  /** 0..100: how dark the corners get. */
  vignette: number;
}

export type Rotation = 0 | 90 | 180 | 270;

export type AspectId = 'free' | 'square' | 'portrait' | 'wide';

/** A crop, as fractions (0..1) of the oriented picture, so it fits any size of the same picture. */
export interface CropRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** A point as fractions (0..1) of the finished (cropped) picture. */
export type Point = [number, number];

interface Placed {
  id: string;
  /** Centre, as fractions of the finished picture. */
  x: number;
  y: number;
  /** 1 is the default size; text and emoji sizes are fractions of the picture's width times this. */
  scale: number;
  /** Degrees, clockwise. */
  rotation: number;
}

export type FontId = 'sans' | 'serif' | 'rounded' | 'mono' | 'hand';

export interface TextOverlay extends Placed {
  kind: 'text';
  text: string;
  font: FontId;
  color: string;
  /** A rounded background behind the words, in a colour that reads against `color`. */
  pill: boolean;
}

export interface EmojiOverlay extends Placed {
  kind: 'emoji';
  emoji: string;
}

export interface StickerOverlay extends Placed {
  kind: 'sticker';
  sticker: string;
}

export interface Stroke {
  color: string;
  /** Line width as a fraction of the finished picture's width. */
  width: number;
  points: Point[];
}

export interface DrawingOverlay {
  id: string;
  kind: 'drawing';
  strokes: Stroke[];
}

export type PlacedOverlay = TextOverlay | EmojiOverlay | StickerOverlay;
export type Overlay = PlacedOverlay | DrawingOverlay;

export interface EditRecipe {
  v: typeof RECIPE_VERSION;
  filter: FilterId | null;
  adjust: Adjustments;
  rotate: Rotation;
  flipH: boolean;
  flipV: boolean;
  aspect: AspectId;
  crop: CropRect | null;
  /** Drawn in order: the first is at the bottom. */
  overlays: Overlay[];
}

export const NO_ADJUSTMENTS: Adjustments = { brightness: 0, contrast: 0, saturation: 0, warmth: 0, vignette: 0 };

export function emptyRecipe(): EditRecipe {
  return { v: RECIPE_VERSION, filter: null, adjust: { ...NO_ADJUSTMENTS }, rotate: 0, flipH: false, flipV: false, aspect: 'free', crop: null, overlays: [] };
}

/** True when the recipe changes nothing: sending it is the same as sending the original. */
export function isIdentity(recipe: EditRecipe): boolean {
  const a = recipe.adjust;
  return (
    !recipe.filter &&
    !a.brightness &&
    !a.contrast &&
    !a.saturation &&
    !a.warmth &&
    !a.vignette &&
    recipe.rotate === 0 &&
    !recipe.flipH &&
    !recipe.flipV &&
    !recipe.crop &&
    !recipe.overlays.some((o) => o.kind !== 'drawing' || o.strokes.length > 0)
  );
}

export const FILTER_IDS: readonly FilterId[] = ['warm', 'cool', 'vintage', 'mono', 'noir', 'vivid', 'film', 'fade', 'glow', 'sunset', 'ocean', 'pop'];
const FONT_IDS: readonly FontId[] = ['sans', 'serif', 'rounded', 'mono', 'hand'];
const ASPECTS: readonly AspectId[] = ['free', 'square', 'portrait', 'wide'];

/** The most overlays and drawn points a recipe may carry, so a stored one cannot be made enormous. */
export const MAX_OVERLAYS = 60;
export const MAX_POINTS = 20_000;
export const MAX_TEXT = 200;

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
const num = (v: unknown, lo: number, hi: number, fallback: number) => (typeof v === 'number' && Number.isFinite(v) ? clamp(v, lo, hi) : fallback);
const COLOR = /^#[0-9a-f]{6}$/i;
const color = (v: unknown, fallback: string) => (typeof v === 'string' && COLOR.test(v) ? v.toLowerCase() : fallback);
const ID = /^[A-Za-z0-9_-]{1,40}$/;

function placed(o: Record<string, unknown>, index: number): Placed {
  return {
    id: typeof o.id === 'string' && ID.test(o.id) ? o.id : `o${index}`,
    x: num(o.x, -0.5, 1.5, 0.5),
    y: num(o.y, -0.5, 1.5, 0.5),
    scale: num(o.scale, 0.1, 10, 1),
    rotation: num(o.rotation, -3600, 3600, 0),
  };
}

function parseOverlay(raw: unknown, index: number, budget: { points: number }): Overlay | null {
  if (!raw || typeof raw !== 'object') return null;
  const o = raw as Record<string, unknown>;
  if (o.kind === 'text') {
    const text = typeof o.text === 'string' ? o.text.slice(0, MAX_TEXT) : '';
    if (!text.trim()) return null;
    const font = FONT_IDS.includes(o.font as FontId) ? (o.font as FontId) : 'sans';
    return { ...placed(o, index), kind: 'text', text, font, color: color(o.color, '#ffffff'), pill: o.pill === true };
  }
  if (o.kind === 'emoji') {
    const emoji = typeof o.emoji === 'string' ? Array.from(o.emoji).slice(0, 8).join('') : '';
    return emoji ? { ...placed(o, index), kind: 'emoji', emoji } : null;
  }
  if (o.kind === 'sticker') {
    return typeof o.sticker === 'string' && ID.test(o.sticker) ? { ...placed(o, index), kind: 'sticker', sticker: o.sticker } : null;
  }
  if (o.kind === 'drawing') {
    const strokes: Stroke[] = [];
    for (const s of Array.isArray(o.strokes) ? o.strokes : []) {
      if (!s || typeof s !== 'object') continue;
      const stroke = s as Record<string, unknown>;
      const points: Point[] = [];
      for (const p of Array.isArray(stroke.points) ? stroke.points : []) {
        if (budget.points <= 0) break;
        if (Array.isArray(p) && p.length === 2) {
          points.push([num(p[0], -0.5, 1.5, 0), num(p[1], -0.5, 1.5, 0)]);
          budget.points -= 1;
        }
      }
      if (points.length) strokes.push({ color: color(stroke.color, '#ffffff'), width: num(stroke.width, 0.001, 0.2, 0.01), points });
    }
    return { id: typeof o.id === 'string' && ID.test(o.id) ? o.id : `o${index}`, kind: 'drawing', strokes };
  }
  return null;
}

/**
 * A recipe read back from storage (or JSON text), checked field by field:
 * anything unknown or out of range falls back to "unchanged", never throws.
 * Returns null only for something that is not a recipe at all.
 */
export function parseRecipe(input: unknown): EditRecipe | null {
  let raw = input;
  if (typeof raw === 'string') {
    try {
      raw = JSON.parse(raw);
    } catch {
      return null;
    }
  }
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  if (r.v !== RECIPE_VERSION) return null;
  const a = (r.adjust && typeof r.adjust === 'object' ? r.adjust : {}) as Record<string, unknown>;
  const rotate = [0, 90, 180, 270].includes(r.rotate as number) ? (r.rotate as Rotation) : 0;
  let crop: CropRect | null = null;
  if (r.crop && typeof r.crop === 'object') {
    const c = r.crop as Record<string, unknown>;
    const x = num(c.x, 0, 1, 0);
    const y = num(c.y, 0, 1, 0);
    const w = num(c.w, 0.01, 1 - x, 1 - x);
    const h = num(c.h, 0.01, 1 - y, 1 - y);
    crop = x === 0 && y === 0 && w === 1 && h === 1 ? null : { x, y, w, h };
  }
  const budget = { points: MAX_POINTS };
  const overlays: Overlay[] = [];
  for (const [i, o] of (Array.isArray(r.overlays) ? r.overlays : []).slice(0, MAX_OVERLAYS).entries()) {
    const parsed = parseOverlay(o, i, budget);
    if (parsed) overlays.push(parsed);
  }
  return {
    v: RECIPE_VERSION,
    filter: FILTER_IDS.includes(r.filter as FilterId) ? (r.filter as FilterId) : null,
    adjust: {
      brightness: num(a.brightness, -100, 100, 0),
      contrast: num(a.contrast, -100, 100, 0),
      saturation: num(a.saturation, -100, 100, 0),
      warmth: num(a.warmth, -100, 100, 0),
      vignette: num(a.vignette, 0, 100, 0),
    },
    rotate,
    flipH: r.flipH === true,
    flipV: r.flipV === true,
    aspect: ASPECTS.includes(r.aspect as AspectId) ? (r.aspect as AspectId) : 'free',
    crop,
    overlays,
  };
}

/** Round to 4 decimals: a ten-thousandth of the picture is far below a pixel on any phone. */
const round4 = (v: number) => Math.round(v * 10_000) / 10_000;

/**
 * The same recipe with every position rounded to four decimals, so it is
 * as small as it can be when stored. Rounding changes nothing anyone could
 * see; `parseRecipe` reads the result back as the same edit.
 */
export function compactRecipe(recipe: EditRecipe): EditRecipe {
  const placed = <T extends PlacedOverlay>(o: T): T => ({ ...o, x: round4(o.x), y: round4(o.y), scale: round4(o.scale), rotation: round4(o.rotation) });
  return {
    ...recipe,
    adjust: { ...recipe.adjust },
    crop: recipe.crop ? { x: round4(recipe.crop.x), y: round4(recipe.crop.y), w: round4(recipe.crop.w), h: round4(recipe.crop.h) } : null,
    overlays: recipe.overlays.map((o) =>
      o.kind === 'drawing'
        ? { ...o, strokes: o.strokes.map((s) => ({ ...s, width: round4(s.width), points: s.points.map(([x, y]) => [round4(x), round4(y)] as Point) })) }
        : placed(o),
    ),
  };
}
