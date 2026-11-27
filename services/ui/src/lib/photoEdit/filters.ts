import type { Adjustments, FilterId } from './recipe';

/**
 * The colour side of an edit: one-tap looks and the adjustment sliders, both
 * reduced to one small parameter set (a "look") and applied to RGBA pixels in
 * plain JavaScript. No canvas filter strings (they differ between browsers
 * and WebViews) and nothing that leaves the device.
 *
 * A look is applied as: a 3x3 colour matrix (saturation, sepia), then a
 * per-channel curve held in a 256-entry table (warmth, tint, brightness,
 * contrast, S-curve, faded blacks), then a vignette and film grain that
 * depend on where the pixel is. Grain is a hash of the position, so every
 * frame of an animation gets the same grain and the same edit gives the same
 * bytes every time.
 */

export interface Look {
  /** Added to every channel, -1..1 (0 unchanged). */
  brightness: number;
  /** Contrast around mid grey: 0 unchanged, -1 flat, 1 twice as steep. */
  contrast: number;
  /** 0 unchanged, -1 grey, 1 twice as colourful. */
  saturation: number;
  /** Below 0 bluer, above 0 more orange. */
  warmth: number;
  /** Added per channel after the matrix. */
  tint: [number, number, number];
  /** Lifts the blacks (0..1), the washed look. */
  fade: number;
  /** Blend towards sepia (0..1). */
  sepia: number;
  /** An S-curve (0..1) for punch without touching the ends. */
  curve: number;
  /** How dark the corners get (0..1). */
  vignette: number;
  /** Film grain (0..1). */
  grain: number;
}

export const NEUTRAL_LOOK: Look = { brightness: 0, contrast: 0, saturation: 0, warmth: 0, tint: [0, 0, 0], fade: 0, sepia: 0, curve: 0, vignette: 0, grain: 0 };

export interface FilterDef {
  id: FilterId;
  label: string;
  look: Partial<Look>;
}

/** The one-tap looks, in the order the strip shows them. Names are plain and family-friendly. */
export const FILTERS: readonly FilterDef[] = [
  { id: 'warm', label: 'Warm', look: { warmth: 0.35, saturation: 0.1 } },
  { id: 'cool', label: 'Cool', look: { warmth: -0.35, saturation: 0.05 } },
  { id: 'vintage', label: 'Vintage', look: { sepia: 0.35, fade: 0.12, contrast: -0.1, vignette: 0.35, grain: 0.15 } },
  { id: 'mono', label: 'Mono', look: { saturation: -1 } },
  { id: 'noir', label: 'Noir', look: { saturation: -1, contrast: 0.35, curve: 0.4, vignette: 0.5, grain: 0.1 } },
  { id: 'vivid', label: 'Vivid', look: { saturation: 0.45, contrast: 0.15 } },
  { id: 'film', label: 'Film', look: { curve: 0.25, fade: 0.08, warmth: 0.1, saturation: -0.1, grain: 0.25 } },
  { id: 'fade', label: 'Fade', look: { fade: 0.25, contrast: -0.2, saturation: -0.25 } },
  { id: 'glow', label: 'Glow', look: { brightness: 0.12, fade: 0.1, contrast: -0.1, warmth: 0.12, curve: 0.1 } },
  { id: 'sunset', label: 'Sunset', look: { warmth: 0.5, tint: [0.05, 0, -0.03], saturation: 0.15, vignette: 0.2 } },
  { id: 'ocean', label: 'Ocean', look: { warmth: -0.4, tint: [-0.03, 0.03, 0.06], saturation: 0.1 } },
  { id: 'pop', label: 'Pop', look: { saturation: 0.6, contrast: 0.3, curve: 0.2 } },
];

export function filterDef(id: FilterId | null): FilterDef | null {
  return FILTERS.find((f) => f.id === id) ?? null;
}

/** The filter's look with the sliders on top. Slider ranges are -100..100 (vignette 0..100). */
export function lookFor(filter: FilterId | null, adjust: Adjustments): Look {
  const base = { ...NEUTRAL_LOOK, ...(filterDef(filter)?.look ?? {}) };
  return {
    ...base,
    brightness: base.brightness + (adjust.brightness / 100) * 0.4,
    contrast: base.contrast + (adjust.contrast / 100) * 0.6,
    saturation: Math.max(-1, base.saturation + adjust.saturation / 100),
    warmth: base.warmth + (adjust.warmth / 100) * 0.6,
    vignette: Math.min(1, base.vignette + (adjust.vignette / 100) * 0.8),
  };
}

export function isNeutral(look: Look): boolean {
  return (
    !look.brightness &&
    !look.contrast &&
    !look.saturation &&
    !look.warmth &&
    !look.tint[0] &&
    !look.tint[1] &&
    !look.tint[2] &&
    !look.fade &&
    !look.sepia &&
    !look.curve &&
    !look.vignette &&
    !look.grain
  );
}

