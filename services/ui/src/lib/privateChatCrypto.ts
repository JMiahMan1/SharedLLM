/**
 * The private chat's end-to-end encryption, on the device only (WebCrypto).
 *
 * Every chat has a 32-byte data key (DK) that encrypts its messages and
 * pictures. The server keeps a *verifier* (the AES-GCM encryption of a fixed
 * constant under DK), so a wrong password is caught here without the server
 * learning anything, and the verifier never changes. A forgotten password
 * cannot be recovered by anyone.
 *
 * - **v1 chats** (the first ones made): DK is PBKDF2-SHA256(password, salt,
 *   iterations) itself, 600,000 rounds or more.
 * - **v2 chats** (every new chat, and a v1 chat after its first password
 *   change): PBKDF2-SHA256(password, kdf salt) gives 512 bits. The first 256
 *   are a key-encryption key (KEK) used only to wrap DK
 *   (`wrapped_key` = AES-GCM(KEK, DK)); the second 256 are `auth`, the proof a
 *   password change sends (the server keeps only its sha256).
 *
 * Changing the password re-wraps the same DK under a new KEK, so nothing sent
 * before has to be re-encrypted. Only that operation (and turning on "Unlock
 * with this phone's lock") ever holds DK as bytes, and it overwrites them with
 * zeros when it is done; the key a chat is unlocked with is never extractable.
 *
 * Every message is a fresh 12-byte IV and its ciphertext, kept by the gateway
 * (not Talk, since the private chat moved off it) as a `sealed` envelope after
 * a short line ("🔒 Encrypted message"), the same text a Talk message carried.
 * A picture is encrypted the same way (IV first, then the ciphertext) and kept
 * by the gateway as an opaque blob.
 *
 * A video clip is encrypted in fixed-size pieces (`VIDEO_CHUNK_BYTES`), each its
 * own blob: AES-GCM with a fresh IV per piece, and the clip's random id, the
 * piece's index, the number of pieces and the clip's size bound in as
 * additional data. A piece moved to another place, a missing last piece, a
 * piece from another clip or any changed byte is refused. Its poster frame is
 * a separate picture blob. The message (`t: 'video'`) names the pieces in
 * order and carries the type, length, size and frame size inside the
 * encryption, so the server sees only opaque blobs and how big they are.
 * Pieces keep every request small (the Android bridge carries a body as
 * base64), leave the 25 MB blob limit as it was, never hold a whole clip twice
 * on the way out, and decrypt one at a time on the way in.
 *
 * `PrivateVault` holds the key for one open chat. `lock()` drops the key, every
 * decrypted message and every picture's object URL; nothing survives a lock.
 */

import { deriveCallKey } from './callVerify';

export const PBKDF2_ITERATIONS = 600_000;
export const SALT_BYTES = 16;
export const IV_BYTES = 12;
/** The data key, the KEK and `auth`, in bytes. */
export const KEY_BYTES = 32;
/** What the verifier encrypts. Changing it locks every existing chat. */
export const VERIFIER_PLAINTEXT = 'jarvis-private-chat-verifier-v1';
export const SEALED_VERSION = 1;
export const SEALED_LINE = '🔒 Encrypted message';

const FENCE = '```jarvis-envelope';

export interface KdfParams {
  salt: string;
  iterations: number;
}

export interface Sealed {
  iv: string;
  ct: string;
}

/** What a device needs to unlock a chat (the server's public key record). */
export interface ChatKeyInfo {
  /** 1 (or absent) for a chat whose key is the password's; 2 for a wrapped key. */
  version?: 1 | 2;
  kdf: KdfParams;
  wrapped_key?: Sealed;
  verifier: Sealed;
}

/** A v2 key record for the server: the KEK's parameters, the wrapped key and the verifier. */
export interface WrappedRecord {
  version: 2;
  kdf: KdfParams;
  wrapped_key: Sealed;
  verifier: Sealed;
}

/** The body of a password change. `auth` (the current proof) only for a v2 chat. */
export interface PasswordChangeBody extends WrappedRecord {
  new_auth: string;
  auth?: string;
}

/** The current password was not right (caught on the device, before anything is sent). */
export class WrongPasswordError extends Error {
  constructor(message = 'That is not the current password for this chat.') {
    super(message);
    this.name = 'WrongPasswordError';
  }
}

