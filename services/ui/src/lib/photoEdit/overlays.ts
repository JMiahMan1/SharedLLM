import { LENS_SIZE } from './lenses';
import type { DrawingOverlay, EditRecipe, FontId, Overlay, PlacedOverlay, Point, Stroke } from './recipe';

/**
 * Words, emoji, stickers and drawing on top of a picture: their sizes, the
 * fonts and colours on offer, the bundled stickers, and the touch arithmetic
 * (drag, pinch to resize and turn, drop on the bin). Positions are fractions
 * of the finished picture, so the same recipe fits the preview and the full
 * size export.
 */

/** Base sizes as fractions of the picture's width, before an overlay's own scale. */
export const TEXT_SIZE = 0.075;
export const EMOJI_SIZE = 0.16;
export const STICKER_SIZE = 0.24;

/**
 * How big an overlay is, in pixels: the words' type size, the emoji's, or the
 * width of a sticker's or a lens's picture.
 */
export function overlaySize(o: PlacedOverlay, width: number): number {
  const base = o.kind === 'text' ? TEXT_SIZE : o.kind === 'emoji' ? EMOJI_SIZE : o.kind === 'lens' ? LENS_SIZE : STICKER_SIZE;
  return base * width * o.scale;
}

/**
 * Font families: the phone's own (generic families every browser and Android
 * WebView maps to a font it already has). No web font is fetched, so nothing
 * about the picture or the person reaches a font server.
 */
export const FONTS: ReadonlyArray<{ id: FontId; label: string; family: string; weight: number }> = [
  { id: 'sans', label: 'Clean', family: 'system-ui, "Segoe UI", Roboto, Arial, sans-serif', weight: 700 },
  { id: 'serif', label: 'Classic', family: 'Georgia, "Noto Serif", "Times New Roman", serif', weight: 700 },
  { id: 'rounded', label: 'Bold', family: '"Arial Black", "Roboto Black", Impact, system-ui, sans-serif', weight: 900 },
  { id: 'mono', label: 'Typewriter', family: '"Courier New", "Roboto Mono", "Droid Sans Mono", monospace', weight: 700 },
  { id: 'hand', label: 'Script', family: '"Segoe Script", "Brush Script MT", "Dancing Script", cursive', weight: 400 },
];

export function fontFor(id: FontId): { family: string; weight: number } {
  return FONTS.find((f) => f.id === id) ?? FONTS[0];
}

export const COLORS: ReadonlyArray<{ value: string; label: string }> = [
  { value: '#ffffff', label: 'White' },
  { value: '#111827', label: 'Black' },
  { value: '#ef4444', label: 'Red' },
  { value: '#f97316', label: 'Orange' },
  { value: '#facc15', label: 'Yellow' },
  { value: '#22c55e', label: 'Green' },
  { value: '#3b82f6', label: 'Blue' },
  { value: '#a855f7', label: 'Purple' },
  { value: '#ec4899', label: 'Pink' },
];

/** Black or white, whichever reads better on `hex` (for a text pill). */
export function contrastOn(hex: string): string {
  const n = parseInt(hex.slice(1), 16);
  const r = (n >> 16) & 255;
  const g = (n >> 8) & 255;
  const b = n & 255;
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 150 ? '#111827' : '#ffffff';
}

export const EMOJI: readonly string[] = ['❤️', '😍', '😂', '🥰', '😎', '🎉', '✨', '🌸', '🌞', '🌈', '⭐', '🔥', '👍', '🙏', '🐶', '🐱', '🍕', '☕', '🎂', '🏖️'];

/**
 * The bundled stickers: simple shapes drawn here, nothing copied from anyone.
 * Each is a complete SVG document with a 100x100 view box.
 */