const LUMA = [0.2126, 0.7152, 0.0722];
const SEPIA = [0.393, 0.769, 0.189, 0.349, 0.686, 0.168, 0.272, 0.534, 0.131];

/** The 3x3 colour matrix (row-major) for saturation and sepia. */
export function colorMatrix(look: Look): number[] {
  const s = 1 + look.saturation;
  const m: number[] = [];
  for (let row = 0; row < 3; row += 1) {
    for (let col = 0; col < 3; col += 1) m.push((1 - s) * LUMA[col] + (row === col ? s : 0));
  }
  if (look.sepia) {
    // Sepia of the saturated colour, blended in.
    const out: number[] = [];
    for (let row = 0; row < 3; row += 1) {
      for (let col = 0; col < 3; col += 1) {
        let sep = 0;
        for (let k = 0; k < 3; k += 1) sep += SEPIA[row * 3 + k] * m[k * 3 + col];
        out.push((1 - look.sepia) * m[row * 3 + col] + look.sepia * sep);
      }
    }
    return out;
  }
  return m;
}

const clamp01 = (v: number) => (v < 0 ? 0 : v > 1 ? 1 : v);

/** One channel's curve: warmth and tint, brightness, contrast, S-curve, faded blacks. */
export function curveValue(v: number, channel: 0 | 1 | 2, look: Look): number {
  const warm = channel === 0 ? 1 + look.warmth * 0.18 : channel === 2 ? 1 - look.warmth * 0.18 : 1 + look.warmth * 0.03;
  let x = v * warm + look.tint[channel] + look.brightness;
  x = (x - 0.5) * (1 + look.contrast) + 0.5;
  x = clamp01(x);
  if (look.curve) x += look.curve * (x * x * (3 - 2 * x) - x);
  if (look.fade) x = look.fade + x * (1 - look.fade * 1.15);
  return clamp01(x);
}

/** The curve for all three channels as one 768-entry table: red, then green, then blue. */
export function buildLut(look: Look): Uint8ClampedArray {
  const lut = new Uint8ClampedArray(768);
  for (let c = 0 as 0 | 1 | 2; c < 3; c = (c + 1) as 0 | 1 | 2) {
    for (let i = 0; i < 256; i += 1) lut[c * 256 + i] = Math.round(curveValue(i / 255, c, look) * 255);
  }
  return lut;
}

/** A repeatable value in -1..1 for a pixel position. */
export function grainAt(x: number, y: number): number {
  let h = (Math.imul(x, 374761393) + Math.imul(y, 668265263)) | 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  h ^= h >>> 16;
  return ((h & 0xffff) / 0xffff) * 2 - 1;
}

/**
 * Apply a look to RGBA pixels in place (alpha is kept). Returns the same array.
 * Pure apart from writing into `data`.
 */
export function applyLook(data: Uint8ClampedArray, width: number, height: number, look: Look): Uint8ClampedArray {
  if (isNeutral(look)) return data;
  const m = colorMatrix(look);
  const lut = buildLut(look);
  const identityMatrix = look.saturation === 0 && look.sepia === 0;
  const cx = width / 2;
  const cy = height / 2;
  // Corners at distance 1, measured on the picture's own shape.
  const inv = 1 / Math.sqrt(cx * cx + cy * cy || 1);
  const vig = look.vignette;
  const grain = look.grain * 28;
  for (let y = 0; y < height; y += 1) {
    const dy = (y + 0.5 - cy) * inv;
    for (let x = 0; x < width; x += 1) {
      const i = (y * width + x) * 4;
      let r = data[i];
      let g = data[i + 1];
      let b = data[i + 2];
      if (!identityMatrix) {
        const nr = m[0] * r + m[1] * g + m[2] * b;
        const ng = m[3] * r + m[4] * g + m[5] * b;
        const nb = m[6] * r + m[7] * g + m[8] * b;
        r = nr < 0 ? 0 : nr > 255 ? 255 : nr;
        g = ng < 0 ? 0 : ng > 255 ? 255 : ng;
        b = nb < 0 ? 0 : nb > 255 ? 255 : nb;
      }
      r = lut[r | 0];
      g = lut[256 + (g | 0)];
      b = lut[512 + (b | 0)];
      if (vig) {
        const dx = (x + 0.5 - cx) * inv;
        const d = Math.sqrt(dx * dx + dy * dy);
        const t = d <= 0.4 ? 0 : d >= 1 ? 1 : (d - 0.4) / 0.6;
        const k = 1 - vig * 0.85 * t * t * (3 - 2 * t);
        r *= k;
        g *= k;
        b *= k;
      }
      if (grain) {
        const n = grainAt(x, y) * grain;
        r += n;
        g += n;
        b += n;
      }
      data[i] = r;
      data[i + 1] = g;
      data[i + 2] = b;
    }
  }
  return data;
}
