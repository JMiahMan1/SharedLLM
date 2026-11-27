import type { AspectId, CropRect, Rotation } from './recipe';

/**
 * Turning, flipping, cropping and shrinking pictures, as plain pixel work on
 * RGBA arrays (the same shape as ImageData), plus the crop box arithmetic the
 * editor's handles use. Nothing here needs a canvas, so all of it is tested
 * directly.
 */

export interface Frame {
  width: number;
  height: number;
  data: Uint8ClampedArray;
}

export function makeFrame(width: number, height: number): Frame {
  return { width, height, data: new Uint8ClampedArray(width * height * 4) };
}

export function orientedSize(width: number, height: number, rotate: Rotation): { width: number; height: number } {
  return rotate === 90 || rotate === 270 ? { width: height, height: width } : { width, height };
}

/** Rotate (clockwise) then flip. Returns the input itself when nothing changes. */
export function orient(frame: Frame, rotate: Rotation, flipH: boolean, flipV: boolean): Frame {
  if (rotate === 0 && !flipH && !flipV) return frame;
  const { width: sw, height: sh, data: src } = frame;
  const { width: dw, height: dh } = orientedSize(sw, sh, rotate);
  const out = makeFrame(dw, dh);
  const dst = new Uint32Array(out.data.buffer);
  const from = src.byteOffset % 4 === 0 ? new Uint32Array(src.buffer, src.byteOffset, sw * sh) : new Uint32Array(src.slice().buffer);
  for (let y = 0; y < dh; y += 1) {
    const ty = flipV ? dh - 1 - y : y;
    for (let x = 0; x < dw; x += 1) {
      const tx = flipH ? dw - 1 - x : x;
      // (tx, ty) in the rotated picture comes from (sx, sy) in the source.
      let sx: number;
      let sy: number;
      if (rotate === 90) {
        sx = ty;
        sy = sh - 1 - tx;
      } else if (rotate === 180) {
        sx = sw - 1 - tx;
        sy = sh - 1 - ty;
      } else if (rotate === 270) {
        sx = sw - 1 - ty;
        sy = tx;
      } else {
        sx = tx;
        sy = ty;
      }
      dst[y * dw + x] = from[sy * sw + sx];
    }
  }
  return out;
}

/** The crop in whole pixels of a picture this size (at least one pixel each way). */
export function cropPixelsRect(crop: CropRect, width: number, height: number): { x: number; y: number; w: number; h: number } {
  const x = Math.min(width - 1, Math.max(0, Math.round(crop.x * width)));
  const y = Math.min(height - 1, Math.max(0, Math.round(crop.y * height)));
  const w = Math.max(1, Math.min(width - x, Math.round(crop.w * width)));
  const h = Math.max(1, Math.min(height - y, Math.round(crop.h * height)));
  return { x, y, w, h };
}

export function cropFrame(frame: Frame, crop: CropRect | null): Frame {
  if (!crop) return frame;
  const r = cropPixelsRect(crop, frame.width, frame.height);
  if (r.x === 0 && r.y === 0 && r.w === frame.width && r.h === frame.height) return frame;
  const out = makeFrame(r.w, r.h);
  for (let y = 0; y < r.h; y += 1) {
    const start = ((r.y + y) * frame.width + r.x) * 4;
    out.data.set(frame.data.subarray(start, start + r.w * 4), y * r.w * 4);
  }
  return out;
}

/** The scale (at most 1) that brings a picture within a longest side and a pixel count. */
export function fitScale(width: number, height: number, maxSide: number, maxPixels: number): number {
  const bySide = maxSide / Math.max(width, height, 1);
  const byPixels = Math.sqrt(maxPixels / Math.max(1, width * height));
  return Math.min(1, bySide, byPixels);
}