export const STICKERS: ReadonlyArray<{ id: string; label: string; svg: string }> = [
  {
    id: 'heart',
    label: 'Heart',
    svg: '<path d="M50 88 C20 66 6 50 6 32 C6 18 17 8 30 8 C39 8 46 13 50 21 C54 13 61 8 70 8 C83 8 94 18 94 32 C94 50 80 66 50 88Z" fill="#ef4444" stroke="#fff" stroke-width="5"/>',
  },
  {
    id: 'star',
    label: 'Star',
    svg: '<path d="M50 6 L62 37 L95 38 L69 58 L78 91 L50 72 L22 91 L31 58 L5 38 L38 37Z" fill="#facc15" stroke="#fff" stroke-width="5" stroke-linejoin="round"/>',
  },
  {
    id: 'sun',
    label: 'Sun',
    svg: '<g stroke="#f59e0b" stroke-width="7" stroke-linecap="round"><path d="M50 4v14M50 82v14M4 50h14M82 50h14M17 17l10 10M73 73l10 10M17 83l10-10M73 27l10-10"/></g><circle cx="50" cy="50" r="22" fill="#facc15" stroke="#fff" stroke-width="4"/>',
  },
  {
    id: 'flower',
    label: 'Flower',
    svg: '<g fill="#f9a8d4" stroke="#fff" stroke-width="3"><circle cx="50" cy="24" r="17"/><circle cx="76" cy="44" r="17"/><circle cx="66" cy="74" r="17"/><circle cx="34" cy="74" r="17"/><circle cx="24" cy="44" r="17"/></g><circle cx="50" cy="52" r="13" fill="#facc15"/>',
  },
  {
    id: 'rainbow',
    label: 'Rainbow',
    svg: '<g fill="none" stroke-width="9" stroke-linecap="round"><path d="M10 78a40 40 0 0 1 80 0" stroke="#ef4444"/><path d="M19 78a31 31 0 0 1 62 0" stroke="#f97316"/><path d="M28 78a22 22 0 0 1 44 0" stroke="#facc15"/><path d="M37 78a13 13 0 0 1 26 0" stroke="#22c55e"/></g>',
  },
  {
    id: 'cloud',
    label: 'Cloud',
    svg: '<path d="M26 76 C12 76 6 66 6 58 C6 48 14 41 24 42 C26 28 38 20 50 22 C60 14 78 20 80 36 C90 36 96 46 96 55 C96 67 87 76 76 76Z" fill="#fff" stroke="#93c5fd" stroke-width="4"/>',
  },
  {
    id: 'sparkle',
    label: 'Sparkle',
    svg: '<path d="M50 4 C54 34 66 46 96 50 C66 54 54 66 50 96 C46 66 34 54 4 50 C34 46 46 34 50 4Z" fill="#fde68a" stroke="#fff" stroke-width="4" stroke-linejoin="round"/>',
  },
  {
    id: 'smile',
    label: 'Smile',
    svg: '<circle cx="50" cy="50" r="44" fill="#facc15" stroke="#fff" stroke-width="5"/><circle cx="36" cy="40" r="6" fill="#111827"/><circle cx="64" cy="40" r="6" fill="#111827"/><path d="M30 60 Q50 80 70 60" fill="none" stroke="#111827" stroke-width="6" stroke-linecap="round"/>',
  },
  {
    id: 'balloon',
    label: 'Balloon',
    svg: '<path d="M50 72 C70 72 82 54 82 36 C82 18 68 6 50 6 C32 6 18 18 18 36 C18 54 30 72 50 72Z" fill="#3b82f6" stroke="#fff" stroke-width="4"/><path d="M46 72 L54 72 L50 79Z" fill="#3b82f6"/><path d="M50 79 C44 86 56 90 50 97" fill="none" stroke="#fff" stroke-width="3"/>',
  },
  {
    id: 'leaf',
    label: 'Leaf',
    svg: '<path d="M14 86 C14 40 40 12 90 10 C88 60 60 86 14 86Z" fill="#22c55e" stroke="#fff" stroke-width="4"/><path d="M18 82 C40 60 58 42 78 22" fill="none" stroke="#dcfce7" stroke-width="4" stroke-linecap="round"/>',
  },
];

export function stickerSvg(id: string): string | null {
  const s = STICKERS.find((x) => x.id === id);
  return s ? `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">${s.svg}</svg>` : null;
}

export function stickerDataUrl(id: string): string | null {
  const svg = stickerSvg(id);
  return svg ? `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}` : null;
}

// --- Editing the overlay list ------------------------------------------------------

