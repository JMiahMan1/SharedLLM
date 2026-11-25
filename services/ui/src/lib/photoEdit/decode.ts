import type { EditFrame } from './apply';
import { fitScale } from './geometry';

/**
 * Reading a picture into frames, on the phone: every frame of an animated GIF
 * or WebP with WebCodecs' ImageDecoder (Android's WebView and Chromium have
 * it), or the first frame only where it is missing, which the editor says.
 *
 * The work is capped so a phone never hangs on a long or huge animation:
 * at most MAX_FRAMES frames, and the frames shrunk so all of them together
 * stay under MAX_TOTAL_PIXELS. A still is read at most STILL_LIMITS.
 */

export const STILL_LIMITS = { maxSide: 4096, maxPixels: 8_000_000 };
export const MAX_FRAMES = 150;
export const MAX_TOTAL_PIXELS = 24_000_000;
export const ANIMATED_MAX_SIDE = 720;
/** Browsers show a GIF frame of 10 ms or less for 100 ms; so do we, so the edit plays at the same speed. */
export const MIN_FRAME_MS = 20;
export const DEFAULT_FRAME_MS = 100;

export interface Decoded {
  frames: EditFrame[];
  /** The source moves (more than one frame). */
  animated: boolean;
  /** It moves, but only its first frame could be read on this phone. */
  firstFrameOnly: boolean;
  /** Frames left out because of MAX_FRAMES. */
  droppedFrames: number;
  sourceWidth: number;
  sourceHeight: number;
}

export class PhotoEditError extends Error {}

/** Whether the bytes say "more than one frame" (a cheap look at the headers, without decoding). */
export function looksAnimated(bytes: Uint8Array, mime: string): boolean {
  const type = mime.toLowerCase();
  if (type === 'image/gif') {
    let controls = 0;
    for (let i = 0; i + 2 < bytes.length; i += 1) {
      if (bytes[i] === 0x21 && bytes[i + 1] === 0xf9 && bytes[i + 2] === 0x04) {
        controls += 1;
        if (controls > 1) return true;
      }
    }
    return false;
  }
  if (type === 'image/webp') {
    for (let i = 12; i + 4 <= Math.min(bytes.length, 4096); i += 1) {
      if (bytes[i] === 0x41 && bytes[i + 1] === 0x4e && bytes[i + 2] === 0x49 && bytes[i + 3] === 0x4d) return true; // ANIM
    }
    return false;
  }
  if (type === 'image/png' || type === 'image/apng') {
    for (let i = 8; i + 4 <= Math.min(bytes.length, 1 << 16); i += 1) {
      if (bytes[i] === 0x61 && bytes[i + 1] === 0x63 && bytes[i + 2] === 0x54 && bytes[i + 3] === 0x4c) return true; // acTL
      if (bytes[i] === 0x49 && bytes[i + 1] === 0x44 && bytes[i + 2] === 0x41 && bytes[i + 3] === 0x54) return false; // IDAT
    }
  }
  return false;
}

/** The size each frame is read at so that `frames` of them fit the budget. */
export function animatedFrameSize(width: number, height: number, frames: number): { width: number; height: number } {
  const perFrame = MAX_TOTAL_PIXELS / Math.max(1, Math.min(frames, MAX_FRAMES));
  const s = fitScale(width, height, ANIMATED_MAX_SIDE, perFrame);
  return { width: Math.max(1, Math.round(width * s)), height: Math.max(1, Math.round(height * s)) };
}

export function frameMs(durationMicros: number | null | undefined): number {
  const ms = (durationMicros ?? 0) / 1000;
  return ms > 10 ? Math.max(MIN_FRAME_MS, Math.round(ms)) : DEFAULT_FRAME_MS;
}

type Canvas2D = { canvas: HTMLCanvasElement | OffscreenCanvas; ctx: CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D };

export function makeCanvas(width: number, height: number): Canvas2D {
  if (typeof OffscreenCanvas !== 'undefined') {
    const canvas = new OffscreenCanvas(width, height);
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    if (ctx) return { canvas, ctx };
  }
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  if (!ctx) throw new PhotoEditError('This phone could not open the picture for editing.');
  return { canvas, ctx };
}

