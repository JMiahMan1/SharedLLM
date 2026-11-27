import { applyLook, lookFor } from './filters';
import { cropFrame, cropPixelsRect, downscale, fitScale, orient, orientedSize, type Frame } from './geometry';
import type { EditRecipe } from './recipe';

/**
 * Apply a recipe to a picture's frames: one frame for a still, every frame of
 * an animation. Pure: frames in, new frames out, the inputs untouched (so the
 * original stays exactly as it was). Overlays are drawn once by the caller
 * into an RGBA layer the size of the result (`overlayLayer`, see render.ts)
 * and laid over every frame here, so an animation gets the same words and
 * stickers on each frame.
 */

export interface EditFrame extends Frame {
  /** How long the frame shows, in milliseconds (0 for a still). */
  duration: number;
}

export interface Limits {
  /** The longest side of the result. */
  maxSide: number;
  /** The most pixels in one frame of the result. */
  maxPixels: number;
}

/** The size the result will be: oriented, cropped, then brought within the limits. */
export function outputSize(width: number, height: number, recipe: EditRecipe, limits: Limits): { width: number; height: number } {
  const o = orientedSize(width, height, recipe.rotate);
  const c = recipe.crop ? cropPixelsRect(recipe.crop, o.width, o.height) : { w: o.width, h: o.height };
  const s = fitScale(c.w, c.h, limits.maxSide, limits.maxPixels);
  return { width: Math.max(1, Math.round(c.w * s)), height: Math.max(1, Math.round(c.h * s)) };
}

/** Lay `layer` over `frame` (same size) in place, ordinary alpha blending. */
export function composite(frame: Frame, layer: Frame): Frame {
  if (layer.width !== frame.width || layer.height !== frame.height) throw new Error('The overlay layer is not the size of the picture.');
  const d = frame.data;
  const s = layer.data;
  for (let i = 0; i < d.length; i += 4) {
    const a = s[i + 3];
    if (!a) continue;
    if (a === 255) {
      d[i] = s[i];
      d[i + 1] = s[i + 1];
      d[i + 2] = s[i + 2];
      d[i + 3] = 255;
      continue;
    }
    const k = a / 255;
    const da = d[i + 3] / 255;
    const outA = k + da * (1 - k);
    d[i] = (s[i] * k + d[i] * da * (1 - k)) / (outA || 1);
    d[i + 1] = (s[i + 1] * k + d[i + 1] * da * (1 - k)) / (outA || 1);
    d[i + 2] = (s[i + 2] * k + d[i + 2] * da * (1 - k)) / (outA || 1);
    d[i + 3] = outA * 255;
  }
  return frame;
}

/** Copy, so the colour work never writes into a frame the caller still holds. */
function own(frame: Frame, original: Frame): Frame {
  return frame === original ? { width: frame.width, height: frame.height, data: new Uint8ClampedArray(frame.data) } : frame;
}

export function applyRecipe(
  frames: readonly EditFrame[],
  recipe: EditRecipe,
  options: { limits?: Limits; overlayLayer?: Frame | null } = {},
): EditFrame[] {
  const look = lookFor(recipe.filter, recipe.adjust);
  return frames.map((input) => {
    let f: Frame = orient(input, recipe.rotate, recipe.flipH, recipe.flipV);
    f = cropFrame(f, recipe.crop);
    if (options.limits) {
      const s = fitScale(f.width, f.height, options.limits.maxSide, options.limits.maxPixels);
      if (s < 1) f = downscale(f, Math.max(1, Math.round(f.width * s)), Math.max(1, Math.round(f.height * s)));
    }
    f = own(f, input);
    applyLook(f.data, f.width, f.height, look);
    if (options.overlayLayer) composite(f, options.overlayLayer);
    return { ...f, duration: input.duration };
  });
}
