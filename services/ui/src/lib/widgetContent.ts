/**
 * What the note and picture widgets may show (Home Assistant's markdown and
 * picture cards), checked the same way the identity service checks them
 * (services/identity/main.py, `widget_config_error`), so a value the store
 * would refuse is refused here first, with the same words.
 */

export const NOTE_MAX_CHARS = 10_000;
export const NOTE_TITLE_MAX = 80;
export const PICTURE_TEXT_MAX = 200;
export const PICTURE_URL_MAX = 2_000;
/** An uploaded picture is kept inline; this is the store's limit on its text. */
export const PICTURE_DATA_MAX = 700_000;
/** The longest side an uploaded picture is scaled down to before it is saved. */
export const PICTURE_MAX_SIDE = 1280;

const DATA_IMAGE = /^data:image\/(png|jpeg|webp|gif);base64,[A-Za-z0-9+/]+={0,2}$/;

/** Why a picture source cannot be used, or null when it can (empty is "no picture yet"). */
export function pictureSrcError(src: string): string | null {
  if (!src) return null;
  if (src.startsWith('data:')) {
    if (src.length > PICTURE_DATA_MAX) return 'The uploaded picture is too large';
    return DATA_IMAGE.test(src) ? null : 'Only PNG, JPEG, WebP or GIF pictures can be uploaded';
  }
  if (src.length > PICTURE_URL_MAX || !/^https:\/\//i.test(src) || /[\s"<>]/.test(src)) {
    return 'A picture link must be an https:// address';
  }
  try {
    new URL(src);
  } catch {
    return 'A picture link must be an https:// address';
  }
  return null;
}

/** The source to put in an `<img>`, or null for anything not provably a picture. */
export function safePictureSrc(src: unknown): string | null {
  return typeof src === 'string' && src && pictureSrcError(src) === null ? src : null;
}

/** Why a note cannot be saved, or null. */
export function noteError(title: string, markdown: string): string | null {
  if (title.length > NOTE_TITLE_MAX) return `The title is longer than ${NOTE_TITLE_MAX} characters`;
  if (markdown.length > NOTE_MAX_CHARS) return `The note is longer than ${NOTE_MAX_CHARS} characters`;
  return null;
}

/**
 * Where a link in a note may go: web pages, mail and phone links. Anything
 * else (`javascript:`, `data:`, `vbscript:`) is dropped to plain text.
 */
export function safeNoteHref(href: string | null | undefined): string | null {
  if (!href) return null;
  const value = href.trim();
  if (/^(https?:|mailto:|tel:)/i.test(value)) return value;
  // A path inside the app ("/rooms") or a page anchor.
  if (/^\/(?!\/)/.test(value) || value.startsWith('#')) return value;
  return null;
}

/** Images inside a note follow the picture widget's rule: https only. */
export function safeNoteImage(src: string | null | undefined): string | null {
  return src && /^https:\/\//i.test(src.trim()) && !/[\s"<>]/.test(src.trim()) ? src.trim() : null;
}

/** The width and height that fit a picture inside `max` on its longest side. */
export function fitWithin(width: number, height: number, max = PICTURE_MAX_SIDE): { width: number; height: number } {
  if (width <= 0 || height <= 0) return { width: 0, height: 0 };
  const scale = Math.min(1, max / Math.max(width, height));
  return { width: Math.round(width * scale), height: Math.round(height * scale) };
}

/**
 * An uploaded picture as a JPEG data URL small enough to store: scaled to
 * PICTURE_MAX_SIDE, then compressed harder until it fits. Fails (with words
 * for the person) rather than saving something the store would refuse.
 */
export async function pictureFromFile(file: File): Promise<string> {
  if (!/^image\/(png|jpeg|webp|gif)$/.test(file.type)) {
    throw new Error('Only PNG, JPEG, WebP or GIF pictures can be uploaded');
  }
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise<HTMLImageElement>((resolve, reject) => {
      const el = new Image();
      el.onload = () => resolve(el);
      el.onerror = () => reject(new Error('That file could not be read as a picture'));
      el.src = url;
    });
    let side = PICTURE_MAX_SIDE;
    for (let attempt = 0; attempt < 6; attempt += 1) {
      const size = fitWithin(img.naturalWidth, img.naturalHeight, side);
      const canvas = document.createElement('canvas');
      canvas.width = size.width;
      canvas.height = size.height;
      const ctx = canvas.getContext('2d');
      if (!ctx) throw new Error('This browser cannot prepare pictures for upload');
      ctx.drawImage(img, 0, 0, size.width, size.height);
      const data = canvas.toDataURL('image/jpeg', attempt < 3 ? 0.82 - attempt * 0.12 : 0.6);
      if (data.length <= PICTURE_DATA_MAX) return data;
      if (attempt >= 2) side = Math.round(side * 0.75);
    }
    throw new Error('That picture is too large even after shrinking it');
  } finally {
    URL.revokeObjectURL(url);
  }
}
