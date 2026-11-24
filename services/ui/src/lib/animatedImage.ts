import { hostOf, type LinkPreview } from './privateLinkPreview';

/**
 * Animated pictures in the chats (GIFs, animated WebP and APNG): whether a
 * file animates at all, and which ones a person paused.
 *
 * A pause lasts for the browser or app session: scrolling away and back, a
 * re-render, another chat and back, or a reload keep it, and a new session
 * starts them playing again. That is sessionStorage, which the Capacitor
 * WebView has too. Every read and write is guarded: without storage (a locked
 * down browser, a full quota) the choices live in memory for this page only.
 *
 * Keys name the message or file, never its object URL (a private picture's
 * URL is new every time it is decrypted). Nothing about the picture itself is
 * stored: the key is an id the server already knows, and the value one bit.
 */

export const PAUSED_STORAGE_KEY = 'jarvis_animated_paused_v1';
/** The most choices kept; the oldest go first, so a long session cannot fill storage. */
export const MAX_CHOICES = 400;

/** Mime types that can animate. JPEG and HEIC never do, so they are never read. */
export const MAYBE_ANIMATED_TYPES = new Set(['image/gif', 'image/webp', 'image/png', 'image/apng']);

export function mayAnimate(mime: string | undefined | null): boolean {
  return MAYBE_ANIMATED_TYPES.has((mime ?? '').toLowerCase().split(';')[0].trim());
}

/** The largest GIF (or WebP) shown as itself in a bubble; a bigger one shows its still preview. */
export const MAX_INLINE_ANIMATED_BYTES = 10 * 1024 * 1024;

/**
 * Whether a bubble shows the file itself rather than Nextcloud's preview:
 * a preview of a GIF or WebP is one still frame, so the picture would never
 * move. It is the one request the bubble makes either way (the file instead
 * of its preview), and the viewer then reuses it.
 */
export function showsFileItself(file: { mimetype?: string; size?: string | number }): boolean {
  const mime = (file.mimetype || '').toLowerCase();
  if (mime !== 'image/gif' && mime !== 'image/webp') return false;
  const size = Number(file.size);
  return Number.isFinite(size) && size > 0 && size <= MAX_INLINE_ANIMATED_BYTES;
}

// --- Does it animate? -------------------------------------------------------------

function ascii(bytes: Uint8Array, at: number, length: number): string {
  let out = '';
  for (let i = at; i < at + length && i < bytes.length; i += 1) out += String.fromCharCode(bytes[i]);
  return out;
}

/** Skip a GIF's data sub-blocks from `at`; returns the index after the terminator, or -1 if cut short. */
function skipSubBlocks(bytes: Uint8Array, at: number): number {
  let i = at;
  while (i < bytes.length) {
    const size = bytes[i];
    i += 1;
    if (size === 0) return i;
    i += size;
  }
  return -1;
}

/** Frames in a GIF (stops counting at two: that is all anyone needs to know). */
function gifFrames(bytes: Uint8Array): number {
  if (bytes.length < 13) return 0;
  let i = 13;
  const flags = bytes[10];
  if (flags & 0x80) i += 3 * (1 << ((flags & 0x07) + 1));
  let frames = 0;
  while (i < bytes.length) {
    const block = bytes[i];
    if (block === 0x3b) break;
    if (block === 0x21) {
      i = skipSubBlocks(bytes, i + 2);
      if (i < 0) break;
      continue;
    }
    if (block !== 0x2c) break;
    frames += 1;
    if (frames > 1) return frames;
    if (i + 10 > bytes.length) break;
    const local = bytes[i + 9];
    i += 10;
    if (local & 0x80) i += 3 * (1 << ((local & 0x07) + 1));
    i = skipSubBlocks(bytes, i + 1);
    if (i < 0) break;
  }
  return frames;
}

/** Whether a WebP has its animation flag (VP8X) or an ANIM chunk. */
function webpAnimated(bytes: Uint8Array): boolean {
  if (ascii(bytes, 12, 4) === 'VP8X' && bytes.length > 20 && bytes[20] & 0x02) return true;
  let i = 12;
  while (i + 8 <= bytes.length) {
    const type = ascii(bytes, i, 4);
    if (type === 'ANIM' || type === 'ANMF') return true;
    const size = bytes[i + 4] | (bytes[i + 5] << 8) | (bytes[i + 6] << 16) | (bytes[i + 7] << 24);
    if (size < 0) return false;
    i += 8 + size + (size & 1);
  }
  return false;
}

/** Whether a PNG is an APNG with more than one frame (an acTL chunk before the image data). */
function pngAnimated(bytes: Uint8Array): boolean {
  let i = 8;
  while (i + 8 <= bytes.length) {
    const size = ((bytes[i] << 24) | (bytes[i + 1] << 16) | (bytes[i + 2] << 8) | bytes[i + 3]) >>> 0;
    const type = ascii(bytes, i + 4, 4);
    if (type === 'acTL') {
      const frames = i + 12 <= bytes.length ? ((bytes[i + 8] << 24) | (bytes[i + 9] << 16) | (bytes[i + 10] << 8) | bytes[i + 11]) >>> 0 : 0;
      return frames > 1;
    }
    if (type === 'IDAT' || type === 'IEND') return false;
    i += 12 + size;
  }
  return false;
}

