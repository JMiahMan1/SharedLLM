/**
 * Link previews in the private chat, the way Signal makes them.
 *
 * The SENDER's device builds a preview once, while composing: it asks the
 * household's own server to read the page (a browser cannot, CORS), and puts
 * the answer inside the sealed message. The other parent's device only draws
 * what it decrypted: nothing is fetched to show a preview, so no site and no
 * video host learns that a message was read. A video or audio file is loaded
 * only when the reader taps play, and only then does that site hear of it.
 *
 * Whatever comes out of a message is checked here before it is drawn
 * (`readLinkPreview`): the other device could be running anything.
 */

export type LinkPreviewKind = 'link' | 'video' | 'audio';

export type LinkPreviewEmbed =
  | { provider: 'youtube'; id: string }
  | { provider: 'vimeo'; id: string; hash?: string }
  | { provider: 'file'; src: string; mime: string };

export interface LinkPreview {
  url: string;
  title?: string;
  description?: string;
  site_name?: string;
  kind: LinkPreviewKind;
  /** A small JPEG or PNG as a data URL, made by the server and sealed with the message. */
  image?: string;
  embed?: LinkPreviewEmbed;
}

export const MAX_PREVIEW_URL_CHARS = 2048;
export const MAX_PREVIEW_TITLE_CHARS = 300;
export const MAX_PREVIEW_DESCRIPTION_CHARS = 500;
export const MAX_PREVIEW_SITE_CHARS = 100;
/** The thumbnail's bytes at most (the server makes it at most 60 KB). */
export const MAX_PREVIEW_IMAGE_BYTES = 64 * 1024;

const YOUTUBE_ID = /^[A-Za-z0-9_-]{11}$/;
const VIMEO_ID = /^[0-9]{5,12}$/;
const VIMEO_HASH = /^[0-9a-f]{6,20}$/;
const IMAGE_DATA_URL = /^data:image\/(jpeg|png);base64,([A-Za-z0-9+/]+={0,2})$/;
export const PREVIEW_VIDEO_TYPES = new Set(['video/mp4', 'video/webm', 'video/quicktime']);
export const PREVIEW_AUDIO_TYPES = new Set(['audio/mpeg', 'audio/mp4', 'audio/aac', 'audio/ogg', 'audio/wav']);

/** An http(s) address no longer than the cap, or null. */
export function webAddress(value: unknown): string | null {
  if (typeof value !== 'string' || !value || value.length > MAX_PREVIEW_URL_CHARS) return null;
  try {
    const url = new URL(value);
    if (url.protocol !== 'https:' && url.protocol !== 'http:') return null;
    if (!url.hostname || url.username || url.password) return null;
    return value;
  } catch {
    return null;
  }
}

const ELLIPSIS = String.fromCharCode(0x2026);

function text(value: unknown, max: number): string | undefined {
  if (typeof value !== 'string') return undefined;
  // Control characters (a bidi override or a bell) have no place in a card; tabs and newlines become spaces.
  const clean = Array.from(value, (c) => {
    const code = c.charCodeAt(0);
    const bidi = (code >= 0x202a && code <= 0x202e) || (code >= 0x2066 && code <= 0x2069);
    return code === 9 || code === 10 || code === 13 ? ' ' : code < 32 || code === 127 || bidi ? '' : c;
  }).join('').trim();
  if (!clean) return undefined;
  return clean.length > max ? `${clean.slice(0, max - 1)}${ELLIPSIS}` : clean;
}

/** A thumbnail data URL whose bytes are a JPEG or PNG under the cap, or undefined. */
function image(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined;
  // Base64 is 4 characters per 3 bytes; anything longer than the cap allows is refused before parsing.
  if (value.length > 32 + Math.ceil(MAX_PREVIEW_IMAGE_BYTES / 3) * 4) return undefined;
  const match = IMAGE_DATA_URL.exec(value);
  if (!match) return undefined;
  let bytes: string;
  try {
    bytes = atob(match[2].slice(0, 16));
  } catch {
    return undefined;
  }
  const jpeg = bytes.charCodeAt(0) === 0xff && bytes.charCodeAt(1) === 0xd8;
  const png = bytes.startsWith('\u0089PNG');
  if ((match[1] === 'jpeg' && !jpeg) || (match[1] === 'png' && !png)) return undefined;
  return value;
}

