/**
 * A person's picture as a square PNG for a home-screen shortcut: their
 * uploaded photo (center-cropped) or their built-in picture drawn on its
 * gradient. Null when there is no picture or it cannot be drawn here; the
 * phone then draws their initials instead.
 */
import { presetFor, type AvatarPreset } from './avatarPresets';

const SIZE = 256;

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error('The picture could not be drawn'));
    img.src = src;
  });
}

async function drawPng(src: string): Promise<string | null> {
  const img = await loadImage(src);
  const canvas = document.createElement('canvas');
  canvas.width = SIZE;
  canvas.height = SIZE;
  const ctx = canvas.getContext('2d');
  if (!ctx || !img.width || !img.height) return null;
  // Cover: the middle square of the picture, like the round avatar in chat.
  const side = Math.min(img.width, img.height);
  ctx.drawImage(img, (img.width - side) / 2, (img.height - side) / 2, side, side, 0, 0, SIZE, SIZE);
  const url = canvas.toDataURL('image/png');
  return url.startsWith('data:image/png;base64,') ? url.slice('data:image/png;base64,'.length) : null;
}

/** A built-in picture as one standalone SVG: its gradient, then its drawing. */
export function presetIconSvg(preset: AvatarPreset): string {
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="${SIZE}" height="${SIZE}">` +
    `<defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">` +
    `<stop offset="0" stop-color="${preset.from}"/><stop offset="1" stop-color="${preset.to}"/>` +
    `</linearGradient></defs><rect width="100" height="100" fill="url(#bg)"/>${preset.svg}</svg>`
  );
}

/**
 * The picture for `avatar` as base64 PNG, or null. `fetchUpload` gets an
 * uploaded photo's bytes (the chat's own cached request, so it is usually
 * already here).
 */
export async function avatarIconPng(avatar: string | null | undefined, fetchUpload: () => Promise<Blob>): Promise<string | null> {
  try {
    if (avatar?.startsWith('upload:')) {
      const url = URL.createObjectURL(await fetchUpload());
      try {
        return await drawPng(url);
      } finally {
        URL.revokeObjectURL(url);
      }
    }
    const preset = presetFor(avatar);
    if (preset) return await drawPng(`data:image/svg+xml;charset=utf-8,${encodeURIComponent(presetIconSvg(preset))}`);
  } catch {
    // Drawn as initials instead.
  }
  return null;
}