/** What a message says once opened. */
export type PrivatePayload =
  | { t: 'text'; text: string }
  /** `sent_at` / `taken_at` are seconds since the epoch, inside the encryption (older pictures have neither). */
  | { t: 'image'; blob: string; mime: string; name?: string; caption?: string; sent_at?: number; taken_at?: number; sha256?: string }
  /**
   * A picture added straight to the gallery (never a chat message): kept as a
   * sealed gallery entry, not in the thread. The original bytes are in the blob
   * untouched; these are the facts read from them, for placing it in time.
   */
  | AddedPicture
  /** A video clip, in encrypted pieces (`chunks`), with its encrypted poster frame. */
  | VideoPayload
  /** A comment on a picture (or a video, by its poster) in the chat's gallery, naming the picture's blob. */
  | { t: 'comment'; blob: string; text: string; sent_at?: number };

/**
 * A video clip. Everything here travels inside the message's encryption: the
 * server sees only the blobs (the pieces and the poster) and their sizes.
 */
export interface VideoPayload {
  t: 'video';
  /** One of VIDEO_TYPES: what the decrypted clip is played as. */
  mime: string;
  /** The clip's size in bytes (the plaintext). */
  size: number;
  /** Length in seconds (0 when the device could not tell). */
  duration: number;
  width?: number;
  height?: number;
  /** The poster frame: a JPEG encrypted like a picture. */
  poster: string;
  /** The encrypted pieces, in order. */
  chunks: string[];
  /** The clip's random id (16 bytes, base64), bound into every piece. */
  clip: string;
  /** Bytes per piece (the last may be shorter). */
  chunk_bytes: number;
  name?: string;
  sent_at?: number;
  taken_at?: number;
}

export interface AddedPicture {
  t: 'added';
  blob: string;
  mime: string;
  /** The original file name, and the path inside a zip when it came from one. */
  name?: string;
  path?: string;
  caption?: string;
  /** When it was taken (seconds), and where that came from: EXIF, a date in its name, or the file's own time. */
  taken_at?: number;
  taken_from?: 'exif' | 'name' | 'file';
  /** When it was added to the gallery (seconds). */
  added_at?: number;
  /** The file's own modification time (seconds), as it was on the phone or in the zip. */
  file_time?: number;
  /** SHA-256 of the original bytes (hex), so an import run again skips what is already in. */
  sha256?: string;
  size?: number;
  source?: 'phone' | 'between' | 'zip';
  camera?: string;
  /** Where it was taken, from EXIF: only ever inside the encryption. */
  gps?: { lat: number; lon: number; alt?: number };
  width?: number;
  height?: number;
}

const encoder = new TextEncoder();
const decoder = new TextDecoder();

function subtle(): SubtleCrypto {
  const s = globalThis.crypto?.subtle;
  if (!s) throw new Error('This browser cannot encrypt (WebCrypto is not available; open Jarvis over HTTPS).');
  return s;
}

export function toBase64(bytes: Uint8Array): string {
  let binary = '';
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

export function fromBase64(text: string): Uint8Array<ArrayBuffer> {
  const binary = atob(text);
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) out[i] = binary.charCodeAt(i);
  return out;
}

export function randomBytes(n: number): Uint8Array<ArrayBuffer> {
  return globalThis.crypto.getRandomValues(new Uint8Array(n));
}

function checkStretch(password: string, params: KdfParams): void {
  if (!password) throw new Error('Enter the password.');
  if (!Number.isInteger(params.iterations) || params.iterations < PBKDF2_ITERATIONS) {
    throw new Error('This chat asks for a key stretch that is too weak.');
  }
}

async function passwordBits(password: string, params: KdfParams, bits: number): Promise<Uint8Array<ArrayBuffer>> {
  checkStretch(password, params);
  const base = await subtle().importKey('raw', encoder.encode(password), 'PBKDF2', false, ['deriveBits']);
  const out = await subtle().deriveBits(
    { name: 'PBKDF2', hash: 'SHA-256', salt: fromBase64(params.salt), iterations: params.iterations },
    base,
    bits,
  );
  return new Uint8Array(out);
}

/** A v1 chat's key from the password: AES-GCM-256, usable only to encrypt and decrypt, never exported. */
export async function deriveKey(password: string, params: KdfParams): Promise<CryptoKey> {
  checkStretch(password, params);
  const base = await subtle().importKey('raw', encoder.encode(password), 'PBKDF2', false, ['deriveKey']);
  return subtle().deriveKey(
    { name: 'PBKDF2', hash: 'SHA-256', salt: fromBase64(params.salt), iterations: params.iterations },
    base,
    { name: 'AES-GCM', length: 256 },
    false,
    ['encrypt', 'decrypt'],
  );
}

/** A KEK and `auth` from a v2 password stretch; the stretch's bytes are zeroed. */
async function splitStretch(bits: Uint8Array<ArrayBuffer>, usages: KeyUsage[]): Promise<{ kek: CryptoKey; auth: string }> {
  try {
    const kek = await subtle().importKey('raw', bits.subarray(0, KEY_BYTES), { name: 'AES-GCM' }, false, usages);
    return { kek, auth: toBase64(bits.subarray(KEY_BYTES, 2 * KEY_BYTES)) };
  } finally {
    bits.fill(0);
  }
}

