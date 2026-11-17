/**
 * The encrypted account export file (docs/ACCOUNT_EXPORT.md): made and opened
 * on the device, so the server never sees the password or the key.
 *
 * File format (version 1), integers big-endian:
 *
 *     MAGIC          12 bytes  "JARVISACCT" 0x00 0x01
 *     header length   4 bytes
 *     header         JSON (UTF-8): format, version, kdf {name, iterations, salt},
 *                    cipher, chunk_bytes, nonce_prefix, compression, content,
 *                    created_at, check
 *     chunk 0 .. n   AES-256-GCM ciphertext + 16-byte tag each
 *
 * - Key: PBKDF2-SHA256(password, 16-byte salt, 600,000 rounds or more) to a
 *   256-bit AES-GCM key. WebCrypto has no Argon2id or scrypt, and a WASM
 *   library would be a new dependency in every build; PBKDF2 at this count is
 *   what the private chat uses and what the self-contained page can repeat.
 * - check: AES-GCM of 16 zero bytes under the key with nonce prefix || 2^64-1
 *   and associated data CHECK_AAD, so a wrong password is told apart from a
 *   damaged file before any chunk is read.
 * - The payload (NDJSON, gzip-compressed when the device can) is cut into
 *   chunks of chunk_bytes. Chunk i's nonce is prefix || i (8 bytes); its
 *   associated data is sha256(MAGIC || header length || header) || i || final
 *   (one byte, 1 only for the last chunk). Every chunk but the last is exactly
 *   chunk_bytes long and the last is always shorter (empty when the payload is
 *   an exact multiple), so a changed header, a changed, reordered, dropped or
 *   duplicated chunk, a truncation or trailing bytes are all refused.
 */

export const MAGIC = new Uint8Array([0x4a, 0x41, 0x52, 0x56, 0x49, 0x53, 0x41, 0x43, 0x43, 0x54, 0x00, 0x01]);
export const FORMAT = 'jarvis-account-export';
export const VERSION = 1;
export const FILE_SUFFIX = '.jarvis-export';
export const CHUNK_BYTES = 1024 * 1024;
export const TAG_BYTES = 16;
export const PBKDF2_ITERATIONS = 600_000;
export const MAX_HEADER_BYTES = 64 * 1024;
const SALT_BYTES = 16;
const PREFIX_BYTES = 4;
const CHECK_AAD = new TextEncoder().encode('jarvis-account-export/v1/check');
/** Fold the ciphertext into one Blob every so often, so a browser can keep it on disk. */
const FOLD_BYTES = 16 * 1024 * 1024;

export interface ExportHeader {
  format: string;
  version: number;
  kdf: { name: 'PBKDF2-SHA256'; iterations: number; salt: string };
  cipher: 'AES-256-GCM';
  chunk_bytes: number;
  nonce_prefix: string;
  compression: 'gzip' | 'none';
  content: 'application/x-ndjson';
  created_at: string;
  check: string;
}

/** The password does not open this file. */
export class WrongPasswordError extends Error {
  constructor() {
    super('That password does not open this file.');
    this.name = 'WrongPasswordError';
  }
}

/** The file is not an export, or was changed or cut short after it was made. */
export class DamagedFileError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'DamagedFileError';
  }
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
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}

export function fromBase64(text: string): Uint8Array<ArrayBuffer> {
  const binary = atob(text);
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) out[i] = binary.charCodeAt(i);
  return out;
}

function concat(...parts: Uint8Array[]): Uint8Array<ArrayBuffer> {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let at = 0;
  for (const p of parts) {
    out.set(p, at);
    at += p.length;
  }
  return out;
}

function u32(n: number): Uint8Array {
  const out = new Uint8Array(4);
  new DataView(out.buffer).setUint32(0, n);
  return out;
}

function u64(n: number | bigint): Uint8Array {
  const out = new Uint8Array(8);
  new DataView(out.buffer).setBigUint64(0, BigInt(n));
  return out;
}

const CHECK_INDEX = (1n << 64n) - 1n;

function nonce(prefix: Uint8Array, index: number | bigint): Uint8Array<ArrayBuffer> {
  return concat(prefix, u64(index));
}

function aad(headerHash: Uint8Array, index: number, final: boolean): Uint8Array<ArrayBuffer> {
  return concat(headerHash, u64(index), new Uint8Array([final ? 1 : 0]));
}

async function deriveKey(password: string, salt: Uint8Array<ArrayBuffer>, iterations: number): Promise<CryptoKey> {
  if (!password) throw new Error('Enter the password.');
  const base = await subtle().importKey('raw', encoder.encode(password), 'PBKDF2', false, ['deriveKey']);
  return subtle().deriveKey(
    { name: 'PBKDF2', hash: 'SHA-256', salt, iterations },
    base,
    { name: 'AES-GCM', length: 256 },
    false,
    ['encrypt', 'decrypt'],
  );
}

