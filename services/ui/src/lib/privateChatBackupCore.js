/**
 * Opening an encrypted private chat backup on this device, and nowhere else.
 *
 * One implementation, used twice: the app imports it (lib/privateChatBackupFile.ts),
 * and the self-contained "encrypted page" embeds this very file's text
 * (lib/privateChatKeepsakePage.ts), so a saved keepsake opens with the same
 * code the app uses. It is plain JavaScript with no imports for that reason;
 * its types are in privateChatBackupCore.d.ts. It needs WebCrypto and Blob,
 * nothing else, and never touches the network.
 *
 * The file format is scripts/private_chat_backup.py's (version 1):
 *
 *   MAGIC "JARVISPCBK\0\1" | header length (4 bytes, big-endian) | header JSON | chunks
 *
 * 1. The chat password opens the chat's data key DK from one of the header's
 *    key records (v1: PBKDF2 is DK; v2: PBKDF2 gives a KEK that unwraps DK),
 *    checked against the record's verifier.
 * 2. DK opens the sealed backup private key (X25519 or P-256).
 * 3. ECDH with the header's ephemeral public key, then HKDF-SHA256, give the
 *    file's AES-256-GCM key and a 4-byte nonce prefix.
 * 4. Every 1 MiB chunk is decrypted with nonce prefix||index and associated
 *    data sha256(header)||index||final; a changed, moved, dropped or added
 *    chunk, a cut, trailing bytes or a changed header is refused, and nothing
 *    is returned unless every chunk was whole.
 * 5. The plaintext is a tar of the chat's folder; it is read as it is
 *    decrypted, into Blobs (still sealed: messages, pictures and Beloved stay
 *    encrypted with DK until `readKeepsake` and `decryptBlob` open them).
 */

const MAGIC = new Uint8Array([74, 65, 82, 86, 73, 83, 80, 67, 66, 75, 0, 1]);
const FORMAT = 'jarvis-private-chat-backup';
const FORMAT_VERSION = 1;
const CHUNK_BYTES = 1024 * 1024;
const TAG_BYTES = 16;
const MAX_HEADER_BYTES = 1024 * 1024;
const HKDF_INFO = 'jarvis-private-chat-backup/v1';
const BACKUP_KEY_RECORD = 'backup-key';
const VERIFIER_PLAINTEXT = 'jarvis-private-chat-verifier-v1';
const MIN_ITERATIONS = 600000;
const MAX_ITERATIONS = 10000000;
const VIDEO_AAD_LABEL = 'jarvis-private-video-v1';
const BACKUP_SUFFIX = '.jarvis-backup';
const TOKEN_PATTERN = /^[A-Za-z0-9]{4,64}$/;
const BLOB_PATTERN = /^[0-9a-f]{32}$/;
const FENCE = '```jarvis-envelope';
const IMAGE_MIMES = ['image/jpeg', 'image/png', 'image/gif', 'image/webp', 'image/heic', 'image/heif'];
const VIDEO_MIMES = ['video/mp4', 'video/webm', 'video/quicktime', 'video/3gpp'];
/** Beloved's kinds in the order a keepsake shows them, with a heading for each. */
const BELOVED_SECTIONS = [
  ['profile', 'Our page'],
  ['day', 'Special days'],
  ['note', 'Love notes'],
  ['heart', 'Thinking of you'],
  ['prayer', 'Prayers'],
  ['devotion', 'Devotions'],
  ['idea', 'Date night ideas'],
  ['together', 'Just us: days'],
  ['plan', 'Just us: plans'],
];

class BackupError extends Error {
  /** `code`: 'format' (not a backup, or damaged), 'password' (wrong or none), 'tampered' (a part was changed). */
  constructor(message, code) {
    super(message);
    this.name = 'BackupError';
    this.code = code || 'format';
  }
}

const utf8 = new TextEncoder();

function subtle() {
  const s = globalThis.crypto && globalThis.crypto.subtle;
  if (!s) throw new BackupError('This browser cannot decrypt (no WebCrypto). Open it in a current browser.', 'format');
  return s;
}