/** Data-key bytes as a key for the chat: AES-GCM, never extractable. The bytes are zeroed. */
export async function importDataKey(bytes: Uint8Array<ArrayBuffer>): Promise<CryptoKey> {
  try {
    if (bytes.length !== KEY_BYTES) throw new Error('That is not a chat key.');
    return await subtle().importKey('raw', bytes, { name: 'AES-GCM' }, false, ['encrypt', 'decrypt']);
  } finally {
    bytes.fill(0);
  }
}

function isV2(info: ChatKeyInfo): boolean {
  return info.version === 2 || Boolean(info.wrapped_key);
}

/**
 * The chat's key for a password, or null when the password is wrong. v1:
 * derived from the password; v2: the wrapped key, unwrapped straight into a
 * non-extractable key (its bytes are never in this page's memory).
 */
export async function unlockKey(password: string, info: ChatKeyInfo): Promise<CryptoKey | null> {
  let key: CryptoKey;
  if (isV2(info)) {
    if (!info.wrapped_key) throw new Error('This chat has no key to unlock.');
    const { kek } = await splitStretch(await passwordBits(password, info.kdf, 512), ['unwrapKey']);
    try {
      key = await subtle().unwrapKey(
        'raw',
        fromBase64(info.wrapped_key.ct),
        kek,
        { name: 'AES-GCM', iv: fromBase64(info.wrapped_key.iv) },
        { name: 'AES-GCM' },
        false,
        ['encrypt', 'decrypt'],
      );
    } catch {
      // AES-GCM refuses a wrong KEK outright: that is the "wrong password" answer.
      return null;
    }
  } else {
    key = await deriveKey(password, info.kdf);
  }
  return (await checkVerifier(key, info.verifier)) ? key : null;
}

/**
 * The chat's data key as raw bytes, for the two jobs that need them (a
 * password change, turning on the phone lock). The caller MUST zero `dk` when
 * done. Null when the password is wrong.
 */
export async function rawDataKey(password: string, info: ChatKeyInfo): Promise<{ dk: Uint8Array<ArrayBuffer>; auth?: string } | null> {
  let dk: Uint8Array<ArrayBuffer>;
  let auth: string | undefined;
  if (isV2(info)) {
    if (!info.wrapped_key) throw new Error('This chat has no key to unlock.');
    const split = await splitStretch(await passwordBits(password, info.kdf, 512), ['decrypt']);
    auth = split.auth;
    try {
      dk = await decrypt(split.kek, fromBase64(info.wrapped_key.iv), fromBase64(info.wrapped_key.ct));
    } catch {
      return null;
    }
  } else {
    // Exactly the bits deriveKey() turns into the v1 chat's AES key.
    dk = await passwordBits(password, info.kdf, 256);
  }
  const check = await subtle().importKey('raw', dk, { name: 'AES-GCM' }, false, ['decrypt']);
  if (dk.length !== KEY_BYTES || !(await checkVerifier(check, info.verifier))) {
    dk.fill(0);
    return null;
  }
  return { dk, ...(auth ? { auth } : {}) };
}

/** Wrap data-key bytes under a new password: a fresh salt, the wrapped key and the new `auth`. */
async function wrapDataKey(dk: Uint8Array<ArrayBuffer>, password: string): Promise<{ kdf: KdfParams; wrapped_key: Sealed; auth: string }> {
  const kdf = { salt: toBase64(randomBytes(SALT_BYTES)), iterations: PBKDF2_ITERATIONS };
  const { kek, auth } = await splitStretch(await passwordBits(password, kdf, 512), ['encrypt']);
  const { iv, ct } = await encrypt(kek, dk);
  return { kdf, wrapped_key: { iv: toBase64(iv), ct: toBase64(ct) }, auth };
}

/**
 * A password change's request body: the same data key wrapped under the new
 * password, with the unchanged verifier. Throws WrongPasswordError (nothing is
 * sent) when the current password is not right. The data key's bytes are zeroed.
 */
export async function changePasswordRequest(info: ChatKeyInfo, current: string, next: string): Promise<PasswordChangeBody> {
  if (!next) throw new Error('Enter the new password.');
  const raw = await rawDataKey(current, info);
  if (!raw) throw new WrongPasswordError();
  try {
    const wrapped = await wrapDataKey(raw.dk, next);
    return {
      version: 2,
      kdf: wrapped.kdf,
      wrapped_key: wrapped.wrapped_key,
      verifier: { iv: info.verifier.iv, ct: info.verifier.ct },
      new_auth: wrapped.auth,
      ...(isV2(info) && raw.auth ? { auth: raw.auth } : {}),
    };
  } finally {
    raw.dk.fill(0);
  }
}