export function compressionAvailable(): boolean {
  return typeof CompressionStream === 'function' && typeof DecompressionStream === 'function';
}

export interface EncryptOptions {
  iterations?: number;
  chunkBytes?: number;
  compress?: boolean;
  /** Called with the payload bytes read so far (before compression). */
  onProgress?: (plainBytes: number) => void;
}

/**
 * Encrypt a stream (the export as the server sends it) into the file's bytes.
 * The stream is read once, a chunk at a time; nothing clear is kept beyond the
 * chunk being encrypted.
 */
export async function encryptStream(source: ReadableStream<Uint8Array>, password: string, options: EncryptOptions = {}): Promise<Blob> {
  const iterations = options.iterations ?? PBKDF2_ITERATIONS;
  const chunkBytes = options.chunkBytes ?? CHUNK_BYTES;
  if (iterations < PBKDF2_ITERATIONS) throw new Error('The key stretch is too weak.');
  const compress = (options.compress ?? true) && compressionAvailable();
  const salt = globalThis.crypto.getRandomValues(new Uint8Array(SALT_BYTES));
  const prefix = globalThis.crypto.getRandomValues(new Uint8Array(PREFIX_BYTES));
  const key = await deriveKey(password, salt, iterations);
  const check = new Uint8Array(
    await subtle().encrypt({ name: 'AES-GCM', iv: nonce(prefix, CHECK_INDEX), additionalData: CHECK_AAD }, key, new Uint8Array(16)),
  );
  const header: ExportHeader = {
    format: FORMAT,
    version: VERSION,
    kdf: { name: 'PBKDF2-SHA256', iterations, salt: toBase64(salt) },
    cipher: 'AES-256-GCM',
    chunk_bytes: chunkBytes,
    nonce_prefix: toBase64(prefix),
    compression: compress ? 'gzip' : 'none',
    content: 'application/x-ndjson',
    created_at: new Date().toISOString(),
    check: toBase64(check),
  };
  const headerBytes = encoder.encode(JSON.stringify(header));
  const preamble = concat(MAGIC, u32(headerBytes.length), headerBytes);
  const headerHash = new Uint8Array(await subtle().digest('SHA-256', preamble));

  let read = 0;
  const counted = source.pipeThrough(
    new TransformStream<Uint8Array, Uint8Array>({
      transform(chunk, controller) {
        read += chunk.length;
        options.onProgress?.(read);
        controller.enqueue(chunk);
      },
    }),
  );
  const payload = compress ? counted.pipeThrough(new CompressionStream('gzip') as unknown as TransformStream<Uint8Array, Uint8Array>) : counted;

  let folded = new Blob([preamble]);
  let parts: BlobPart[] = [];
  let partBytes = 0;
  let index = 0;
  const seal = async (plain: Uint8Array<ArrayBuffer>, final: boolean) => {
    const ct = await subtle().encrypt({ name: 'AES-GCM', iv: nonce(prefix, index), additionalData: aad(headerHash, index, final) }, key, plain);
    parts.push(ct);
    partBytes += ct.byteLength;
    index += 1;
    if (partBytes >= FOLD_BYTES) {
      folded = new Blob([folded, ...parts]);
      parts = [];
      partBytes = 0;
    }
  };

  const buffer = new Uint8Array(chunkBytes);
  let filled = 0;
  const reader = payload.getReader();
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    let at = 0;
    while (at < value.length) {
      const take = Math.min(chunkBytes - filled, value.length - at);
      buffer.set(value.subarray(at, at + take), filled);
      filled += take;
      at += take;
      // A full chunk is never the last one: the last is always shorter.
      if (filled === chunkBytes) {
        await seal(buffer.slice(), false);
        filled = 0;
      }
    }
  }
  await seal(buffer.slice(0, filled), true);
  buffer.fill(0);
  return new Blob([folded, ...parts], { type: 'application/octet-stream' });
}

async function readBytes(file: Blob, start: number, end: number): Promise<Uint8Array<ArrayBuffer>> {
  return new Uint8Array(await file.slice(start, end).arrayBuffer());
}