function fromBase64(text, field) {
  if (typeof text !== 'string' || !text) throw new BackupError(`The backup is damaged (${field} is missing).`);
  let binary;
  try {
    binary = atob(text);
  } catch {
    throw new BackupError(`The backup is damaged (${field} is not base64).`);
  }
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) out[i] = binary.charCodeAt(i);
  return out;
}

function concat(parts) {
  let size = 0;
  for (const p of parts) size += p.length;
  const out = new Uint8Array(size);
  let at = 0;
  for (const p of parts) {
    out.set(p, at);
    at += p.length;
  }
  return out;
}

function u64(n) {
  const out = new Uint8Array(8);
  new DataView(out.buffer).setBigUint64(0, BigInt(n));
  return out;
}

function decodeText(bytes) {
  return new TextDecoder('utf-8', { fatal: true }).decode(bytes);
}

async function bytesOf(blob, start, end) {
  return new Uint8Array(await blob.slice(start, end).arrayBuffer());
}

// --- The header -----------------------------------------------------------------

/** The header (no secrets in it), the digest every chunk is bound to, and where the chunks start. */
async function readBackupHeader(source) {
  if (!source || typeof source.slice !== 'function') throw new BackupError('Choose a backup file.');
  const lead = await bytesOf(source, 0, MAGIC.length + 4);
  if (lead.length < MAGIC.length + 4 || MAGIC.some((b, i) => lead[i] !== b)) {
    throw new BackupError('This is not a private chat backup.');
  }
  const length = new DataView(lead.buffer).getUint32(MAGIC.length);
  if (length <= 0 || length > MAX_HEADER_BYTES) throw new BackupError('The backup is damaged (its header has an impossible size).');
  const head = await bytesOf(source, lead.length, lead.length + length);
  if (head.length !== length) throw new BackupError('The backup is cut short (its header is incomplete).');
  let header;
  try {
    header = JSON.parse(decodeText(head));
  } catch {
    throw new BackupError('The backup is damaged (its header is not readable).');
  }
  if (!header || header.format !== FORMAT || header.version !== FORMAT_VERSION) {
    throw new BackupError('This backup was made by a newer Jarvis, or is not a private chat backup.');
  }
  if ((header.alg !== 'X25519' && header.alg !== 'P-256') || header.cipher !== 'AES-256-GCM' || header.kdf !== 'HKDF-SHA256') {
    throw new BackupError('This backup uses a method this version cannot open.');
  }
  if (header.chunk_bytes !== CHUNK_BYTES || !TOKEN_PATTERN.test(String(header.token || '')) || !Array.isArray(header.key_records)) {
    throw new BackupError('The backup is damaged (its header is malformed).');
  }
  const digest = new Uint8Array(await subtle().digest('SHA-256', concat([lead, head])));
  return { header, digest, offset: lead.length + length };
}

// --- The password ---------------------------------------------------------------

async function checkVerifier(key, verifier) {
  try {
    const plain = await subtle().decrypt({ name: 'AES-GCM', iv: fromBase64(verifier.iv, 'verifier') }, key, fromBase64(verifier.ct, 'verifier'));
    return decodeText(new Uint8Array(plain)) === VERIFIER_PLAINTEXT;
  } catch {
    return false;
  }
}