let counter = 0;
export function newOverlayId(): string {
  counter += 1;
  return `o${Date.now().toString(36)}${counter.toString(36)}`;
}

export function replaceOverlay(recipe: EditRecipe, id: string, change: (o: Overlay) => Overlay): EditRecipe {
  return { ...recipe, overlays: recipe.overlays.map((o) => (o.id === id ? change(o) : o)) };
}

export function removeOverlay(recipe: EditRecipe, id: string): EditRecipe {
  return { ...recipe, overlays: recipe.overlays.filter((o) => o.id !== id) };
}

/** Bring an overlay to the top (the last drawn). */
export function raiseOverlay(recipe: EditRecipe, id: string): EditRecipe {
  const o = recipe.overlays.find((x) => x.id === id);
  if (!o) return recipe;
  return { ...recipe, overlays: [...recipe.overlays.filter((x) => x.id !== id), o] };
}

/** The drawing layer (there is one; it is made on the first stroke and stays at the bottom of the overlays). */
export function drawingOf(recipe: EditRecipe): DrawingOverlay | null {
  return (recipe.overlays.find((o) => o.kind === 'drawing') as DrawingOverlay | undefined) ?? null;
}

export function addStroke(recipe: EditRecipe, stroke: Stroke): EditRecipe {
  const drawing = drawingOf(recipe);
  if (!drawing) return { ...recipe, overlays: [{ id: 'drawing', kind: 'drawing', strokes: [stroke] }, ...recipe.overlays] };
  return replaceOverlay(recipe, drawing.id, () => ({ ...drawing, strokes: [...drawing.strokes, stroke] }));
}

export function undoStroke(recipe: EditRecipe): EditRecipe {
  const drawing = drawingOf(recipe);
  if (!drawing || !drawing.strokes.length) return recipe;
  return replaceOverlay(recipe, drawing.id, () => ({ ...drawing, strokes: drawing.strokes.slice(0, -1) }));
}

/** Drop points closer than `min` (fractions of the picture) to the last kept one: a finger sends far more than a line needs. */
export function thinPoints(points: Point[], min = 0.004): Point[] {
  const out: Point[] = [];
  for (const p of points) {
    const last = out[out.length - 1];
    if (!last || Math.hypot(p[0] - last[0], p[1] - last[1]) >= min) out.push(p);
  }
  const end = points[points.length - 1];
  if (end && out[out.length - 1] !== end && out.length > 0) out.push(end);
  return out;
}

// --- Touch --------------------------------------------------------------------------

export interface Touch2 {
  a: Point;
  b: Point;
}

/**
 * From two fingers at the start of a pinch to where they are now: how much
 * bigger, how far turned (degrees, clockwise on screen) and how far the
 * middle between them moved.
 */
export function pinchChange(start: Touch2, now: Touch2): { scale: number; rotation: number; dx: number; dy: number } {
  const d0 = Math.hypot(start.b[0] - start.a[0], start.b[1] - start.a[1]) || 1;
  const d1 = Math.hypot(now.b[0] - now.a[0], now.b[1] - now.a[1]) || d0;
  const a0 = Math.atan2(start.b[1] - start.a[1], start.b[0] - start.a[0]);
  const a1 = Math.atan2(now.b[1] - now.a[1], now.b[0] - now.a[0]);
  let turn = ((a1 - a0) * 180) / Math.PI;
  if (turn > 180) turn -= 360;
  if (turn < -180) turn += 360;
  return {
    scale: d1 / d0,
    rotation: turn,
    dx: (now.a[0] + now.b[0]) / 2 - (start.a[0] + start.b[0]) / 2,
    dy: (now.a[1] + now.b[1]) / 2 - (start.a[1] + start.b[1]) / 2,
  };
}

export const MIN_SCALE = 0.25;
export const MAX_SCALE = 6;

export function clampScale(scale: number): number {
  return Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale));
}

/** Whether a point (screen pixels) is over the bin's box. */
export function overBin(x: number, y: number, bin: { left: number; top: number; right: number; bottom: number } | null): boolean {
  return !!bin && x >= bin.left && x <= bin.right && y >= bin.top && y <= bin.bottom;
}
