/**
 * The offline family chat's words and small helpers (the radio and the
 * crypto are on the phone: FamilyMesh / MeshRules).
 */
import type { MeshMessage } from '../plugins/familyMesh';

/** Largest photo preview the offline chat carries (MeshRules.THUMB_MAX). */
export const THUMB_MAX_BYTES = 24 * 1024;

/** Where a message is: waiting for a phone, passed on, or in the family chat. */
export function deliveryLine(m: MeshMessage): string {
  if (m.synced) return 'In the family chat';
  if (m.mine) return 'Waiting for the internet; passed to family phones nearby';
  return m.hops > 1 ? `Came through ${m.hops - 1} other phone${m.hops === 2 ? '' : 's'}` : 'From a phone nearby';
}

export function mapLink(lat: number, lon: number): string {
  return `https://www.openstreetmap.org/?mlat=${lat.toFixed(5)}&mlon=${lon.toFixed(5)}#map=17/${lat.toFixed(5)}/${lon.toFixed(5)}`;
}

/** Base64 length to bytes. */
export function base64Bytes(b64: string): number {
  const pad = b64.endsWith('==') ? 2 : b64.endsWith('=') ? 1 : 0;
  return Math.floor((b64.length * 3) / 4) - pad;
}

/**
 * A photo as a JPEG preview small enough to send offline: scaled down and
 * compressed until it fits THUMB_MAX_BYTES. Returns base64 without the
 * data: prefix, or null when it cannot be made that small.
 */
export async function makeThumbnail(file: Blob, maxBytes = THUMB_MAX_BYTES): Promise<string | null> {
  const bitmap = await createImageBitmap(file);
  for (const side of [320, 240, 160, 120]) {
    const scale = Math.min(1, side / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    const ctx = canvas.getContext('2d');
    if (!ctx) return null;
    ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    for (const quality of [0.7, 0.5, 0.35]) {
      const url = canvas.toDataURL('image/jpeg', quality);
      const b64 = url.slice(url.indexOf(',') + 1);
      if (base64Bytes(b64) <= maxBytes) return b64;
    }
  }
  return null;
}