/** The chat's data key (never extractable) for a password and one key record, or null when the password does not open it. */
async function dataKeyFor(password, record) {
  const kdf = (record && record.kdf) || {};
  const iterations = kdf.iterations;
  if (!Number.isInteger(iterations) || iterations < MIN_ITERATIONS || iterations > MAX_ITERATIONS) return null;
  if (!record.verifier) return null;
  const salt = fromBase64(kdf.salt, 'kdf.salt');
  const base = await subtle().importKey('raw', utf8.encode(password), 'PBKDF2', false, ['deriveBits', 'deriveKey']);
  let key;
  if (record.version === 2 || record.wrapped_key) {
    if (!record.wrapped_key) return null;
    const bits = new Uint8Array(await subtle().deriveBits({ name: 'PBKDF2', hash: 'SHA-256', salt, iterations }, base, 512));
    try {
      const kek = await subtle().importKey('raw', bits.subarray(0, 32), { name: 'AES-GCM' }, false, ['unwrapKey']);
      key = await subtle().unwrapKey(
        'raw',
        fromBase64(record.wrapped_key.ct, 'wrapped_key'),
        kek,
        { name: 'AES-GCM', iv: fromBase64(record.wrapped_key.iv, 'wrapped_key') },
        { name: 'AES-GCM' },
        false,
        ['decrypt'],
      );
    } catch {
      return null;
    } finally {
      bits.fill(0);
    }
  } else {
    key = await subtle().deriveKey(
      { name: 'PBKDF2', hash: 'SHA-256', salt, iterations },
      base,
      { name: 'AES-GCM', length: 256 },
      false,
      ['decrypt'],
    );
  }
  return (await checkVerifier(key, record.verifier)) ? key : null;
}

/** DK from the header's key records (and any more the caller has, such as the live chat's). */
async function unlockBackup(header, password, moreRecords) {
  if (!password) throw new BackupError('Enter the chat password.', 'password');
  const records = [...(moreRecords || []), ...header.key_records];
  for (const record of records) {
    const key = await dataKeyFor(password, record);
    if (key) return key;
  }
  throw new BackupError('That password does not open this backup.', 'password');
}

function publicKeyParams(alg) {
  return alg === 'X25519' ? { name: 'X25519' } : { name: 'ECDH', namedCurve: 'P-256' };
}

function checkPublic(alg, raw, field) {
  if (alg === 'X25519' ? raw.length !== 32 : raw.length !== 65 || raw[0] !== 4) {
    throw new BackupError(`The backup is damaged (${field} has the wrong size).`);
  }
}

/** The backup private key, unsealed with DK. */
async function backupPrivateKey(header, dk) {
  const sealed = header.sealed_private_key || {};
  let record;
  try {
    const plain = await subtle().decrypt({ name: 'AES-GCM', iv: fromBase64(sealed.iv, 'sealed_private_key') }, dk, fromBase64(sealed.ct, 'sealed_private_key'));
    record = JSON.parse(decodeText(new Uint8Array(plain)));
  } catch (error) {
    if (error instanceof BackupError) throw error;
    throw new BackupError("The chat's key does not open this backup's key: the file was changed.", 'tampered');
  }
  if (!record || record.t !== BACKUP_KEY_RECORD || record.alg !== header.alg || record.public_key !== header.recipient_public_key) {
    throw new BackupError("The backup's key does not belong to it: the file was changed.", 'tampered');
  }
  const der = fromBase64(record.private_key, 'private_key');
  try {
    return await subtle().importKey('pkcs8', der, publicKeyParams(header.alg), false, ['deriveBits']);
  } catch {
    throw new BackupError(
      header.alg === 'X25519'
        ? 'This browser cannot open X25519 keys. Use a current Chrome, Firefox or Safari, or open it in the Jarvis app.'
        : "The backup's key cannot be read.",
      'format',
    );
  } finally {
    der.fill(0);
  }
}

