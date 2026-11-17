import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  BackupError,
  CHUNK_BYTES,
  decryptBlob,
  decryptClip,
  describeBeloved,
  openBackup,
  readBackupHeader,
  readKeepsake,
  type BackupHeader,
  type OpenedBackup,
} from './privateChatBackupCore.js';

/**
 * The app's decryptor against backups the Python encrypter made
 * (tests/unit/private_chat_backup_fixture.py, regenerated with
 * `python tests/unit/private_chat_backup_fixture.py`): the same fake chats as
 * the key vectors, filled with sealed messages, a picture, a clip, an edit, a
 * reaction, a deleted message, a gallery picture and Beloved records.
 */
interface FixtureEntry {
  password: string;
  other_passwords: string[];
  alg: string;
  file: string;
  expect: {
    token: string;
    members: string[];
    messages: number;
    texts: string[];
    edited: { id: number; original: string; text: string };
    reaction: { id: number; by: string; emoji: string };
    deleted: number[];
    picture: { id: number; blob: string; caption: string };
    video: { id: number; bytes: string; pieces: number };
    gallery: { blob: string; name: string };
    beloved: Record<string, string>;
    blobs: string[];
    files: string[];
  };
}

const FIXTURE = JSON.parse(
  readFileSync(resolve(__dirname, '../../../../tests/unit/fixtures/private_chat_backup_files.json'), 'utf8'),
) as Record<'v1' | 'v2', FixtureEntry>;
const PNG_HEAD = [0x89, 0x50, 0x4e, 0x47];

function bytes(name: 'v1' | 'v2'): Uint8Array<ArrayBuffer> {
  return new Uint8Array(Buffer.from(FIXTURE[name].file, 'base64'));
}

function file(data: Uint8Array<ArrayBuffer>): Blob {
  return new Blob([data]);
}

async function refused(data: Uint8Array<ArrayBuffer>, password: string): Promise<BackupError> {
  try {
    await openBackup(file(data), password);
  } catch (error) {
    expect(error).toBeInstanceOf(BackupError);
    return error as BackupError;
  }
  throw new Error('a damaged backup was opened');
}

describe('opening a backup the server made', () => {
  for (const name of ['v2', 'v1'] as const) {
    it(`opens the ${name} chat (${FIXTURE[name].alg}) with its password and shows everything in it`, async () => {
      const entry = FIXTURE[name];
      const opened = await openBackup(file(bytes(name)), entry.password);
      expect(opened.header.alg).toBe(entry.alg);
      expect([...opened.files.keys()].sort()).toEqual(entry.expect.files);
      const keepsake = await readKeepsake(opened);
      const want = entry.expect;
      expect(keepsake.members).toEqual(want.members);
      expect(keepsake.messages.map((m) => m.id)).toEqual([1, 2, 3, 4, 5, 6]);
      expect(keepsake.messages.every((m) => !m.unreadable)).toBe(true);

      const texts = keepsake.messages.map((m) => m.edit?.text ?? (m.payload?.t === 'text' || m.payload?.t === 'comment' ? m.payload.text : null));
      for (const text of want.texts) expect(texts).toContain(text);
      const edited = keepsake.messages.find((m) => m.id === want.edited.id)!;
      expect(edited.payload).toMatchObject({ t: 'text', text: want.edited.original });
      expect(edited.edit?.text).toBe(want.edited.text);
      expect(edited.reactions).toEqual([expect.objectContaining({ by: want.reaction.by, emoji: want.reaction.emoji })]);
      expect(keepsake.messages.filter((m) => m.deleted).map((m) => m.id)).toEqual(want.deleted);
      expect(edited.replyTo).toBe(1);

      const picture = keepsake.messages.find((m) => m.id === want.picture.id)!;
      expect(picture.payload).toMatchObject({ t: 'image', blob: want.picture.blob, caption: want.picture.caption });
      const png = await decryptBlob(keepsake.key, keepsake.blobs.get(want.picture.blob)!);
      expect([...png.slice(0, 4)]).toEqual(PNG_HEAD);

      const clipMessage = keepsake.messages.find((m) => m.id === want.video.id)!;
      expect(clipMessage.payload?.t).toBe('video');
      const clip = await decryptClip(keepsake, clipMessage.payload!);
      expect(Buffer.from(await clip.arrayBuffer()).toString('base64')).toBe(want.video.bytes);
      expect(clip.type).toBe('video/mp4');

      expect(keepsake.gallery).toHaveLength(1);
      expect(keepsake.gallery[0].payload).toMatchObject({ t: 'added', blob: want.gallery.blob, name: want.gallery.name });
      const kinds = Object.fromEntries(keepsake.beloved.map((b) => [b.kind, describeBeloved(b.kind, b.record)]));
      expect(kinds.profile.title).toBe(want.beloved.profile);
      expect(kinds.profile.lines).toContain('Married 2015-06-20');
      expect(kinds.note.lines).toEqual([want.beloved.note]);
      expect(kinds.prayer.lines).toEqual([want.beloved.prayer]);
      expect(kinds.day.title).toBe(want.beloved.day);
      expect([...keepsake.blobs.keys()].sort()).toEqual(want.blobs);
    });
  }

  it('opens with any password the chat had when the backup was made', async () => {
    const entry = FIXTURE.v2;
    for (const password of entry.other_passwords) {
      const opened = await openBackup(file(bytes('v2')), password);
      expect(opened.token).toBe(entry.expect.token);
    }
  });

  it('a wrong password or none is refused as a password, with nothing opened', async () => {
    expect((await refused(bytes('v2'), 'not the password')).code).toBe('password');
    expect((await refused(bytes('v1'), FIXTURE.v2.password)).code).toBe('password');
    expect((await refused(bytes('v2'), '')).code).toBe('password');
  });

  it('reads the header without the password, and it names no one', async () => {
    const { header } = await readBackupHeader(file(bytes('v2')));
    expect(header.token).toBe(FIXTURE.v2.expect.token);
    const text = JSON.stringify(header);
    for (const member of FIXTURE.v2.expect.members) expect(text).not.toContain(member);
  });
});

