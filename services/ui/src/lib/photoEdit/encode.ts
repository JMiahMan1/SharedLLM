import { GIFEncoder, applyPalette, quantize } from 'gifenc';
import { applyRecipe, composite, outputSize, type EditFrame, type Limits } from './apply';
import { makeCanvas, PhotoEditError, releaseCanvas, STILL_LIMITS, type Decoded } from './decode';
import { downscale, type Frame } from './geometry';
import { isIdentity, type EditRecipe } from './recipe';
import { renderOverlayLayer } from './render';

/**
 * Turning edited frames back into a file: a still becomes a JPEG (or a PNG
 * when it has see-through parts), an animation an animated GIF with every
 * frame's own timing (gifenc, MIT, a few KB: Canvas cannot write a GIF).
 *
 * A result over the caller's byte limit is made again smaller (three tries),
 * then refused with a message; it is never cut.
 */

export interface EditResult {
  /** The edited picture. */
  blob: Blob;
  mime: string;
  width: number;
  height: number;
  animated: boolean;
  /** What was done, to start again from `original` later. */
  recipe: EditRecipe;
  /** The picture as it came in, untouched. */
  original: Blob;
}

export type Progress = (stage: 'decode' | 'edit' | 'encode', done: number, total: number) => void;

function hasTransparency(frame: Frame): boolean {
  for (let i = 3; i < frame.data.length; i += 4) if (frame.data[i] < 250) return true;
  return false;
}

async function stillBlob(frame: Frame, mime: 'image/jpeg' | 'image/png', quality: number): Promise<Blob> {
  const c = makeCanvas(frame.width, frame.height);
  try {
    c.ctx.putImageData(new ImageData(new Uint8ClampedArray(frame.data), frame.width, frame.height), 0, 0);
    if ('convertToBlob' in c.canvas) return await c.canvas.convertToBlob({ type: mime, quality });
    const canvas = c.canvas as HTMLCanvasElement;
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, mime, quality));
    if (!blob) throw new PhotoEditError('Could not make the edited picture.');
    return blob;
  } finally {
    releaseCanvas(c);
  }
}

/** One frame's palette and pixels for the GIF; see-through pixels get their own palette entry. */
function gifFrame(frame: Frame): { index: Uint8Array; palette: number[][]; transparentIndex: number } {
  if (hasTransparency(frame)) {
    const palette = quantize(frame.data, 256, { format: 'rgba4444', oneBitAlpha: true });
    const index = applyPalette(frame.data, palette, 'rgba4444');
    return { index, palette, transparentIndex: palette.findIndex((p) => p[3] === 0) };
  }
  const palette = quantize(frame.data, 256);
  return { index: applyPalette(frame.data, palette), palette, transparentIndex: -1 };
}

export async function encodeGif(frames: readonly EditFrame[], onProgress?: (done: number, total: number) => void): Promise<Uint8Array> {
  const gif = GIFEncoder();
  for (const [i, f] of frames.entries()) {
    const { index, palette, transparentIndex } = gifFrame(f);
    gif.writeFrame(index, f.width, f.height, {
      palette,
      delay: f.duration,
      ...(i === 0 ? { repeat: 0 } : {}),
      ...(transparentIndex >= 0 ? { transparent: true, transparentIndex, dispose: 2 } : {}),
    });
    onProgress?.(i + 1, frames.length);
    await new Promise((r) => setTimeout(r, 0));
  }
  gif.finish();
  return gif.bytes();
}

/** Each try is this much of the size before it, when a result is over the byte limit. */
const RETRY_SCALES = [1, 0.75, 0.55];

/**
 * Make the edited picture from what `decodePicture` read. `original` is
 * returned untouched with the result. Throws PhotoEditError with a message
 * fit to show when the result cannot be made within `maxBytes`.
 */
export async function exportEdit(
  decoded: Decoded,
  recipe: EditRecipe,
  original: Blob,
  options: { maxBytes: number; onProgress?: Progress },
): Promise<EditResult> {
  const first = decoded.frames[0];
  const animated = decoded.frames.length > 1;
  const base: Limits = animated ? { maxSide: Math.max(first.width, first.height), maxPixels: first.width * first.height } : STILL_LIMITS;
  for (const factor of RETRY_SCALES) {
    const limits: Limits = { maxSide: Math.max(1, Math.round(base.maxSide * factor)), maxPixels: Math.max(1, Math.round(base.maxPixels * factor * factor)) };
    const size = outputSize(first.width, first.height, recipe, limits);
    const layer = await renderOverlayLayer(recipe.overlays, size.width, size.height);
    const edited: EditFrame[] = [];
    for (const [i, f] of decoded.frames.entries()) {
      let [out] = applyRecipe([f], recipe, { limits, overlayLayer: null });
      // Rounding can leave a frame a pixel off the planned size; the layer must match exactly.
      if (out.width !== size.width || out.height !== size.height) out = { ...downscale(out, size.width, size.height), duration: out.duration };
      if (layer) composite(out, layer);
      edited.push(out);
      options.onProgress?.('edit', i + 1, decoded.frames.length);
      if (animated && i % 4 === 3) await new Promise((r) => setTimeout(r, 0));
    }
    let blob: Blob;
    let mime: string;
    if (animated) {
      const bytes = await encodeGif(edited, (done, total) => options.onProgress?.('encode', done, total));
      blob = new Blob([bytes as Uint8Array<ArrayBuffer>], { type: 'image/gif' });
      mime = 'image/gif';
    } else {
      const png = hasTransparency(edited[0]);
      mime = png ? 'image/png' : 'image/jpeg';
      blob = await stillBlob(edited[0], png ? 'image/png' : 'image/jpeg', 0.9);
      if (!png && blob.size > options.maxBytes) blob = await stillBlob(edited[0], 'image/jpeg', 0.75);
    }
    if (blob.size <= options.maxBytes) {
      return { blob, mime, width: edited[0].width, height: edited[0].height, animated, recipe: structuredClone(recipe), original };
    }
  }
  const mb = Math.round(options.maxBytes / (1024 * 1024));
  throw new PhotoEditError(`The edited picture would be over ${mb} MB. Try a shorter crop, or send it as it is.`);
}

/** Whether exporting is needed at all: an edit that changes nothing sends the original. */
export function needsExport(recipe: EditRecipe): boolean {
  return !isIdentity(recipe);
}