/** The file's AES-GCM key and nonce prefix. */
async function fileKey(header, privateKey) {
  const eph = fromBase64(header.ephemeral_public_key, 'ephemeral_public_key');
  const recipient = fromBase64(header.recipient_public_key, 'recipient_public_key');
  checkPublic(header.alg, eph, 'ephemeral_public_key');
  checkPublic(header.alg, recipient, 'recipient_public_key');
  const pub = await subtle().importKey('raw', eph, publicKeyParams(header.alg), false, []);
  const shared = new Uint8Array(await subtle().deriveBits({ name: header.alg === 'X25519' ? 'X25519' : 'ECDH', public: pub }, privateKey, 256));
  if (!shared.some((b) => b !== 0)) throw new BackupError('The backup is damaged (its key agreement gave nothing).', 'tampered');
  const token = utf8.encode(header.token);
  const tokenLength = new Uint8Array([(token.length >> 8) & 0xff, token.length & 0xff]);
  const info = concat([utf8.encode(HKDF_INFO), tokenLength, token, eph, recipient]);
  const ikm = await subtle().importKey('raw', shared, 'HKDF', false, ['deriveBits']);
  shared.fill(0);
  const out = new Uint8Array(
    await subtle().deriveBits({ name: 'HKDF', hash: 'SHA-256', salt: fromBase64(header.hkdf_salt, 'hkdf_salt'), info }, ikm, 288),
  );
  try {
    const key = await subtle().importKey('raw', out.slice(0, 32), { name: 'AES-GCM' }, false, ['decrypt']);
    return { key, prefix: out.slice(32, 36) };
  } finally {
    out.fill(0);
  }
}

// --- The tar inside -------------------------------------------------------------

function cString(bytes, start, length) {
  let end = start;
  while (end < start + length && bytes[end] !== 0) end += 1;
  return decodeText(bytes.subarray(start, end));
}

function tarNumber(bytes, start, length) {
  if (bytes[start] & 0x80) {
    // Base-256, for a size past 8 GB.
    let n = 0;
    for (let i = start + 1; i < start + length; i += 1) n = n * 256 + bytes[i];
    return n;
  }
  const text = cString(bytes, start, length).trim();
  if (!text) return 0;
  if (!/^[0-7]+$/.test(text)) throw new BackupError('The backup is damaged (its contents are not readable).');
  return parseInt(text, 8);
}

function safePath(path) {
  if (!path || path.startsWith('/') || path.includes('\\') || path.split('/').some((p) => p === '' || p === '.' || p === '..')) {
    throw new BackupError(`The backup holds an unsafe path (${path}).`, 'tampered');
  }
  return path;
}

/** A tar read as it arrives, into Blobs. Only files and folders, never links. */
class TarReader {
  constructor() {
    this.queue = [];
    this.queued = 0;
    this.state = 'header';
    this.files = new Map();
    this.entry = null;
    this.remaining = 0;
    this.pad = 0;
    this.pax = null;
  }

  push(bytes) {
    if (bytes.length) {
      this.queue.push(bytes);
      this.queued += bytes.length;
    }
    this.drain();
  }

  take(n) {
    const parts = [];
    let need = n;
    while (need > 0) {
      const head = this.queue[0];
      if (head.length <= need) {
        parts.push(head);
        this.queue.shift();
        need -= head.length;
      } else {
        parts.push(head.slice(0, need));
        this.queue[0] = head.subarray(need);
        need = 0;
      }
    }
    this.queued -= n;
    return parts.length === 1 ? parts[0] : concat(parts);
  }

  drain() {
    for (;;) {
      if (this.state === 'header') {
        if (this.queued < 512) return;
        this.header(this.take(512));
      } else if (this.state === 'body') {
        if (this.remaining === 0) {
          this.finish();
          this.state = 'pad';
          continue;
        }
        const n = Math.min(this.remaining, this.queued);
        if (n === 0) return;
        this.entry.parts.push(this.take(n));
        this.remaining -= n;
      } else if (this.state === 'pad') {
        if (this.queued < this.pad) return;
        if (this.pad) this.take(this.pad);
        this.state = 'header';
      } else {
        // After the end marker only zero padding may follow.
        if (!this.queued) return;
        const rest = this.take(this.queued);
        if (rest.some((b) => b !== 0)) throw new BackupError('The backup is damaged (there is data after its end).', 'tampered');
      }
    }
  }