async function encrypt(key: CryptoKey, data: Uint8Array<ArrayBuffer>): Promise<{ iv: Uint8Array<ArrayBuffer>; ct: Uint8Array<ArrayBuffer> }> {
  const iv = randomBytes(IV_BYTES);
  const ct = new Uint8Array(await subtle().encrypt({ name: 'AES-GCM', iv }, key, data));
  return { iv, ct };
}

async function decrypt(key: CryptoKey, iv: Uint8Array<ArrayBuffer>, ct: Uint8Array<ArrayBuffer>): Promise<Uint8Array<ArrayBuffer>> {
  return new Uint8Array(await subtle().decrypt({ name: 'AES-GCM', iv }, key, ct));
}

/** The verifier for a key: the encryption of a fixed constant. */
export async function makeVerifier(key: CryptoKey): Promise<Sealed> {
  const { iv, ct } = await encrypt(key, encoder.encode(VERIFIER_PLAINTEXT));
  return { iv: toBase64(iv), ct: toBase64(ct) };
}

/** Whether a key opens the chat's verifier (the password was right). */
export async function checkVerifier(key: CryptoKey, verifier: Sealed): Promise<boolean> {
  try {
    const plain = await decrypt(key, fromBase64(verifier.iv), fromBase64(verifier.ct));
    return decoder.decode(plain) === VERIFIER_PLAINTEXT;
  } catch {
    // AES-GCM refuses a wrong key outright: that is the "wrong password" answer.
    return false;
  }
}

/** A new (v2) chat's key record for a password, its `auth`, and its key (never extractable). */
export async function newChatSecrets(password: string): Promise<WrappedRecord & { auth: string; key: CryptoKey }> {
  const dk = randomBytes(KEY_BYTES);
  try {
    const wrapped = await wrapDataKey(dk, password);
    const key = await subtle().importKey('raw', dk, { name: 'AES-GCM' }, false, ['encrypt', 'decrypt']);
    return { version: 2, kdf: wrapped.kdf, wrapped_key: wrapped.wrapped_key, verifier: await makeVerifier(key), auth: wrapped.auth, key };
  } finally {
    dk.fill(0);
  }
}

/** A message as the gateway keeps it (and Talk once carried it): a short readable line and the sealed envelope. */
export async function sealMessage(key: CryptoKey, payload: PrivatePayload): Promise<string> {
  const { iv, ct } = await encrypt(key, encoder.encode(JSON.stringify(payload)));
  const envelope = { kind: 'sealed', title: 'Encrypted message', v: SEALED_VERSION, iv: toBase64(iv), ct: toBase64(ct) };
  return `${SEALED_LINE}\n\n${FENCE}\n${JSON.stringify(envelope)}\n${FENCE}`;
}

/**
 * Any JSON record sealed in the same envelope as a message (Beloved's records,
 * services/gateway/beloved.py). The server can only check its shape.
 */
export async function sealRecord(key: CryptoKey, record: object): Promise<string> {
  const { iv, ct } = await encrypt(key, encoder.encode(JSON.stringify(record)));
  const envelope = { kind: 'sealed', title: 'Encrypted message', v: SEALED_VERSION, iv: toBase64(iv), ct: toBase64(ct) };
  return `${SEALED_LINE}\n\n${FENCE}\n${JSON.stringify(envelope)}\n${FENCE}`;
}

/** A sealed record's JSON, or null when it is not sealed, was made with another key, or was tampered with. */
export async function openRecord(key: CryptoKey, raw: string | null | undefined): Promise<unknown> {
  const sealed = readSealed(raw);
  if (!sealed) return null;
  try {
    return JSON.parse(decoder.decode(await decrypt(key, fromBase64(sealed.iv), fromBase64(sealed.ct)))) as unknown;
  } catch {
    return null;
  }
}

/** The sealed envelope in a raw message, or null when it carries none. */
export function readSealed(raw: string | null | undefined): Sealed | null {
  const message = String(raw ?? '');
  const start = message.indexOf(FENCE);
  if (start === -1) return null;
  const end = message.indexOf(FENCE, start + FENCE.length);
  if (end === -1) return null;
  try {
    const parsed = JSON.parse(message.slice(start + FENCE.length, end).trim()) as Record<string, unknown>;
    if (parsed?.kind !== 'sealed' || typeof parsed.iv !== 'string' || typeof parsed.ct !== 'string') return null;
    return { iv: parsed.iv, ct: parsed.ct };
  } catch {
    return null;
  }
}

