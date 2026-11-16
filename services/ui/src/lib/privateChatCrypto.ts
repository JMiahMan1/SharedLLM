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
 * Every message is a fresh 12-byte IV and its ciphertext, posted to Talk as a
 * `sealed` envelope after a short line other clients show instead
 * ("🔒 Encrypted message"). A picture is encrypted the same way (IV first,
 * then the ciphertext) and kept by the gateway as an opaque blob.
 *
 * `PrivateVault` holds the key for one open chat. `lock()` drops the key, every
 * decrypted message and every picture's object URL; nothing survives a lock.
 */

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
   * sealed gallery entry, not in Talk. The original bytes are in the blob
   * untouched; these are the facts read from them, for placing it in time.
   */
  | AddedPicture
  /** A comment on a picture in the chat's gallery, naming the picture's blob. */
  | { t: 'comment'; blob: string; text: string; sent_at?: number };

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

/** A message as Talk carries it: a short readable line and the sealed envelope. */
export async function sealMessage(key: CryptoKey, payload: PrivatePayload): Promise<string> {
  const { iv, ct } = await encrypt(key, encoder.encode(JSON.stringify(payload)));
  const envelope = { kind: 'sealed', title: 'Encrypted message', v: SEALED_VERSION, iv: toBase64(iv), ct: toBase64(ct) };
  return `${SEALED_LINE}\n\n${FENCE}\n${JSON.stringify(envelope)}\n${FENCE}`;
}

/** The sealed envelope in a raw Talk message, or null when it carries none. */
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
    if (payload?.t === 'comment' && typeof payload.blob === 'string' && typeof payload.text === 'string' && payload.text.trim()) {
      const sentAt = stamp(payload.sent_at);
      return { t: 'comment', blob: payload.blob, text: payload.text, ...(sentAt ? { sent_at: sentAt } : {}) };
    }
    return null;
  } catch {
    return null;
  }
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