  header(block) {
    if (block.every((b) => b === 0)) {
      this.state = 'end';
      return;
    }
    let sum = 0;
    for (let i = 0; i < 512; i += 1) sum += i >= 148 && i < 156 ? 32 : block[i];
    if (sum !== tarNumber(block, 148, 8)) throw new BackupError('The backup is damaged (its contents are not readable).', 'tampered');
    const type = String.fromCharCode(block[156] || 48);
    const size = tarNumber(block, 124, 12);
    const ustar = cString(block, 257, 5) === 'ustar';
    const name = cString(block, 0, 100);
    const prefix = ustar ? cString(block, 345, 155) : '';
    const pax = this.pax || {};
    if (type === 'x' || type === 'g') {
      this.entry = { kind: type === 'x' ? 'pax' : 'global', parts: [] };
    } else if (type === '0' || type === '7') {
      const path = safePath(pax.path || (prefix ? `${prefix}/${name}` : name));
      this.entry = { kind: 'file', path, parts: [] };
      this.pax = null;
    } else if (type === '5') {
      safePath((pax.path || (prefix ? `${prefix}/${name}` : name)).replace(/\/$/, ''));
      this.entry = { kind: 'dir', parts: [] };
      this.pax = null;
    } else {
      throw new BackupError('The backup holds something that is not a file (a link or a device).', 'tampered');
    }
    this.remaining = type === '0' || type === '7' ? (pax.size !== undefined ? pax.size : size) : size;
    this.pad = (512 - (this.remaining % 512)) % 512;
    this.state = 'body';
  }

  finish() {
    const entry = this.entry;
    this.entry = null;
    if (!entry) return;
    if (entry.kind === 'file') {
      if (this.files.has(entry.path)) throw new BackupError(`The backup holds ${entry.path} twice.`, 'tampered');
      this.files.set(entry.path, new Blob(entry.parts));
    } else if (entry.kind === 'pax') {
      this.pax = parsePax(concat(entry.parts));
    }
  }

  end() {
    if (this.state !== 'end' || this.queued) {
      throw new BackupError('The backup is cut short (its contents end too soon).', 'tampered');
    }
    return this.files;
  }
}

function parsePax(bytes) {
  const out = {};
  let at = 0;
  while (at < bytes.length) {
    let space = at;
    while (space < bytes.length && bytes[space] !== 32) space += 1;
    const length = parseInt(decodeText(bytes.subarray(at, space)), 10);
    if (!Number.isInteger(length) || length <= 0 || at + length > bytes.length) {
      throw new BackupError('The backup is damaged (its contents are not readable).', 'tampered');
    }
    const record = decodeText(bytes.subarray(space + 1, at + length - 1));
    const eq = record.indexOf('=');
    const key = record.slice(0, eq);
    const value = record.slice(eq + 1);
    if (key === 'path') out.path = value;
    if (key === 'size') out.size = Number(value);
    at += length;
  }
  return out;
}

// --- Opening the file -----------------------------------------------------------

/** Whether a path in the backup belongs to its chat: the registry record or a `<kind>/<token>/` folder. */
function belongsTo(path, token) {
  if (path === `chats/${token}.json`) return true;
  const parts = path.split('/');
  return parts.length >= 3 && parts[0] !== 'chats' && parts[1] === token;
}

/**
 * Open a backup with the chat password: every chunk is decrypted and checked
 * before anything is returned. `moreRecords` are key records to try first
 * (the live chat's, for a password set after the backup was made).
 */