describe('a changed backup is refused', () => {
  const data = bytes('v2');
  const headerEnd = (d: Uint8Array) => 16 + new DataView(d.buffer).getUint32(12);

  it('a flipped byte in a chunk', async () => {
    const copy = data.slice();
    copy[headerEnd(copy) + 40] ^= 1;
    expect((await refused(copy, FIXTURE.v2.password)).code).toBe('tampered');
  });

  it('a changed header', async () => {
    const text = new TextDecoder().decode(data);
    const at = text.indexOf('"payload":"chat"');
    expect(at).toBeGreaterThan(0);
    const copy = data.slice();
    copy[at + '"payload":"cha'.length] = 'T'.charCodeAt(0);
    expect((await refused(copy, FIXTURE.v2.password)).code).toBe('tampered');
  });

  it('a cut at the end, a cut to the header, and bytes added after the end', async () => {
    expect((await refused(data.slice(0, data.length - 1), FIXTURE.v2.password)).code).toBe('tampered');
    expect((await refused(data.slice(0, headerEnd(data) + 5), FIXTURE.v2.password)).code).toBe('tampered');
    const longer = new Uint8Array(data.length + 3);
    longer.set(data);
    expect((await refused(longer, FIXTURE.v2.password)).code).toBe('tampered');
  });

  it('not a backup at all', async () => {
    expect((await refused(new Uint8Array(Buffer.from('<html>hello</html>')), 'x')).code).toBe('format');
  });
});

// --- A multi-chunk backup, made here the way scripts/private_chat_backup.py makes one ---------------

function tarFile(path: string, body: Uint8Array): Uint8Array[] {
  const header = new Uint8Array(512);
  const put = (text: string, at: number) => header.set(new TextEncoder().encode(text), at);
  put(path, 0);
  put('0000644\0', 100);
  put('0000000\0', 108);
  put('0000000\0', 116);
  put(`${body.length.toString(8).padStart(11, '0')}\0`, 124);
  put('00000000000\0', 136);
  put('        ', 148);
  header[156] = '0'.charCodeAt(0);
  put('ustar\x0000', 257);
  const sum = header.reduce((a, b) => a + b, 0);
  put(`${sum.toString(8).padStart(6, '0')}\0 `, 148);
  const pad = new Uint8Array((512 - (body.length % 512)) % 512);
  return [header, body, pad];
}