export function isSealedMessage(raw: string | null | undefined): boolean {
  return readSealed(raw) !== null;
}

/** Open a message; null when it is not sealed, was made with another key, or was tampered with. */
export async function openMessage(key: CryptoKey, raw: string | null | undefined): Promise<PrivatePayload | null> {
  const sealed = readSealed(raw);
  if (!sealed) return null;
  try {
    const plain = await decrypt(key, fromBase64(sealed.iv), fromBase64(sealed.ct));
    const payload = JSON.parse(decoder.decode(plain)) as PrivatePayload;
    if (payload?.t === 'text' && typeof payload.text === 'string') return { t: 'text', text: payload.text };
    const stamp = (value: unknown) => (typeof value === 'number' && Number.isFinite(value) && value > 0 ? Math.floor(value) : undefined);
    if (payload?.t === 'image' && typeof payload.blob === 'string' && typeof payload.mime === 'string') {
      const sentAt = stamp(payload.sent_at);
      const takenAt = stamp(payload.taken_at);
      return {
        t: 'image',
        blob: payload.blob,
        mime: payload.mime,
        ...(typeof payload.name === 'string' ? { name: payload.name } : {}),
        ...(typeof payload.caption === 'string' ? { caption: payload.caption } : {}),
        ...(sentAt ? { sent_at: sentAt } : {}),
        ...(takenAt ? { taken_at: takenAt } : {}),
        ...(isSha256(payload.sha256) ? { sha256: payload.sha256 } : {}),
      };
    }
    if (payload?.t === 'added' && typeof payload.blob === 'string' && typeof payload.mime === 'string') return readAdded(payload, stamp);
    if (payload?.t === 'video') return readVideo(payload, stamp);
    if (payload?.t === 'comment' && typeof payload.blob === 'string' && typeof payload.text === 'string' && payload.text.trim()) {
      const sentAt = stamp(payload.sent_at);
      return { t: 'comment', blob: payload.blob, text: payload.text, ...(sentAt ? { sent_at: sentAt } : {}) };
    }
    return null;
  } catch {
    return null;
  }
}

const BLOB_ID = /^[0-9a-f]{32}$/;

/** A video message, or null when any part of it is not the right shape (the player could not play it anyway). */
function readVideo(raw: VideoPayload, stamp: (value: unknown) => number | undefined): VideoPayload | null {
  const whole = (value: unknown) => (typeof value === 'number' && Number.isInteger(value) && value > 0 ? value : undefined);
  if (!VIDEO_TYPES.has(raw.mime) || typeof raw.poster !== 'string' || !BLOB_ID.test(raw.poster)) return null;
  if (!Array.isArray(raw.chunks) || !raw.chunks.length || raw.chunks.length > MAX_VIDEO_CHUNKS) return null;
  if (!raw.chunks.every((id) => typeof id === 'string' && BLOB_ID.test(id))) return null;
  const size = whole(raw.size);
  const chunkBytes = whole(raw.chunk_bytes);
  if (!size || !chunkBytes || chunkBytes > VIDEO_CHUNK_BYTES || Math.ceil(size / chunkBytes) !== raw.chunks.length) return null;
  try {
    if (typeof raw.clip !== 'string' || fromBase64(raw.clip).length !== VIDEO_CLIP_ID_BYTES) return null;
  } catch {
    return null;
  }
  const duration = typeof raw.duration === 'number' && Number.isFinite(raw.duration) && raw.duration > 0 ? raw.duration : 0;
  const out: VideoPayload = { t: 'video', mime: raw.mime, size, duration, poster: raw.poster, chunks: [...raw.chunks], clip: raw.clip, chunk_bytes: chunkBytes };
  const width = whole(raw.width);
  const height = whole(raw.height);
  if (width && height) Object.assign(out, { width, height });
  if (typeof raw.name === 'string' && raw.name) out.name = raw.name.slice(0, 500);
  const sentAt = stamp(raw.sent_at);
  const takenAt = stamp(raw.taken_at);
  if (sentAt) out.sent_at = sentAt;
  if (takenAt) out.taken_at = takenAt;
  return out;
}

function isSha256(value: unknown): value is string {
  return typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
}