async function openBackup(source, password, options) {
  const opts = options || {};
  const { header, digest, offset } = await readBackupHeader(source);
  const dk = await unlockBackup(header, password, opts.moreRecords);
  const privateKey = await backupPrivateKey(header, dk);
  const { key, prefix } = await fileKey(header, privateKey);
  const tar = new TarReader();
  const full = CHUNK_BYTES + TAG_BYTES;
  let at = offset;
  for (let index = 0; ; index += 1) {
    const block = await bytesOf(source, at, Math.min(at + full, source.size));
    if (block.length < TAG_BYTES) throw new BackupError('The backup is cut short (its last part is missing). Nothing was opened.', 'tampered');
    const final = block.length < full;
    let plain;
    try {
      plain = new Uint8Array(await subtle().decrypt(
        { name: 'AES-GCM', iv: concat([prefix, u64(index)]), additionalData: concat([digest, u64(index), new Uint8Array([final ? 1 : 0])]) },
        key,
        block,
      ));
    } catch {
      throw new BackupError(`Part ${index + 1} of the backup was changed, moved or cut short. Nothing was opened.`, 'tampered');
    }
    tar.push(plain);
    at += block.length;
    if (opts.onProgress) opts.onProgress(at, source.size);
    if (final) break;
  }
  if (at !== source.size) throw new BackupError('The backup has extra bytes after its end. Nothing was opened.', 'tampered');
  const files = tar.end();
  for (const path of files.keys()) {
    if (!belongsTo(path, header.token)) throw new BackupError(`The backup holds a file of another chat (${path}).`, 'tampered');
  }
  if (!files.has(`chats/${header.token}.json`)) throw new BackupError("The backup has no record of its chat.", 'format');
  return { header, token: header.token, key: dk, files };
}

// --- What is in it --------------------------------------------------------------

function readSealed(raw) {
  const text = String(raw == null ? '' : raw);
  const start = text.indexOf(FENCE);
  if (start === -1) return null;
  const end = text.indexOf(FENCE, start + FENCE.length);
  if (end === -1) return null;
  try {
    const parsed = JSON.parse(text.slice(start + FENCE.length, end).trim());
    if (!parsed || parsed.kind !== 'sealed' || typeof parsed.iv !== 'string' || typeof parsed.ct !== 'string') return null;
    return parsed;
  } catch {
    return null;
  }
}

/** A sealed record's JSON, or null when it is not one or was not made with this chat's key. */
async function openSealed(key, raw) {
  const sealed = readSealed(raw);
  if (!sealed) return null;
  try {
    const plain = await subtle().decrypt({ name: 'AES-GCM', iv: fromBase64(sealed.iv, 'iv') }, key, fromBase64(sealed.ct, 'ct'));
    return JSON.parse(decodeText(new Uint8Array(plain)));
  } catch {
    return null;
  }
}

async function readJson(files, path) {
  const blob = files.get(path);
  if (!blob) return null;
  try {
    return JSON.parse(await blob.text());
  } catch {
    return null;
  }
}

function whole(value) {
  return typeof value === 'number' && Number.isFinite(value) ? Math.floor(value) : 0;
}

/**
 * The chat as a keepsake: every message opened (with its edit and reactions),
 * the gallery, Beloved and the encrypted pictures (opened one at a time with
 * `decryptBlob`). Everything is decrypted here, with the key `openBackup` returned.
 */
