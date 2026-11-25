import type { Frame } from './geometry';
import { contrastOn, fontFor, overlaySize, stickerDataUrl } from './overlays';
import { lensById, lensDataUrl } from './lenses';
import type { Overlay, PlacedOverlay, Stroke, TextOverlay } from './recipe';
import { makeCanvas, releaseCanvas } from './decode';

/**
 * Drawing overlays with Canvas 2D: the same code draws the preview's pen
 * strokes and the full size layer laid over every exported frame, so what is
 * seen while editing is what is sent.
 */

type Ctx = CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D;

export function drawStrokes(ctx: Ctx, strokes: readonly Stroke[], width: number, height: number): void {
  ctx.save();
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  for (const s of strokes) {
    ctx.strokeStyle = s.color;
    ctx.fillStyle = s.color;
    ctx.lineWidth = Math.max(1, s.width * width);
    if (s.points.length === 1) {
      ctx.beginPath();
      ctx.arc(s.points[0][0] * width, s.points[0][1] * height, ctx.lineWidth / 2, 0, Math.PI * 2);
      ctx.fill();
      continue;
    }
    ctx.beginPath();
    s.points.forEach(([x, y], i) => (i ? ctx.lineTo(x * width, y * height) : ctx.moveTo(x * width, y * height)));
    ctx.stroke();
  }
  ctx.restore();
}

/** The lines of a text overlay and the box they take, at `size` px. */
export function textBox(ctx: Ctx, o: TextOverlay, size: number): { lines: string[]; width: number; height: number; lineHeight: number } {
  const f = fontFor(o.font);
  ctx.font = `${f.weight} ${size}px ${f.family}`;
  const lines = o.text.split('\n');
  const lineHeight = size * 1.2;
  const width = Math.max(...lines.map((l) => ctx.measureText(l).width), size * 0.5);
  return { lines, width, height: lineHeight * lines.length, lineHeight };
}

function drawText(ctx: Ctx, o: TextOverlay, size: number): void {
  const box = textBox(ctx, o, size);
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  if (o.pill) {
    const padX = size * 0.45;
    const padY = size * 0.2;
    const w = box.width + padX * 2;
    const h = box.height + padY * 2;
    ctx.fillStyle = o.color;
    ctx.beginPath();
    ctx.roundRect(-w / 2, -h / 2, w, h, Math.min(h / 2, size * 0.6));
    ctx.fill();
    ctx.fillStyle = contrastOn(o.color);
  } else {
    // Readable on any picture: a soft dark edge under light words, a light one under dark words.
    ctx.shadowColor = contrastOn(o.color) === '#111827' ? 'rgba(0,0,0,0.55)' : 'rgba(255,255,255,0.6)';
    ctx.shadowBlur = size * 0.12;
    ctx.fillStyle = o.color;
  }
  box.lines.forEach((line, i) => ctx.fillText(line, 0, (i - (box.lines.length - 1) / 2) * box.lineHeight));
}

async function loadImage(src: string): Promise<CanvasImageSource | null> {
  try {
    const img = new Image();
    img.decoding = 'async';
    img.src = src;
    await img.decode();
    return img;
  } catch {
    return null;
  }
}

/**
 * Load the pictures a set of overlays uses, keyed by kind and id: the bundled
 * stickers and lenses are inline SVG, so nothing is fetched from anywhere.
 */
export async function loadPictures(overlays: readonly Overlay[]): Promise<Map<string, CanvasImageSource>> {
  const out = new Map<string, CanvasImageSource>();
  for (const o of overlays) {
    if (o.kind !== 'sticker' && o.kind !== 'lens') continue;
    const key = `${o.kind}:${o.kind === 'sticker' ? o.sticker : o.lens}`;
    if (out.has(key)) continue;
    const url = o.kind === 'sticker' ? stickerDataUrl(o.sticker) : lensDataUrl(o.lens);
    const img = url ? await loadImage(url) : null;
    if (img) out.set(key, img);
  }
  return out;
}

export function drawPlaced(ctx: Ctx, o: PlacedOverlay, width: number, height: number, pictures: Map<string, CanvasImageSource>): void {
  const size = overlaySize(o, width);
  ctx.save();
  ctx.translate(o.x * width, o.y * height);
  ctx.rotate((o.rotation * Math.PI) / 180);
  if (o.kind === 'text') drawText(ctx, o, size);
  else if (o.kind === 'emoji') {
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.font = `${size}px "Noto Color Emoji", "Apple Color Emoji", "Segoe UI Emoji", sans-serif`;
    ctx.fillText(o.emoji, 0, 0);
  } else if (o.kind === 'lens') {
    const lens = lensById(o.lens);
    const img = pictures.get(`lens:${o.lens}`);
    if (!lens || !img) return ctx.restore();
    const boxWidth = size;
    const boxHeight = size / lens.aspect;
    const [ax, ay] = lens.origin;
    ctx.drawImage(img, -ax * boxWidth, -ay * boxHeight, boxWidth, boxHeight);
  } else {
    const img = pictures.get(`sticker:${o.sticker}`);
    if (img) ctx.drawImage(img, -size / 2, -size / 2, size, size);
  }
  ctx.restore();
}

export function drawOverlays(ctx: Ctx, overlays: readonly Overlay[], width: number, height: number, pictures: Map<string, CanvasImageSource>): void {
  for (const o of overlays) {
    if (o.kind === 'drawing') drawStrokes(ctx, o.strokes, width, height);
    else drawPlaced(ctx, o, width, height, pictures);
  }
}

/** Every overlay drawn on a clear layer the size of the result, or null when there are none. */
export async function renderOverlayLayer(overlays: readonly Overlay[], width: number, height: number): Promise<Frame | null> {
  const visible = overlays.filter((o) => o.kind !== 'drawing' || o.strokes.length);
  if (!visible.length) return null;
  const pictures = await loadPictures(visible);
  const c = makeCanvas(width, height);
  try {
    drawOverlays(c.ctx, visible, width, height, pictures);
    const img = c.ctx.getImageData(0, 0, width, height);
    return { width, height, data: img.data };
  } finally {
    releaseCanvas(c);
  }
}