function embed(value: unknown, kind: LinkPreviewKind): LinkPreviewEmbed | undefined {
  if (!value || typeof value !== 'object') return undefined;
  const raw = value as Record<string, unknown>;
  if (raw.provider === 'youtube' && kind === 'video' && typeof raw.id === 'string' && YOUTUBE_ID.test(raw.id)) {
    return { provider: 'youtube', id: raw.id };
  }
  if (raw.provider === 'vimeo' && kind === 'video' && typeof raw.id === 'string' && VIMEO_ID.test(raw.id)) {
    return { provider: 'vimeo', id: raw.id, ...(typeof raw.hash === 'string' && VIMEO_HASH.test(raw.hash) ? { hash: raw.hash } : {}) };
  }
  if (raw.provider === 'file' && typeof raw.mime === 'string') {
    const src = webAddress(raw.src);
    const types = kind === 'video' ? PREVIEW_VIDEO_TYPES : kind === 'audio' ? PREVIEW_AUDIO_TYPES : null;
    if (src && types?.has(raw.mime)) return { provider: 'file', src, mime: raw.mime };
  }
  return undefined;
}

/**
 * A preview from a message (or the server), keeping only fields of the right
 * shape, or null when it cannot be drawn safely. A video or audio preview
 * whose player is not one of ours becomes a plain link card.
 */
export function readLinkPreview(raw: unknown): LinkPreview | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const value = raw as Record<string, unknown>;
  const url = webAddress(value.url);
  if (!url) return null;
  const wanted: LinkPreviewKind = value.kind === 'video' || value.kind === 'audio' ? value.kind : 'link';
  const player = wanted === 'link' ? undefined : embed(value.embed, wanted);
  const out: LinkPreview = { url, kind: player ? wanted : 'link' };
  const title = text(value.title, MAX_PREVIEW_TITLE_CHARS);
  const description = text(value.description, MAX_PREVIEW_DESCRIPTION_CHARS);
  const site = text(value.site_name, MAX_PREVIEW_SITE_CHARS);
  const thumb = image(value.image);
  if (title) out.title = title;
  if (description) out.description = description;
  if (site) out.site_name = site;
  if (thumb) out.image = thumb;
  if (player) out.embed = player;
  if (!out.title && !out.description && !out.image && !out.embed) return null;
  return out;
}

/** Where the tapped player loads from: YouTube's no-cookie host and Vimeo's player with Do Not Track. */
export function playerUrl(embed: LinkPreviewEmbed): string {
  if (embed.provider === 'youtube') {
    return `https://www.youtube-nocookie.com/embed/${embed.id}?autoplay=1&playsinline=1&rel=0&modestbranding=1`;
  }
  if (embed.provider === 'vimeo') {
    return `https://player.vimeo.com/video/${embed.id}?autoplay=1&dnt=1${embed.hash ? `&h=${embed.hash}` : ''}`;
  }
  return embed.src;
}

/** The host to show under a title when the page named no site. */
export function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return '';
  }
}

// --- This device's setting --------------------------------------------------------

const STORAGE_PREFIX = 'jarvis.privateChat.linkPreviews.';

/**
 * Whether this device makes link previews in a chat. On unless turned off: a
 * preview only ever asks the household's own server, and only for what the
 * sender is about to send. Off, nothing about a link leaves the device.
 */
export function linkPreviewsOn(token: string): boolean {
  try {
    return globalThis.localStorage?.getItem(STORAGE_PREFIX + token) !== 'off';
  } catch {
    return true;
  }
}

export function setLinkPreviews(token: string, on: boolean): void {
  if (on) globalThis.localStorage.removeItem(STORAGE_PREFIX + token);
  else globalThis.localStorage.setItem(STORAGE_PREFIX + token, 'off');
}
