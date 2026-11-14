/**
 * Cropping a profile photo: which square of the photo becomes the picture.
 *
 * A crop is the square's centre (``x``, ``y`` as fractions of the photo's
 * width and height) and a ``zoom``: at 1 the square is the photo's whole short
 * side, at 2 half of it. The same three numbers are kept with the photo on the
 * server, so the picture can be moved or zoomed later without uploading again.
 */

export interface AvatarCrop {
  x: number;
  y: number;
  zoom: number;
}

export const MIN_ZOOM = 1;
export const MAX_ZOOM = 5;
/** The square everyone sees, in pixels. */
export const AVATAR_SIZE = 512;
/** The longest side of the original kept for re-cropping: enough to zoom in, small enough to send. */
export const ORIGINAL_MAX_SIDE = 1600;

export const CENTRED: AvatarCrop = { x: 0.5, y: 0.5, zoom: 1 };

/** The square's side in the photo's pixels. */
export function cropSide(width: number, height: number, zoom: number): number {
  return Math.min(width, height) / zoom;
}

/** The crop moved back inside the photo (a square never hangs off its edge) and its zoom into range. */
export function clampCrop(crop: AvatarCrop, width: number, height: number): AvatarCrop {
  const zoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, Number.isFinite(crop.zoom) ? crop.zoom : 1));
  const side = cropSide(width, height, zoom);
  const halfX = side / 2 / width;
  const halfY = side / 2 / height;
  const within = (v: number, half: number) => Math.min(1 - half, Math.max(half, Number.isFinite(v) ? v : 0.5));
  return { x: within(crop.x, halfX), y: within(crop.y, halfY), zoom };
}

/** The square, in the photo's pixels: left, top and side. */
export function cropRect(crop: AvatarCrop, width: number, height: number): { left: number; top: number; side: number } {
  const c = clampCrop(crop, width, height);
  const side = cropSide(width, height, c.zoom);
  return { left: c.x * width - side / 2, top: c.y * height - side / 2, side };
}

/**
 * The crop after dragging the photo by (dx, dy) screen pixels in a view
 * ``viewSize`` pixels across: the photo follows the finger, so the square
 * moves the other way.
 */
export function panned(crop: AvatarCrop, dx: number, dy: number, viewSize: number, width: number, height: number): AvatarCrop {
  const side = cropSide(width, height, clampCrop(crop, width, height).zoom);
  const scale = side / viewSize;  // photo pixels per screen pixel
  return clampCrop({ ...crop, x: crop.x - (dx * scale) / width, y: crop.y - (dy * scale) / height }, width, height);
}

/** The crop at a new zoom, keeping the point under (fx, fy) (0-1 across the view) where it is. */
export function zoomed(crop: AvatarCrop, zoom: number, width: number, height: number, fx = 0.5, fy = 0.5): AvatarCrop {
  const before = clampCrop(crop, width, height);
  const next = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, zoom));
  const oldSide = cropSide(width, height, before.zoom);
  const newSide = cropSide(width, height, next);
  const px = before.x * width + (fx - 0.5) * oldSide;
  const py = before.y * height + (fy - 0.5) * oldSide;
  return clampCrop({ x: (px - (fx - 0.5) * newSide) / width, y: (py - (fy - 0.5) * newSide) / height, zoom: next }, width, height);
}

type Drawable = CanvasImageSource & { width: number; height: number };

/** Draw the cropped square at ``size`` and return it as a JPEG data URL. */
export function renderCrop(image: Drawable, crop: AvatarCrop, size = AVATAR_SIZE): string {
  const { left, top, side } = cropRect(crop, image.width, image.height);
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('This device cannot prepare the picture.');
  ctx.imageSmoothingQuality = 'high';
  ctx.drawImage(image, left, top, side, side, 0, 0, size, size);
  return canvas.toDataURL('image/jpeg', 0.88);
}

/**
 * A chosen file, the right way up and no bigger than ``ORIGINAL_MAX_SIDE``:
 * the canvas to crop from and the JPEG kept as the original.
 */
export async function loadPhoto(source: Blob): Promise<{ canvas: HTMLCanvasElement; dataUrl: string }> {
  // Phone photos carry their rotation in EXIF; "from-image" applies it.
  const bitmap = await createImageBitmap(source, { imageOrientation: 'from-image' });
  const scale = Math.min(1, ORIGINAL_MAX_SIDE / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Math.round(bitmap.width * scale));
  canvas.height = Math.max(1, Math.round(bitmap.height * scale));
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('This device cannot prepare the picture.');
  ctx.imageSmoothingQuality = 'high';
  ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  bitmap.close?.();
  return { canvas, dataUrl: canvas.toDataURL('image/jpeg', 0.86) };
}

/** A data URL as a Blob, to load a kept original back into the cropper. */
export async function dataUrlBlob(dataUrl: string): Promise<Blob> {
  return (await fetch(dataUrl)).blob();
}