/** An added picture's entry, keeping only fields of the right shape. */
function readAdded(raw: AddedPicture, stamp: (value: unknown) => number | undefined): AddedPicture {
  const text = (value: unknown, max = 500) => (typeof value === 'string' && value ? value.slice(0, max) : undefined);
  const num = (value: unknown) => (typeof value === 'number' && Number.isFinite(value) && value > 0 ? Math.floor(value) : undefined);
  const out: AddedPicture = { t: 'added', blob: raw.blob, mime: raw.mime };
  const fields: Partial<AddedPicture> = {
    name: text(raw.name),
    path: text(raw.path, 1000),
    caption: text(raw.caption, 2000),
    taken_at: stamp(raw.taken_at),
    taken_from: raw.taken_from === 'exif' || raw.taken_from === 'name' || raw.taken_from === 'file' ? raw.taken_from : undefined,
    added_at: stamp(raw.added_at),
    file_time: stamp(raw.file_time),
    sha256: isSha256(raw.sha256) ? raw.sha256 : undefined,
    size: num(raw.size),
    source: raw.source === 'phone' || raw.source === 'between' || raw.source === 'zip' ? raw.source : undefined,
    camera: text(raw.camera, 200),
    width: num(raw.width),
    height: num(raw.height),
  };
  const gps = raw.gps;
  if (gps && typeof gps.lat === 'number' && typeof gps.lon === 'number' && Number.isFinite(gps.lat) && Number.isFinite(gps.lon)) {
    fields.gps = { lat: gps.lat, lon: gps.lon, ...(typeof gps.alt === 'number' && Number.isFinite(gps.alt) ? { alt: gps.alt } : {}) };
  }
  for (const [key, value] of Object.entries(fields)) if (value !== undefined) (out as unknown as Record<string, unknown>)[key] = value;
  return out;
}

/** A picture's bytes encrypted for upload: IV first, then the ciphertext. */
export async function encryptBytes(key: CryptoKey, data: Uint8Array<ArrayBuffer>): Promise<Uint8Array<ArrayBuffer>> {
  const { iv, ct } = await encrypt(key, data);
  const out = new Uint8Array(iv.length + ct.length);
  out.set(iv, 0);
  out.set(ct, iv.length);
  return out;
}

export async function decryptBytes(key: CryptoKey, data: Uint8Array<ArrayBuffer>): Promise<Uint8Array<ArrayBuffer>> {
  if (data.length <= IV_BYTES) throw new Error('Not an encrypted picture.');
  return decrypt(key, data.slice(0, IV_BYTES), data.slice(IV_BYTES));
}

/** Only these picture types are shown (decrypted into an object URL of that type). */
export const IMAGE_TYPES = new Set(['image/jpeg', 'image/png', 'image/gif', 'image/webp', 'image/heic', 'image/heif']);

// --- Video clips ----------------------------------------------------------------

/** Only these video types are sent and played (decrypted into an object URL of that type). */
export const VIDEO_TYPES = new Set(['video/mp4', 'video/webm', 'video/quicktime', 'video/3gpp']);
/**
 * One encrypted piece of a clip: 4 MiB of video, plus its IV and tag. Small
 * enough for one request through the Android bridge (which carries a body as
 * base64), far below the gateway's 25 MB blob limit.
 */
export const VIDEO_CHUNK_BYTES = 4 * 1024 * 1024;
/**
 * The largest clip, picked or recorded. The whole clip is in memory twice at
 * most while it plays (the pieces as they are decrypted, then the one Blob
 * they make), so it is kept well within what a phone's browser holds in
 * memory: 50 MiB is three minutes of what the in-app recorder makes, or about
 * half a minute of a phone camera's own 1080p video.
 */
export const MAX_VIDEO_BYTES = 50 * 1024 * 1024;
/** The most pieces a clip can have. */
export const MAX_VIDEO_CHUNKS = Math.ceil(MAX_VIDEO_BYTES / VIDEO_CHUNK_BYTES);
/** The clip's random id, bound into every piece. */
export const VIDEO_CLIP_ID_BYTES = 16;
const VIDEO_AAD_LABEL = 'jarvis-private-video-v1';

/** A clip that is not whole: a piece missing, out of place, from another clip, or changed. */
export class VideoIntegrityError extends Error {
  constructor(message = 'This video could not be decrypted (a piece is missing or was changed).') {
    super(message);
    this.name = 'VideoIntegrityError';
  }
}

/** What one piece is bound to: the label, the clip id, its index, how many pieces, and the clip's size. */
export function videoChunkAad(clip: Uint8Array, index: number, count: number, size: number): Uint8Array<ArrayBuffer> {
  if (clip.length !== VIDEO_CLIP_ID_BYTES) throw new VideoIntegrityError('That is not a clip id.');
  const label = encoder.encode(VIDEO_AAD_LABEL);
  const out = new Uint8Array(label.length + VIDEO_CLIP_ID_BYTES + 16);
  out.set(label, 0);
  out.set(clip, label.length);
  const view = new DataView(out.buffer, label.length + VIDEO_CLIP_ID_BYTES);
  view.setUint32(0, index);
  view.setUint32(4, count);
  view.setBigUint64(8, BigInt(size));
  return out;
}