/** Let go of a canvas's memory now rather than whenever the collector gets to it. */
export function releaseCanvas(c: Canvas2D): void {
  c.canvas.width = 0;
  c.canvas.height = 0;
}

function grab(c: Canvas2D, source: CanvasImageSource, width: number, height: number, duration: number): EditFrame {
  c.ctx.clearRect(0, 0, width, height);
  c.ctx.drawImage(source, 0, 0, width, height);
  const img = c.ctx.getImageData(0, 0, width, height);
  return { width, height, data: img.data, duration };
}

async function decodeAll(blob: Blob, onProgress?: (done: number, total: number) => void, signal?: AbortSignal): Promise<Decoded | null> {
  if (typeof ImageDecoder === 'undefined') return null;
  try {
    if (!(await ImageDecoder.isTypeSupported(blob.type))) return null;
  } catch {
    return null;
  }
  const decoder = new ImageDecoder({ data: await blob.arrayBuffer(), type: blob.type });
  let canvas: Canvas2D | null = null;
  try {
    await decoder.tracks.ready;
    await decoder.completed.catch(() => {});
    const track = decoder.tracks.selectedTrack;
    const count = Math.max(1, track?.frameCount ?? 1);
    if (count < 2) return null;
    const take = Math.min(count, MAX_FRAMES);
    const frames: EditFrame[] = [];
    let size: { width: number; height: number } | null = null;
    let sourceWidth = 0;
    let sourceHeight = 0;
    for (let i = 0; i < take; i += 1) {
      if (signal?.aborted) throw new PhotoEditError('Stopped.');
      const { image } = await decoder.decode({ frameIndex: i });
      try {
        if (!size) {
          sourceWidth = image.displayWidth;
          sourceHeight = image.displayHeight;
          size = animatedFrameSize(sourceWidth, sourceHeight, take);
          canvas = makeCanvas(size.width, size.height);
        }
        frames.push(grab(canvas!, image, size.width, size.height, frameMs(image.duration)));
      } finally {
        image.close();
      }
      onProgress?.(i + 1, take);
      // Give the page a breath between frames, so the progress shows and taps still land.
      if (i % 4 === 3) await new Promise((r) => setTimeout(r, 0));
    }
    return { frames, animated: true, firstFrameOnly: false, droppedFrames: count - take, sourceWidth, sourceHeight };
  } finally {
    decoder.close();
    if (canvas) releaseCanvas(canvas);
  }
}

async function decodeFirst(blob: Blob): Promise<{ frame: EditFrame; width: number; height: number }> {
  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(blob, { imageOrientation: 'from-image' });
  } catch {
    throw new PhotoEditError('This picture cannot be edited on this phone. You can still send it as it is.');
  }
  try {
    const s = fitScale(bitmap.width, bitmap.height, STILL_LIMITS.maxSide, STILL_LIMITS.maxPixels);
    const width = Math.max(1, Math.round(bitmap.width * s));
    const height = Math.max(1, Math.round(bitmap.height * s));
    const c = makeCanvas(width, height);
    try {
      return { frame: grab(c, bitmap, width, height, 0), width: bitmap.width, height: bitmap.height };
    } finally {
      releaseCanvas(c);
    }
  } finally {
    bitmap.close();
  }
}

/** Read a picture for editing. Never changes `blob`. */
export async function decodePicture(blob: Blob, options: { onProgress?: (done: number, total: number) => void; signal?: AbortSignal } = {}): Promise<Decoded> {
  const head = new Uint8Array(await blob.slice(0, 1 << 20).arrayBuffer());
  const moves = looksAnimated(head, blob.type);
  if (moves) {
    const all = await decodeAll(blob, options.onProgress, options.signal).catch(() => null);
    if (all) return all;
  }
  const first = await decodeFirst(blob);
  return { frames: [first.frame], animated: moves, firstFrameOnly: moves, droppedFrames: 0, sourceWidth: first.width, sourceHeight: first.height };
}