async function readKeepsake(opened) {
  const { files, key, token, header } = opened;
  const registry = (await readJson(files, `chats/${token}.json`)) || {};
  const members = Array.isArray(registry.members) ? registry.members.map(String) : [];
  const blobs = new Map();
  const messageRecords = [];
  const markRecords = new Map();
  const gallery = [];
  const beloved = [];
  let read = {};
  const kinds = BELOVED_SECTIONS.map((s) => s[0]);
  for (const [path, blob] of files) {
    const parts = path.split('/');
    if (parts[0] === 'blobs' && parts.length === 3 && BLOB_PATTERN.test(parts[2])) blobs.set(parts[2], blob);
    else if (parts[0] === 'messages' && parts[2] === 'm' && /^[1-9][0-9]{0,15}\.json$/.test(parts[3] || '')) messageRecords.push(path);
    else if (parts[0] === 'messages' && parts[2] === 'read.json') read = (await readJson(files, path)) || {};
    else if (parts[0] === 'marks' && /^[1-9][0-9]{0,15}\.json$/.test(parts[2] || '')) markRecords.set(Number(parts[2].slice(0, -5)), path);
    else if (parts[0] === 'gallery' && /^[0-9a-f]{32}\.json$/.test(parts[2] || '')) gallery.push(path);
    else if (parts[0] === 'beloved' && kinds.includes(parts[2]) && /^[0-9a-f]{32}\.json$/.test(parts[3] || '')) beloved.push(path);
  }

  const messages = [];
  for (const path of messageRecords) {
    const record = await readJson(files, path);
    if (!record || typeof record.id !== 'number') continue;
    const item = {
      id: record.id,
      by: String(record.by || ''),
      at: whole(record.at),
      replyTo: typeof record.reply_to === 'number' ? record.reply_to : null,
      deleted: record.deleted === true,
      payload: null,
      unreadable: false,
      edit: null,
      reactions: [],
    };
    if (!item.deleted) {
      item.payload = await openSealed(key, record.sealed);
      item.unreadable = !item.payload || typeof item.payload.t !== 'string';
    }
    const markPath = markRecords.get(item.id);
    const mark = markPath ? await readJson(files, markPath) : null;
    if (mark && !item.deleted) {
      if (mark.edit) {
        const edit = await openSealed(key, mark.edit.sealed);
        if (edit && edit.t === 'edit' && edit.m === item.id && typeof edit.text === 'string') item.edit = { text: edit.text, at: whole(mark.edit.at) };
      }
      for (const r of Array.isArray(mark.reactions) ? mark.reactions : []) {
        const reaction = await openSealed(key, r.sealed);
        if (reaction && reaction.t === 'reaction' && reaction.m === item.id && typeof reaction.e === 'string') {
          item.reactions.push({ by: String(r.by || ''), emoji: reaction.e, at: whole(r.at) });
        }
      }
    }
    messages.push(item);
  }
  messages.sort((a, b) => a.id - b.id);

  const pictures = [];
  for (const path of gallery) {
    const entry = await readJson(files, path);
    if (!entry) continue;
    const payload = await openSealed(key, entry.sealed);
    if (payload && typeof payload.blob === 'string') pictures.push({ id: String(entry.id), by: String(entry.by || ''), at: whole(entry.at), payload });
  }
  pictures.sort((a, b) => a.at - b.at);

  const love = [];
  for (const path of beloved) {
    const entry = await readJson(files, path);
    if (!entry) continue;
    const kind = path.split('/')[2];
    const record = await openSealed(key, entry.sealed);
    if (record && record.b === kind) {
      love.push({ kind, id: String(entry.id), by: String(entry.by || ''), at: whole(entry.at), updatedAt: whole(entry.updated_at), record });
    }
  }
  love.sort((a, b) => a.at - b.at);

  return {
    token,
    madeAt: whole(header.created_at),
    createdAt: whole(registry.created_at),
    members,
    messages,
    gallery: pictures,
    beloved: love,
    blobs,
    read,
    key,
  };
}

/** A picture (or a poster frame) decrypted: IV first, then the ciphertext. */
async function decryptBlob(key, blob) {
  const data = await bytesOf(blob, 0, blob.size);
  if (data.length <= 12 + TAG_BYTES) throw new BackupError('This picture is damaged.', 'tampered');
  try {
    return new Uint8Array(await subtle().decrypt({ name: 'AES-GCM', iv: data.slice(0, 12) }, key, data.slice(12)));
  } catch {
    throw new BackupError('This picture is damaged.', 'tampered');
  }
}

function pictureType(mime) {
  return IMAGE_MIMES.includes(mime) ? mime : 'application/octet-stream';
}