/** One piece encrypted: IV first, then the ciphertext (with the piece's place bound in). */
export async function encryptVideoChunk(
  key: CryptoKey,
  clip: Uint8Array,
  index: number,
  count: number,
  size: number,
  plain: Uint8Array<ArrayBuffer>,
): Promise<Uint8Array<ArrayBuffer>> {
  const iv = randomBytes(IV_BYTES);
  const ct = new Uint8Array(
    await subtle().encrypt({ name: 'AES-GCM', iv, additionalData: videoChunkAad(clip, index, count, size) }, key, plain),
  );
  const out = new Uint8Array(IV_BYTES + ct.length);
  out.set(iv, 0);
  out.set(ct, IV_BYTES);
  return out;
}

/** One piece decrypted, or VideoIntegrityError when it is not this clip's piece `index` of `count`. */
export async function decryptVideoChunk(
  key: CryptoKey,
  clip: Uint8Array,
  index: number,
  count: number,
  size: number,
  data: Uint8Array<ArrayBuffer>,
): Promise<Uint8Array<ArrayBuffer>> {
  if (data.length <= IV_BYTES + 16) throw new VideoIntegrityError();
  try {
    return new Uint8Array(
      await subtle().decrypt(
        { name: 'AES-GCM', iv: data.slice(0, IV_BYTES), additionalData: videoChunkAad(clip, index, count, size) },
        key,
        data.slice(IV_BYTES),
      ),
    );
  } catch {
    throw new VideoIntegrityError();
  }
}

/** Where a clip's pieces went: the clip id, the blob ids in order, and the sizes. */
export interface SealedVideo {
  clip: string;
  chunks: string[];
  size: number;
  chunk_bytes: number;
}

/**
 * Encrypt a clip piece by piece and hand each piece to `upload` (which returns
 * its blob id). Only one piece of plaintext is read into memory at a time and
 * it is zeroed once encrypted. `chunkBytes` is for tests.
 */
export async function encryptVideo(
  key: CryptoKey,
  source: Blob,
  upload: (sealed: Uint8Array<ArrayBuffer>, index: number, count: number) => Promise<string>,
  { chunkBytes = VIDEO_CHUNK_BYTES, maxBytes = MAX_VIDEO_BYTES, onProgress }: { chunkBytes?: number; maxBytes?: number; onProgress?: (done: number, count: number) => void } = {},
): Promise<SealedVideo> {
  const size = source.size;
  if (!size) throw new Error('That video is empty.');
  if (size > maxBytes) throw new Error(`Videos are limited to ${Math.round(maxBytes / (1024 * 1024))} MB.`);
  const count = Math.ceil(size / chunkBytes);
  const clipBytes = randomBytes(VIDEO_CLIP_ID_BYTES);
  const chunks: string[] = [];
  for (let index = 0; index < count; index += 1) {
    const plain = new Uint8Array(await source.slice(index * chunkBytes, Math.min(size, (index + 1) * chunkBytes)).arrayBuffer());
    let sealed: Uint8Array<ArrayBuffer>;
    try {
      sealed = await encryptVideoChunk(key, clipBytes, index, count, size, plain);
    } finally {
      plain.fill(0);
    }
    chunks.push(await upload(sealed, index, count));
    onProgress?.(index + 1, count);
  }
  return { clip: toBase64(clipBytes), chunks, size, chunk_bytes: chunkBytes };
}

/**
 * Fetch and decrypt a clip one piece at a time, into one Blob of `mime`. Every
 * piece must be this clip's, in its place, of the length its place calls for;
 * otherwise VideoIntegrityError and nothing is returned.
 */
export async function decryptVideo(
  key: CryptoKey,
  video: Pick<VideoPayload, 'mime' | 'size' | 'chunks' | 'clip' | 'chunk_bytes'>,
  fetchChunk: (id: string, index: number) => Promise<Uint8Array<ArrayBuffer>>,
  onProgress?: (done: number, count: number) => void,
): Promise<Blob> {
  const count = video.chunks.length;
  if (!count || count > MAX_VIDEO_CHUNKS || video.chunk_bytes <= 0 || Math.ceil(video.size / video.chunk_bytes) !== count) {
    throw new VideoIntegrityError();
  }
  const clip = fromBase64(video.clip);
  const parts: Uint8Array<ArrayBuffer>[] = [];
  try {
    for (let index = 0; index < count; index += 1) {
      const plain = await decryptVideoChunk(key, clip, index, count, video.size, await fetchChunk(video.chunks[index], index));
      const expected = index < count - 1 ? video.chunk_bytes : video.size - video.chunk_bytes * (count - 1);
      parts.push(plain);
      if (plain.length !== expected) throw new VideoIntegrityError();
      onProgress?.(index + 1, count);
    }
    return new Blob(parts, { type: VIDEO_TYPES.has(video.mime) ? video.mime : 'application/octet-stream' });
  } finally {
    // The Blob holds its own copy: the pieces are wiped either way.
    for (const part of parts) part.fill(0);
  }
}