/** Shrink by averaging the source pixels under each output pixel (an area filter: no shimmer). */
export function downscale(frame: Frame, width: number, height: number): Frame {
  if (width >= frame.width && height >= frame.height) return frame;
  const out = makeFrame(width, height);
  const fx = frame.width / width;
  const fy = frame.height / height;
  const src = frame.data;
  for (let y = 0; y < height; y += 1) {
    const y0 = Math.floor(y * fy);
    const y1 = Math.max(y0 + 1, Math.min(frame.height, Math.floor((y + 1) * fy)));
    for (let x = 0; x < width; x += 1) {
      const x0 = Math.floor(x * fx);
      const x1 = Math.max(x0 + 1, Math.min(frame.width, Math.floor((x + 1) * fx)));
      let r = 0;
      let g = 0;
      let b = 0;
      let a = 0;
      for (let sy = y0; sy < y1; sy += 1) {
        let i = (sy * frame.width + x0) * 4;
        for (let sx = x0; sx < x1; sx += 1, i += 4) {
          r += src[i];
          g += src[i + 1];
          b += src[i + 2];
          a += src[i + 3];
        }
      }
      const n = (y1 - y0) * (x1 - x0);
      const o = (y * width + x) * 4;
      out.data[o] = r / n;
      out.data[o + 1] = g / n;
      out.data[o + 2] = b / n;
      out.data[o + 3] = a / n;
    }
  }
  return out;
}

// --- The crop box -----------------------------------------------------------------

export const ASPECTS: ReadonlyArray<{ id: AspectId; label: string; ratio: number | null }> = [
  { id: 'free', label: 'Free', ratio: null },
  { id: 'square', label: 'Square', ratio: 1 },
  { id: 'portrait', label: '4:5', ratio: 4 / 5 },
  { id: 'wide', label: '16:9', ratio: 16 / 9 },
];

export function aspectRatio(id: AspectId): number | null {
  return ASPECTS.find((a) => a.id === id)?.ratio ?? null;
}

/** The smallest crop side, as a fraction of the picture. */
export const MIN_CROP = 0.08;

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/** The largest centred crop of `ratio` (width/height in pixels) on a picture this shape; the whole picture for free. */
export function fitCrop(ratio: number | null, width: number, height: number): CropRect {
  if (!ratio) return { x: 0, y: 0, w: 1, h: 1 };
  const pictureRatio = width / height;
  if (ratio > pictureRatio) {
    const h = pictureRatio / ratio;
    return { x: 0, y: (1 - h) / 2, w: 1, h };
  }
  const w = ratio / pictureRatio;
  return { x: (1 - w) / 2, y: 0, w, h: 1 };
}

/** Slide the box, keeping it on the picture. */
export function moveCrop(rect: CropRect, dx: number, dy: number): CropRect {
  return { ...rect, x: clamp(rect.x + dx, 0, 1 - rect.w), y: clamp(rect.y + dy, 0, 1 - rect.h) };
}

export type Corner = 'nw' | 'ne' | 'sw' | 'se';

/**
 * Drag one corner by (dx, dy) (fractions of the picture), the opposite corner
 * staying put. With a ratio the box keeps its shape (in pixels, so the
 * picture's own width/height counts), following the larger of the two moves.
 */
export function resizeCrop(rect: CropRect, corner: Corner, dx: number, dy: number, ratio: number | null, width: number, height: number): CropRect {
  const west = corner === 'nw' || corner === 'sw';
  const north = corner === 'nw' || corner === 'ne';
  // The fixed corner.
  const fx = west ? rect.x + rect.w : rect.x;
  const fy = north ? rect.y + rect.h : rect.y;
  // The room there is towards the dragged side.
  const roomW = west ? fx : 1 - fx;
  const roomH = north ? fy : 1 - fy;
  let w = clamp(rect.w + (west ? -dx : dx), MIN_CROP, roomW);
  let h = clamp(rect.h + (north ? -dy : dy), MIN_CROP, roomH);
  if (ratio) {
    // r = (w * width) / (h * height): keep it, following whichever side moved more.
    const k = (ratio * height) / width;
    const growW = Math.abs(dx) * width >= Math.abs(dy) * height;
    if (growW) h = w / k;
    else w = h * k;
    // Too big for the room or too small: scale both back together.
    const shrink = Math.min(1, roomW / w, roomH / h);
    w *= shrink;
    h *= shrink;
    const grow = Math.max(1, MIN_CROP / w, MIN_CROP / h);
    if (grow > 1 && w * grow <= roomW && h * grow <= roomH) {
      w *= grow;
      h *= grow;
    }
  }
  return { x: west ? fx - w : fx, y: north ? fy - h : fy, w, h };
}

/** A crop that covers the whole picture is no crop at all. */
export function normaliseCrop(rect: CropRect | null): CropRect | null {
  if (!rect) return null;
  const whole = rect.x <= 0.0005 && rect.y <= 0.0005 && rect.w >= 0.999 && rect.h >= 0.999;
  return whole ? null : rect;
}