/** Whether these bytes are a picture with more than one frame, judged by its content, not its name or type. */
export function isAnimatedImage(bytes: Uint8Array): boolean {
  if (ascii(bytes, 0, 3) === 'GIF') return gifFrames(bytes) > 1;
  if (ascii(bytes, 0, 4) === 'RIFF' && ascii(bytes, 8, 4) === 'WEBP') return webpAnimated(bytes);
  if (bytes[0] === 0x89 && ascii(bytes, 1, 3) === 'PNG') return pngAnimated(bytes);
  return false;
}

/** The bytes of a base64 data: URL (empty when it is not one). */
function dataUrlBytes(src: string): Uint8Array {
  const comma = src.indexOf(',');
  if (comma < 0 || !src.slice(0, comma).endsWith(';base64')) return new Uint8Array();
  const binary = atob(src.slice(comma + 1));
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

/** Answers per picture key, so a picture shown again is not read again. In memory only. */
const animatedByKey = new Map<string, Promise<boolean>>();

/**
 * Whether the picture at `src` animates. `src` is an object URL (the chats
 * show pictures from one), so reading it is a copy in memory, never a request
 * to a server: a private picture's plaintext stays in this page.
 */
export function animatesAt(key: string, src: string): Promise<boolean> {
  const known = animatedByKey.get(key);
  if (known) return known;
  const made = (async () => {
    if (src.startsWith('data:')) return isAnimatedImage(dataUrlBytes(src));
    if (!src.startsWith('blob:')) return false;
    const response = await fetch(src);
    return isAnimatedImage(new Uint8Array(await response.arrayBuffer()));
  })().catch(() => false);
  animatedByKey.set(key, made);
  if (animatedByKey.size > MAX_CHOICES) animatedByKey.delete(animatedByKey.keys().next().value as string);
  return made;
}

// --- Who paused what ----------------------------------------------------------------

export type AnimationChoice = 'paused' | 'playing';

let memory: Record<string, AnimationChoice> | null = null;
const listeners = new Set<() => void>();

function read(): Record<string, AnimationChoice> {
  if (memory) return memory;
  let stored: Record<string, AnimationChoice> = {};
  try {
    const raw = window.sessionStorage.getItem(PAUSED_STORAGE_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : {};
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      for (const [key, value] of Object.entries(parsed as Record<string, unknown>)) {
        if (value === 'paused' || value === 'playing') stored[key] = value;
      }
    }
  } catch {
    stored = {};
  }
  memory = stored;
  return memory;
}

/** What this session chose for a picture, or null when nobody has pressed anything. */
export function animationChoice(key: string): AnimationChoice | null {
  return read()[key] ?? null;
}

export function setAnimationChoice(key: string, choice: AnimationChoice): void {
  const next: Record<string, AnimationChoice> = { ...read() };
  delete next[key];
  next[key] = choice;
  const keys = Object.keys(next);
  for (const old of keys.slice(0, Math.max(0, keys.length - MAX_CHOICES))) delete next[old];
  memory = next;
  try {
    window.sessionStorage.setItem(PAUSED_STORAGE_KEY, JSON.stringify(next));
  } catch {
    // No storage: the choice holds for this page only.
  }
  for (const listener of listeners) listener();
}

export function subscribeAnimationChoices(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Whether a picture starts paused: what the person chose, else paused only when they asked for reduced motion. */
export function startsPaused(choice: AnimationChoice | null, reducedMotion: boolean): boolean {
  if (choice) return choice === 'paused';
  return reducedMotion;
}

/** Tests only: forget the in-memory copy so the next read goes to storage again. */
export function resetAnimationChoicesForTests(): void {
  memory = null;
  animatedByKey.clear();
}

/** The key for a picture in the private chat: the chat and its encrypted blob's id, never the object URL. */
export function privatePictureKey(token: string, blob: string): string {
  return `private:${token}:${blob}`;
}

/** The key for a file shared in a family chat (Nextcloud's file id). */
export function talkFileKey(file: { id?: string | number; path?: string }): string {
  return `talk:${file.id ?? file.path ?? ''}`;
}

/** The key for a GIF-like clip from a pasted link (GIPHY, Tenor). */
export function linkClipKey(url: string): string {
  return `link:${url}`;
}

/** Whether a link's clip is a GIF in video clothes (GIPHY and Tenor serve their GIFs as MP4). */
export function isGifClip(preview: LinkPreview): boolean {
  if (preview.kind !== 'video' || preview.embed?.provider !== 'file') return false;
  return isGifHost(hostOf(preview.embed.src)) || isGifHost(hostOf(preview.url));
}

/** Hosts whose "videos" are GIFs: their clips loop, silent, like the GIF they stand for. */
export function isGifHost(host: string): boolean {
  const h = host.toLowerCase().replace(/\.$/, '');
  return ['giphy.com', 'tenor.com', 'tenor.co', 'gph.is'].some((base) => h === base || h.endsWith(`.${base}`));
}