/**
 * The key and everything decrypted with it, for one open chat. Lives in memory
 * only; `lock()` forgets all of it and revokes every object URL it made.
 */
export class PrivateVault {
  private key: CryptoKey | null = null;
  private urls = new Set<string>();
  private listeners = new Set<() => void>();

  get unlocked(): boolean {
    return this.key !== null;
  }

  /** Unlock with a password; false (and still locked) when the verifier says it is wrong. */
  async unlock(password: string, info: ChatKeyInfo): Promise<boolean> {
    const key = await unlockKey(password, info);
    if (!key) return false;
    this.key = key;
    this.emit();
    return true;
  }

  /** Hold a key just made for a new chat, or one the phone's lock gave back. */
  adopt(key: CryptoKey): void {
    this.key = key;
    this.emit();
  }

  private need(): CryptoKey {
    if (!this.key) throw new Error('The chat is locked.');
    return this.key;
  }

  async seal(payload: PrivatePayload): Promise<string> {
    return sealMessage(this.need(), payload);
  }

  async open(raw: string | null | undefined): Promise<PrivatePayload | null> {
    return openMessage(this.need(), raw);
  }

  /** The key that verifies this chat's calls, derived here (lib/callVerify.ts); the chat's key never leaves. */
  async callKey(): Promise<CryptoKey> {
    return deriveCallKey(this.need());
  }

  /** Seal any record (Beloved) with the chat's key. */
  async sealRecord(record: object): Promise<string> {
    return sealRecord(this.need(), record);
  }

  async openRecord(raw: string | null | undefined): Promise<unknown> {
    return openRecord(this.need(), raw);
  }

  async encrypt(data: Uint8Array<ArrayBuffer>): Promise<Uint8Array<ArrayBuffer>> {
    return encryptBytes(this.need(), data);
  }

  /** A decrypted picture as an object URL, revoked on lock. */
  async pictureUrl(data: Uint8Array<ArrayBuffer>, mime: string): Promise<string> {
    const plain = await decryptBytes(this.need(), data);
    if (!this.key) throw new Error('The chat is locked.');
    const url = URL.createObjectURL(new Blob([plain], { type: IMAGE_TYPES.has(mime) ? mime : 'application/octet-stream' }));
    this.urls.add(url);
    return url;
  }

  /** Encrypt a clip piece by piece, handing each piece to `upload`. */
  async encryptVideo(
    source: Blob,
    upload: (sealed: Uint8Array<ArrayBuffer>, index: number, count: number) => Promise<string>,
    onProgress?: (done: number, count: number) => void,
  ): Promise<SealedVideo> {
    return encryptVideo(this.need(), source, upload, { onProgress });
  }

  /**
   * A decrypted clip as an object URL (in memory), revoked by `revoke`, on
   * lock and when the chat leaves the screen. Throws VideoIntegrityError when a
   * piece is missing or changed.
   */
  async videoUrl(
    video: VideoPayload,
    fetchChunk: (id: string, index: number) => Promise<Uint8Array<ArrayBuffer>>,
    onProgress?: (done: number, count: number) => void,
  ): Promise<string> {
    const blob = await decryptVideo(this.need(), video, fetchChunk, onProgress);
    if (!this.key) throw new Error('The chat is locked.');
    const url = URL.createObjectURL(blob);
    this.urls.add(url);
    return url;
  }

  /** Let go of one object URL now (a player closed), rather than at the next lock. */
  revoke(url: string): void {
    if (!this.urls.delete(url)) return;
    URL.revokeObjectURL(url);
  }

  /** How many object URLs are live (for tests and the lock check). */
  get liveUrls(): number {
    return this.urls.size;
  }

  /** Bumped whenever decrypted pictures are released, so a screen redraws its pictures. */
  epoch = 0;

  /** Revoke every decrypted picture's object URL but keep the key (the chat went off the screen). */
  releasePictures(): void {
    if (!this.urls.size) return;
    for (const url of this.urls) URL.revokeObjectURL(url);
    this.urls.clear();
    this.epoch += 1;
    this.emit();
  }

  lock(): void {
    this.key = null;
    for (const url of this.urls) URL.revokeObjectURL(url);
    this.urls.clear();
    this.epoch += 1;
    this.emit();
  }

  subscribe(fn: () => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  private emit(): void {
    for (const fn of this.listeners) fn();
  }
}