/** The header of an export file, checked for shape (it is readable without the password). */
export async function readHeader(file: Blob): Promise<{ header: ExportHeader; bodyOffset: number; headerHash: Uint8Array<ArrayBuffer> }> {
  const head = await readBytes(file, 0, MAGIC.length + 4);
  if (head.length < MAGIC.length + 4 || MAGIC.some((b, i) => head[i] !== b)) {
    throw new DamagedFileError('This is not a Jarvis export file.');
  }
  const length = new DataView(head.buffer).getUint32(MAGIC.length);
  if (length < 2 || length > MAX_HEADER_BYTES) throw new DamagedFileError('This export file is damaged (its header is the wrong size).');
  const headerBytes = await readBytes(file, MAGIC.length + 4, MAGIC.length + 4 + length);
  if (headerBytes.length !== length) throw new DamagedFileError('This export file is cut short.');
  let header: ExportHeader;
  try {
    header = JSON.parse(decoder.decode(headerBytes)) as ExportHeader;
  } catch {
    throw new DamagedFileError('This export file is damaged (its header is not readable).');
  }
  if (header?.format !== FORMAT) throw new DamagedFileError('This is not a Jarvis account export.');
  if (header.version !== VERSION) throw new DamagedFileError(`This export was made by a newer Jarvis (format ${String(header.version)}).`);
  if (header.kdf?.name !== 'PBKDF2-SHA256' || !Number.isInteger(header.kdf.iterations) || header.kdf.iterations < PBKDF2_ITERATIONS) {
    throw new DamagedFileError('This export file asks for a key stretch that is too weak.');
  }
  if (header.cipher !== 'AES-256-GCM' || !Number.isInteger(header.chunk_bytes) || header.chunk_bytes < 1024 || header.chunk_bytes > 64 * 1024 * 1024) {
    throw new DamagedFileError('This export file is damaged (unknown cipher or chunk size).');
  }
  if (header.compression !== 'gzip' && header.compression !== 'none') throw new DamagedFileError('This export file is damaged (unknown compression).');
  const preamble = concat(head, headerBytes);
  const headerHash = new Uint8Array(await subtle().digest('SHA-256', preamble));
  return { header, bodyOffset: preamble.length, headerHash };
}

/**
 * Open an export: check the password, then give back the clear payload as a
 * stream that decrypts one chunk at a time as it is read. A chunk that fails
 * its check ends the stream with DamagedFileError.
 */
export async function openExport(file: Blob, password: string): Promise<{ header: ExportHeader; stream: ReadableStream<Uint8Array> }> {
  const { header, bodyOffset, headerHash } = await readHeader(file);
  if (header.compression === 'gzip' && !compressionAvailable()) {
    throw new Error('This browser cannot open a compressed export. Update it, or open the file on another device.');
  }
  let salt: Uint8Array<ArrayBuffer>;
  let prefix: Uint8Array<ArrayBuffer>;
  let check: Uint8Array<ArrayBuffer>;
  try {
    salt = fromBase64(header.kdf.salt);
    prefix = fromBase64(header.nonce_prefix);
    check = fromBase64(header.check);
  } catch {
    throw new DamagedFileError('This export file is damaged (its header is not readable).');
  }
  if (prefix.length !== PREFIX_BYTES) throw new DamagedFileError('This export file is damaged (bad nonce).');
  const key = await deriveKey(password, salt, header.kdf.iterations);
  try {
    await subtle().decrypt({ name: 'AES-GCM', iv: nonce(prefix, CHECK_INDEX), additionalData: CHECK_AAD }, key, check);
  } catch {
    throw new WrongPasswordError();
  }

  const step = header.chunk_bytes + TAG_BYTES;
  let offset = bodyOffset;
  let index = 0;
  let finished = false;
  const decrypted = new ReadableStream<Uint8Array>({
    async pull(controller) {
      if (finished) return;
      const remaining = file.size - offset;
      if (remaining < TAG_BYTES) {
        controller.error(new DamagedFileError('This export file is cut short.'));
        return;
      }
      // A full-size piece is never the last chunk; a shorter one always is.
      const final = remaining < step;
      const piece = await readBytes(file, offset, offset + Math.min(step, remaining));
      if (!final && remaining === step) {
        controller.error(new DamagedFileError('This export file is cut short.'));
        return;
      }
      try {
        const plain = await subtle().decrypt(
          { name: 'AES-GCM', iv: nonce(prefix, index), additionalData: aad(headerHash, index, final) },
          key,
          piece,
        );
        offset += piece.length;
        index += 1;
        if (plain.byteLength) controller.enqueue(new Uint8Array(plain));
        if (final) {
          finished = true;
          controller.close();
        }
      } catch {
        controller.error(new DamagedFileError('This export file was changed or damaged after it was made.'));
      }
    },
  });
  const stream = header.compression === 'gzip'
    ? decrypted.pipeThrough(new DecompressionStream('gzip') as unknown as TransformStream<Uint8Array, Uint8Array>)
    : decrypted;
  return { header, stream };
}