async function reencrypt(opened: OpenedBackup, extra: Uint8Array): Promise<{ data: Uint8Array<ArrayBuffer>; chunks: number; start: number }> {
  const s = crypto.subtle;
  const parts: Uint8Array[] = [];
  for (const [path, blob] of opened.files) parts.push(...tarFile(path, new Uint8Array(await blob.arrayBuffer())));
  parts.push(...tarFile(`blobs/${opened.token}/${'e'.repeat(32)}`, extra), new Uint8Array(1024));
  const plain = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let at = 0;
  for (const p of parts) {
    plain.set(p, at);
    at += p.length;
  }
  const old = opened.header;
  const recipient = new Uint8Array(Buffer.from(old.recipient_public_key, 'base64'));
  const eph = (await s.generateKey({ name: 'X25519' }, true, ['deriveBits'])) as CryptoKeyPair;
  const ephRaw = new Uint8Array(await s.exportKey('raw', eph.publicKey));
  const salt = crypto.getRandomValues(new Uint8Array(32));
  const header: BackupHeader = { ...old, ephemeral_public_key: Buffer.from(ephRaw).toString('base64'), hkdf_salt: Buffer.from(salt).toString('base64') };
  const head = new TextEncoder().encode(JSON.stringify(header));
  const lead = new Uint8Array(16 + head.length);
  lead.set(new TextEncoder().encode('JARVISPCBK'), 0);
  lead.set([0, 1], 10);
  new DataView(lead.buffer).setUint32(12, head.length);
  lead.set(head, 16);
  const digest = new Uint8Array(await s.digest('SHA-256', lead));
  const pub = await s.importKey('raw', recipient, { name: 'X25519' }, false, []);
  const shared = await s.deriveBits({ name: 'X25519', public: pub }, eph.privateKey, 256);
  const token = new TextEncoder().encode(old.token);
  const info = new Uint8Array([...new TextEncoder().encode('jarvis-private-chat-backup/v1'), 0, token.length, ...token, ...ephRaw, ...recipient]);
  const okm = new Uint8Array(await s.deriveBits({ name: 'HKDF', hash: 'SHA-256', salt, info }, await s.importKey('raw', shared, 'HKDF', false, ['deriveBits']), 288));
  const key = await s.importKey('raw', okm.slice(0, 32), 'AES-GCM', false, ['encrypt']);
  const out: Uint8Array[] = [lead];
  const count = Math.floor(plain.length / CHUNK_BYTES) + 1;
  for (let i = 0; i < count; i += 1) {
    const final = i === count - 1;
    const be = new Uint8Array(8);
    new DataView(be.buffer).setBigUint64(0, BigInt(i));
    const iv = new Uint8Array([...okm.slice(32, 36), ...be]);
    const aad = new Uint8Array([...digest, ...be, final ? 1 : 0]);
    out.push(new Uint8Array(await s.encrypt({ name: 'AES-GCM', iv, additionalData: aad }, key, plain.slice(i * CHUNK_BYTES, (i + 1) * CHUNK_BYTES))));
  }
  const data = new Uint8Array(out.reduce((n, p) => n + p.length, 0));
  at = 0;
  for (const p of out) {
    data.set(p, at);
    at += p.length;
  }
  return { data, chunks: count, start: lead.length };
}

describe('a backup of several chunks', () => {
  it('opens whole, and refuses a reordered, dropped or duplicated chunk', async () => {
    const opened = await openBackup(file(bytes('v2')), FIXTURE.v2.password);
    const extra = crypto.getRandomValues(new Uint8Array(60_000));
    const big = new Uint8Array(2 * CHUNK_BYTES + 5000);
    for (let i = 0; i < big.length; i += extra.length) big.set(extra.subarray(0, Math.min(extra.length, big.length - i)), i);
    const { data, chunks, start } = await reencrypt(opened, big);
    expect(chunks).toBe(3);
    const whole = await openBackup(file(data), FIXTURE.v2.password);
    expect(whole.files.get(`blobs/${opened.token}/${'e'.repeat(32)}`)?.size).toBe(big.length);

    const full = CHUNK_BYTES + 16;
    const body = data.slice(start);
    const join = (...parts: Uint8Array[]) => {
      const out = new Uint8Array(start + parts.reduce((n, p) => n + p.length, 0));
      out.set(data.slice(0, start));
      let at = start;
      for (const p of parts) {
        out.set(p, at);
        at += p.length;
      }
      return out;
    };
    const c0 = body.slice(0, full);
    const c1 = body.slice(full, 2 * full);
    const last = body.slice(2 * full);
    for (const changed of [join(c1, c0, last), join(c0, last), join(c0, c0, c1, last), join(c0, c1), join(c0, c1, last.slice(0, 10))]) {
      expect((await refused(changed, FIXTURE.v2.password)).code).toBe('tampered');
    }
  });
});