/** A video clip from its pieces, each checked for its place in the clip (privateChatCrypto.ts decryptVideo). */
async function decryptClip(keepsake, video) {
  const count = Array.isArray(video.chunks) ? video.chunks.length : 0;
  const size = whole(video.size);
  const chunkBytes = whole(video.chunk_bytes);
  if (!count || !size || !chunkBytes || Math.ceil(size / chunkBytes) !== count) throw new BackupError('This clip is damaged.', 'tampered');
  const clip = fromBase64(video.clip, 'clip');
  if (clip.length !== 16) throw new BackupError('This clip is damaged.', 'tampered');
  const label = utf8.encode(VIDEO_AAD_LABEL);
  const parts = [];
  for (let index = 0; index < count; index += 1) {
    const blob = keepsake.blobs.get(video.chunks[index]);
    if (!blob) throw new BackupError('A piece of this clip is not in the backup.', 'tampered');
    const data = await bytesOf(blob, 0, blob.size);
    const aad = new Uint8Array(label.length + 32);
    aad.set(label, 0);
    aad.set(clip, label.length);
    const view = new DataView(aad.buffer, label.length + 16);
    view.setUint32(0, index);
    view.setUint32(4, count);
    view.setBigUint64(8, BigInt(size));
    let plain;
    try {
      plain = new Uint8Array(await subtle().decrypt({ name: 'AES-GCM', iv: data.slice(0, 12), additionalData: aad }, keepsake.key, data.slice(12)));
    } catch {
      throw new BackupError('This clip is damaged.', 'tampered');
    }
    const expected = index < count - 1 ? chunkBytes : size - chunkBytes * (count - 1);
    if (plain.length !== expected) throw new BackupError('This clip is damaged.', 'tampered');
    parts.push(plain);
  }
  return new Blob(parts, { type: VIDEO_MIMES.includes(video.mime) ? video.mime : 'application/octet-stream' });
}

function words(value) {
  return typeof value === 'string' && value.trim() ? value.trim() : '';
}

/** A Beloved record as a heading and lines of text, for a keepsake. */
function describeBeloved(kind, record) {
  const lines = [];
  let title = '';
  if (kind === 'profile') {
    title = words(record.title) || 'Our page';
    if (words(record.wedding)) lines.push(`Married ${record.wedding}`);
    if (words(record.firstDate)) lines.push(`First date ${record.firstDate}`);
    for (const [who, day] of Object.entries(record.birthdays || {})) if (words(day)) lines.push(`${who}: birthday ${day}`);
    for (const child of Array.isArray(record.children) ? record.children : []) {
      if (child && words(child.name)) lines.push(`${child.name}: birthday ${child.birthday || ''}`.trim());
    }
  } else if (kind === 'day') {
    title = words(record.name) || 'A special day';
    lines.push(`${record.date || ''}${record.yearly ? ', every year' : ''}`);
  } else if (kind === 'note') {
    title = 'Love note';
    lines.push(words(record.text));
  } else if (kind === 'heart') {
    title = 'Thinking of you';
  } else if (kind === 'prayer') {
    title = record.answeredAt ? 'Prayer, answered' : 'Prayer';
    lines.push(words(record.text));
    if (words(record.answer)) lines.push(`Answer: ${record.answer}`);
  } else if (kind === 'devotion') {
    title = `Devotion for ${record.day || ''}`.trim();
    lines.push(record.done ? 'Done together' : 'Not done');
    if (words(record.answer)) lines.push(record.answer);
  } else if (kind === 'idea') {
    title = words(record.custom) || words(record.idea) || 'Date night idea';
    if (words(record.on)) lines.push(`For ${record.on}`);
    if (record.doneAt) lines.push('Done');
  } else if (kind === 'together') {
    title = `A day together: ${record.day || ''}`;
    if (words(record.note)) lines.push(record.note);
  } else if (kind === 'plan') {
    title = `Plan for ${record.day || ''}${record.time ? ` at ${record.time}` : ''}`;
    if (words(record.note)) lines.push(record.note);
    if (record.doneAt) lines.push('Done');
  }
  return { title, lines: lines.filter(Boolean) };
}

export {
  BACKUP_SUFFIX,
  BELOVED_SECTIONS,
  BackupError,
  CHUNK_BYTES,
  IMAGE_MIMES,
  VIDEO_MIMES,
  belongsTo,
  decryptBlob,
  decryptClip,
  describeBeloved,
  openBackup,
  openSealed,
  pictureType,
  readBackupHeader,
  readKeepsake,
};
