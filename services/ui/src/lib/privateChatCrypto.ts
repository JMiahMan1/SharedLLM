/**
 * The private chat's end-to-end encryption, on the device only (WebCrypto).
 *
 * The parents agree on a password in person. PBKDF2-SHA256 (600,000 rounds or
 * more, a random 16-byte salt per chat) turns it into an AES-GCM-256 key that
 * is never extractable, never stored and never sent. The server keeps the
 * salt, the round count and a *verifier*: the AES-GCM encryption of a fixed
 * constant, so a wrong password is caught here without the server learning
 * anything. A forgotten password cannot be recovered by anyone.
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

/** What a message says once opened. */
export type PrivatePayload =
  | { t: 'text'; text: string }
  | { t: 'image'; blob: string; mime: string; name?: string; caption?: string };

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

/** The chat key from the password: AES-GCM-256, usable only to encrypt and decrypt, never exported. */
export async function deriveKey(password: string, params: KdfParams): Promise<CryptoKey> {
  if (!password) throw new Error('Enter the password.');
  if (!Number.isInteger(params.iterations) || params.iterations < PBKDF2_ITERATIONS) {
    throw new Error('This chat asks for a key stretch that is too weak.');
  }
  const base = await subtle().importKey('raw', encoder.encode(password), 'PBKDF2', false, ['deriveKey']);
  return subtle().deriveKey(
    { name: 'PBKDF2', hash: 'SHA-256', salt: fromBase64(params.salt), iterations: params.iterations },
    base,
    { name: 'AES-GCM', length: 256 },
    false,
    ['encrypt', 'decrypt'],
  );
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

/** A new chat's public parameters and verifier for a password, plus its key. */
export async function newChatSecrets(password: string): Promise<{ salt: string; iterations: number; verifier: Sealed; key: CryptoKey }> {
  const salt = toBase64(randomBytes(SALT_BYTES));
  const key = await deriveKey(password, { salt, iterations: PBKDF2_ITERATIONS });
  return { salt, iterations: PBKDF2_ITERATIONS, verifier: await makeVerifier(key), key };
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
    if (payload?.t === 'image' && typeof payload.blob === 'string' && typeof payload.mime === 'string') {
      return {
        t: 'image',
        blob: payload.blob,
        mime: payload.mime,
        ...(typeof payload.name === 'string' ? { name: payload.name } : {}),
        ...(typeof payload.caption === 'string' ? { caption: payload.caption } : {}),
      };
    }
    return null;
  } catch {
    return null;
  }
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
  async unlock(password: string, params: KdfParams, verifier: Sealed): Promise<boolean> {
    const key = await deriveKey(password, params);
    if (!(await checkVerifier(key, verifier))) return false;
    this.key = key;
    this.emit();
    return true;
  }

  /** Hold a key just made for a new chat. */
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

  lock(): void {
    this.key = null;
    for (const url of this.urls) URL.revokeObjectURL(url);
    this.urls.clear();
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
