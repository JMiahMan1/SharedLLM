import { describe, expect, it, vi } from 'vitest';
import {
  changePasswordRequest,
  checkVerifier,
  decryptBytes,
  deriveKey,
  encryptBytes,
  fromBase64,
  importDataKey,
  isSealedMessage,
  makeVerifier,
  newChatSecrets,
  openMessage,
  PBKDF2_ITERATIONS,
  PrivateVault,
  randomBytes,
  rawDataKey,
  readSealed,
  sealMessage,
  SEALED_LINE,
  toBase64,
  unlockKey,
  WrongPasswordError,
  type ChatKeyInfo,
  type PasswordChangeBody,
  type PrivatePayload,
} from './privateChatCrypto';

/** A chat as the first version made it (the one already in use): its key IS the password's. */
async function v1Chat(password: string): Promise<{ info: ChatKeyInfo; key: CryptoKey }> {
  const kdf = { salt: toBase64(randomBytes(16)), iterations: PBKDF2_ITERATIONS };
  const key = await deriveKey(password, kdf);
  return { info: { version: 1, kdf, verifier: await makeVerifier(key) }, key };
}

/** The record the server keeps after a change. */
function afterChange(body: PasswordChangeBody): ChatKeyInfo {
  return { version: 2, kdf: body.kdf, wrapped_key: body.wrapped_key, verifier: body.verifier };
}

const v2Info = (made: Awaited<ReturnType<typeof newChatSecrets>>): ChatKeyInfo => ({
  version: 2,
  kdf: made.kdf,
  wrapped_key: made.wrapped_key,
  verifier: made.verifier,
});

// The real stretch (600,000 rounds) runs once per describe block; it is the
// point of the design, so the tests use it rather than a weaker count.
describe('private chat crypto', () => {
  it('opens a new (v2) chat with the same password on the other parent\'s device', async () => {
    const created = await newChatSecrets('correct horse battery');
    expect(created.version).toBe(2);
    expect(created.kdf.iterations).toBe(PBKDF2_ITERATIONS);
    expect(fromBase64(created.kdf.salt)).toHaveLength(16);
    expect(fromBase64(created.verifier.iv)).toHaveLength(12);
    expect(fromBase64(created.wrapped_key.ct)).toHaveLength(48);
    expect(fromBase64(created.auth)).toHaveLength(32);
    expect(created.key.extractable).toBe(false);

    const other = (await unlockKey('correct horse battery', v2Info(created)))!;
    expect(other).not.toBeNull();
    expect(other.extractable).toBe(false);
    expect(await checkVerifier(other, created.verifier)).toBe(true);

    const wire = await sealMessage(created.key, { t: 'text', text: 'Date night Friday?' });
    expect(wire.startsWith(SEALED_LINE)).toBe(true);
    expect(wire).not.toContain('Date night');
    expect(await openMessage(other, wire)).toEqual({ t: 'text', text: 'Date night Friday?' });
  }, 30_000);

  it('rejects a wrong password through the verifier and cannot open messages with it', async () => {
    const created = await newChatSecrets('the real one');
    expect(await unlockKey('not the real one', v2Info(created))).toBeNull();
    const { info, key } = await v1Chat('the real one');
    const wrong = await deriveKey('not the real one', info.kdf);
    expect(await checkVerifier(wrong, info.verifier)).toBe(false);
    expect(await unlockKey('not the real one', info)).toBeNull();
    const wire = await sealMessage(key, { t: 'text', text: 'secret' });
    expect(await openMessage(wrong, wire)).toBeNull();
  }, 30_000);

  it('uses a fresh IV for every message and refuses a tampered one', async () => {
    const { key } = await newChatSecrets('pw');
    const a = readSealed(await sealMessage(key, { t: 'text', text: 'same' }))!;
    const b = readSealed(await sealMessage(key, { t: 'text', text: 'same' }))!;
    expect(a.iv).not.toBe(b.iv);
    expect(a.ct).not.toBe(b.ct);
    const bytes = fromBase64(a.ct);
    bytes[0] ^= 1;
    const tampered = `x\n\n\`\`\`jarvis-envelope\n${JSON.stringify({ kind: 'sealed', iv: a.iv, ct: btoa(String.fromCharCode(...bytes)) })}\n\`\`\``;
    expect(await openMessage(key, tampered)).toBeNull();
    expect(isSealedMessage('hello')).toBe(false);
    expect(await openMessage(key, 'hello')).toBeNull();
  }, 30_000);

  it('round-trips a picture and refuses one too short to be ciphertext', async () => {
    const { key } = await newChatSecrets('pw');
    const picture = new Uint8Array(4096).map((_, i) => i % 251);
    const sealed = await encryptBytes(key, picture);
    expect(sealed.length).toBe(picture.length + 12 + 16);
    expect(await decryptBytes(key, sealed)).toEqual(picture);
    await expect(decryptBytes(key, sealed.slice(0, 8))).rejects.toThrow();
  }, 30_000);

  it('never accepts a weaker stretch than the minimum', async () => {
    await expect(deriveKey('pw', { salt: 'AAAAAAAAAAAAAAAAAAAAAA==', iterations: 1000 })).rejects.toThrow(/too weak/);
  });
});

describe('PrivateVault', () => {
  it('unlocks only with the right password and forgets everything on lock', async () => {
    const created = await newChatSecrets('pw-1');
    const params = v2Info(created);
    const vault = new PrivateVault();
    const changes = vi.fn();
    vault.subscribe(changes);

    expect(await vault.unlock('nope', params)).toBe(false);
    expect(vault.unlocked).toBe(false);
    expect(await vault.unlock('pw-1', params)).toBe(true);
    expect(vault.unlocked).toBe(true);

    const wire = await vault.seal({ t: 'text', text: 'hi' });
    expect(await vault.open(wire)).toEqual({ t: 'text', text: 'hi' });

    const revoke = vi.spyOn(URL, 'revokeObjectURL');
    const picture = await vault.encrypt(new Uint8Array([1, 2, 3, 4]));
    const url = await vault.pictureUrl(picture, 'image/png');
    expect(vault.liveUrls).toBe(1);

    vault.lock();
    expect(vault.unlocked).toBe(false);
    expect(vault.liveUrls).toBe(0);
    expect(revoke).toHaveBeenCalledWith(url);
    await expect(vault.open(wire)).rejects.toThrow(/locked/);
    await expect(vault.seal({ t: 'text', text: 'x' })).rejects.toThrow(/locked/);
    expect(changes).toHaveBeenCalledTimes(2);
  }, 60_000);
});

/** Every buffer a WebCrypto call hands back, so a test can see what was left in memory. */
function capture(method: 'deriveBits' | 'decrypt'): ArrayBuffer[] {
  const subtle = globalThis.crypto.subtle;
  const original = subtle[method].bind(subtle) as (...args: unknown[]) => Promise<ArrayBuffer>;
  const seen: ArrayBuffer[] = [];
  vi.spyOn(subtle, method).mockImplementation((async (...args: unknown[]) => {
    const out = await original(...args);
    seen.push(out);
    return out;
  }) as never);
  return seen;
}

const allZero = (buffer: ArrayBuffer) => new Uint8Array(buffer).every((b) => b === 0);

describe('changing the password', () => {
  it('keeps a v1 chat\'s messages and photos readable with the new password', async () => {
    const { info, key } = await v1Chat('the first password');
    const wire = await sealMessage(key, { t: 'text', text: 'Before the change' });
    const photo = new Uint8Array(2048).map((_, i) => (i * 7) % 256);
    const sealedPhoto = await encryptBytes(key, photo);

    const body = await changePasswordRequest(info, 'the first password', 'the second password');
    expect(body.version).toBe(2);
    // The same key: the verifier is the one already stored, and v1 has no proof to send.
    expect(body.verifier).toEqual(info.verifier);
    expect(body.auth).toBeUndefined();
    expect(fromBase64(body.new_auth)).toHaveLength(32);
    expect(body.kdf.salt).not.toBe(info.kdf.salt);
    expect(JSON.stringify(body)).not.toContain('the second password');

    const after = afterChange(body);
    const opened = (await unlockKey('the second password', after))!;
    expect(opened).not.toBeNull();
    expect(opened.extractable).toBe(false);
    expect(await openMessage(opened, wire)).toEqual({ t: 'text', text: 'Before the change' });
    expect(await decryptBytes(opened, sealedPhoto)).toEqual(photo);
    // And what is sent now opens with the old key bytes too: it is one key.
    expect(await openMessage(key, await sealMessage(opened, { t: 'text', text: 'after' }))).toEqual({ t: 'text', text: 'after' });

    const vault = new PrivateVault();
    expect(await vault.unlock('the first password', after)).toBe(false);
    expect(await vault.unlock('the second password', after)).toBe(true);
  }, 60_000);

  it('chains v2 changes: each needs the last proof, and the history still opens', async () => {
    const made = await newChatSecrets('one one one one');
    const wire = await sealMessage(made.key, { t: 'text', text: 'From the start' });

    const first = await changePasswordRequest(v2Info(made), 'one one one one', 'two two two two');
    expect(first.auth).toBe(made.auth);
    const second = await changePasswordRequest(afterChange(first), 'two two two two', 'three three three');
    expect(second.auth).toBe(first.new_auth);
    expect(second.verifier).toEqual(made.verifier);

    const now = afterChange(second);
    const key = (await unlockKey('three three three', now))!;
    expect(await openMessage(key, wire)).toEqual({ t: 'text', text: 'From the start' });
    expect(await unlockKey('one one one one', now)).toBeNull();
    expect(await unlockKey('two two two two', now)).toBeNull();
  }, 90_000);

  it('refuses a wrong current password on the device, before anything is sent', async () => {
    const { info } = await v1Chat('the right one');
    await expect(changePasswordRequest(info, 'the wrong one', 'a new password')).rejects.toBeInstanceOf(WrongPasswordError);
    const made = await newChatSecrets('the right one');
    await expect(changePasswordRequest(v2Info(made), 'the wrong one', 'a new password')).rejects.toThrow(/not the current password/);
  }, 60_000);

  it('overwrites the raw data key (and every stretch) with zeros when done, v1 and v2', async () => {
    const { info } = await v1Chat('pw v1 pw v1');
    const bits = capture('deriveBits');
    const body = await changePasswordRequest(info, 'pw v1 pw v1', 'pw two pw two');
    // The v1 data key itself (256 bits) and the new 512-bit stretch.
    expect(bits.map((b) => b.byteLength).sort()).toEqual([32, 64]);
    expect(bits.every(allZero)).toBe(true);
    vi.restoreAllMocks();

    const decrypted = capture('decrypt');
    const bits2 = capture('deriveBits');
    await changePasswordRequest(afterChange(body), 'pw two pw two', 'pw three pw three');
    const keys = decrypted.filter((b) => b.byteLength === 32);
    expect(keys).toHaveLength(1);
    expect(keys.every(allZero)).toBe(true);
    expect(bits2.length).toBe(2);
    expect(bits2.every(allZero)).toBe(true);
    vi.restoreAllMocks();
  }, 90_000);

  it('hands out raw key bytes only for the right password, and imports them never extractable', async () => {
    const { info, key } = await v1Chat('raw raw raw raw');
    expect(await rawDataKey('nope nope nope', info)).toBeNull();
    const raw = (await rawDataKey('raw raw raw raw', info))!;
    const copy = raw.dk.slice();
    const imported = await importDataKey(raw.dk);
    expect(imported.extractable).toBe(false);
    expect(raw.dk.every((b) => b === 0)).toBe(true);
    const wire = await sealMessage(key, { t: 'text', text: 'same key' });
    expect(await openMessage(imported, wire)).toEqual({ t: 'text', text: 'same key' });
    copy.fill(0);
  }, 60_000);
});

describe('picture and comment payloads', () => {
  it('carry their timestamps inside the encryption, and comments name the picture', async () => {
    const { key } = await v1Chat('payload payload');
    const image = await sealMessage(key, { t: 'image', blob: 'b1', mime: 'image/jpeg', sent_at: 1_800_000_000, taken_at: 1_799_999_000 });
    expect(image).not.toContain('1800000000');
    expect(await openMessage(key, image)).toEqual({ t: 'image', blob: 'b1', mime: 'image/jpeg', sent_at: 1_800_000_000, taken_at: 1_799_999_000 });
    // A distinctive id: a two-letter one like 'b1' turns up in random base64 about one run in twenty.
    const blob = 'blob-ref-7f3a9c';
    const comment = await sealMessage(key, { t: 'comment', blob, text: 'Lovely', sent_at: 1_800_000_100 });
    expect(comment).not.toContain('Lovely');
    expect(comment).not.toContain(blob);
    expect(await openMessage(key, comment)).toEqual({ t: 'comment', blob, text: 'Lovely', sent_at: 1_800_000_100 });
    // Nonsense stamps are dropped; an empty comment is not a comment.
    const odd = await sealMessage(key, { t: 'image', blob: 'b2', mime: 'image/png', sent_at: -5, taken_at: Number.NaN });
    expect(await openMessage(key, odd)).toEqual({ t: 'image', blob: 'b2', mime: 'image/png' });
    expect(await openMessage(key, await sealMessage(key, { t: 'comment', blob: 'b1', text: '  ' }))).toBeNull();
  }, 30_000);

  it('an added picture keeps its metadata inside the encryption, and only fields of the right shape survive', async () => {
    const { key } = await v1Chat('payload payload');
    const sha = 'f'.repeat(64);
    const sealed = await sealMessage(key, {
      t: 'added', blob: 'b9', mime: 'image/jpeg', name: 'IMG_20160512.jpg', taken_at: 1_463_000_000, taken_from: 'exif', added_at: 1_800_000_000,
      sha256: sha, size: 1234, source: 'between', camera: 'Acme', gps: { lat: 12.5, lon: -3.25, alt: 7 }, width: 4000, height: 3000,
    });
    // Nothing of it is readable outside: not the name, the place, the camera or the fingerprint.
    for (const secret of ['IMG_2016', '12.5', 'Acme', sha, 'between']) expect(sealed).not.toContain(secret);
    expect(await openMessage(key, sealed)).toEqual({
      t: 'added', blob: 'b9', mime: 'image/jpeg', name: 'IMG_20160512.jpg', taken_at: 1_463_000_000, taken_from: 'exif', added_at: 1_800_000_000,
      sha256: sha, size: 1234, source: 'between', camera: 'Acme', gps: { lat: 12.5, lon: -3.25, alt: 7 }, width: 4000, height: 3000,
    });
    const odd = await sealMessage(key, { t: 'added', blob: 'b8', mime: 'image/png', sha256: 'nothex', taken_from: 'guess', gps: { lat: 'x' } } as never);
    expect(await openMessage(key, odd)).toEqual({ t: 'added', blob: 'b8', mime: 'image/png' });
    // A sent picture can carry the fingerprint too, so an import skips it.
    expect(await openMessage(key, await sealMessage(key, { t: 'image', blob: 'b1', mime: 'image/jpeg', sha256: sha }))).toEqual({ t: 'image', blob: 'b1', mime: 'image/jpeg', sha256: sha });
  }, 30_000);
});

describe('gallery membership payloads', () => {
  const A = 'a1'.repeat(16);
  const B = 'b2'.repeat(16);

  it('a reply names the picture it answers inside the encryption, and a bad quote is dropped', async () => {
    const { key } = await v1Chat('payload payload');
    const wire = await sealMessage(key, { t: 'text', text: 'save to gallery', quote: { m: 12, blob: A } });
    expect(wire).not.toContain(A);
    expect(await openMessage(key, wire)).toEqual({ t: 'text', text: 'save to gallery', quote: { m: 12, blob: A } });
    for (const quote of [{ m: 0, blob: A }, { m: 3, blob: 'not-a-blob' }, { m: 1.5, blob: A }, 'x']) {
      const odd = await sealMessage(key, { t: 'text', text: 'hi', quote } as unknown as PrivatePayload);
      expect(await openMessage(key, odd)).toEqual({ t: 'text', text: 'hi' });
    }
  }, 30_000);

  it('add, remove and the opt-in start open checked; anything malformed is refused', async () => {
    const { key } = await v1Chat('payload payload');
    const add = await sealMessage(key, { t: 'gallery', op: 'add', blobs: [A, B] });
    expect(add).not.toContain(A);
    expect(await openMessage(key, add)).toEqual({ t: 'gallery', op: 'add', blobs: [A, B] });
    // Padding (and any other extra field) is dropped on the way in.
    const padded = await sealMessage(key, { t: 'gallery', op: 'remove', blobs: [A], p: '    ' } as unknown as PrivatePayload);
    expect(await openMessage(key, padded)).toEqual({ t: 'gallery', op: 'remove', blobs: [A] });
    expect(await openMessage(key, await sealMessage(key, { t: 'gallery_start', kept: [B] }))).toEqual({ t: 'gallery_start', kept: [B] });
    expect(await openMessage(key, await sealMessage(key, { t: 'gallery_start' } as unknown as PrivatePayload))).toEqual({ t: 'gallery_start', kept: [] });
    for (const bad of [
      { t: 'gallery', op: 'move', blobs: [A] },
      { t: 'gallery', op: 'add', blobs: ['nope'] },
      { t: 'gallery', op: 'add', blobs: A },
      { t: 'gallery', op: 'add', blobs: Array.from({ length: 181 }, () => A) },
      { t: 'gallery_start', kept: [1] },
    ]) {
      expect(await openMessage(key, await sealMessage(key, bad as unknown as PrivatePayload))).toBeNull();
    }
  }, 30_000);
});

describe('a text message with a link preview', () => {
  // A real JPEG's first bytes, so the thumbnail check sees a JPEG.
  const JPEG = `data:image/jpeg;base64,${btoa(String.fromCharCode(0xff, 0xd8, 0xff, 0xe0, 0, 16, 74, 70, 73, 70))}`;

  it('carries the preview inside the encryption and gives it back checked', async () => {
    const { key } = await v1Chat('payload payload');
    const preview = { url: 'https://example.com/cabin', title: 'The cabin', site_name: 'Example', kind: 'link' as const, image: JPEG };
    const wire = await sealMessage(key, { t: 'text', text: 'Look https://example.com/cabin', preview });
    expect(wire).not.toContain('cabin');
    expect(wire).not.toContain('Example');
    expect(await openMessage(key, wire)).toEqual({ t: 'text', text: 'Look https://example.com/cabin', preview });
  }, 30_000);

  it('keeps a message from before previews exactly as it was, and drops fields it does not know', async () => {
    const { key } = await v1Chat('payload payload');
    expect(await openMessage(key, await sealMessage(key, { t: 'text', text: 'plain' }))).toEqual({ t: 'text', text: 'plain' });
    const future = await sealMessage(key, { t: 'text', text: 'hi', reply: 7, colour: 'red' } as unknown as PrivatePayload);
    expect(await openMessage(key, future)).toEqual({ t: 'text', text: 'hi' });
  }, 30_000);

  it('a preview that is not safe to draw is dropped and the words still open', async () => {
    const { key } = await v1Chat('payload payload');
    const bad = [
      { url: 'javascript:alert(1)', title: 'x', kind: 'link' },
      { url: 'https://example.com', kind: 'link', image: 'https://tracker.example/pixel.gif' },
      'not an object',
    ];
    for (const preview of bad) {
      const wire = await sealMessage(key, { t: 'text', text: 'hi', preview } as unknown as PrivatePayload);
      const opened = await openMessage(key, wire);
      expect(opened).toEqual({ t: 'text', text: 'hi' });
    }
    // A picture from the web (a tracking pixel) is never drawn: only a sealed thumbnail is.
    const pixel = await sealMessage(key, {
      t: 'text', text: 'hi', preview: { url: 'https://example.com', title: 'x', kind: 'link', image: 'https://tracker.example/pixel.gif' },
    } as unknown as PrivatePayload);
    expect(await openMessage(key, pixel)).toEqual({ t: 'text', text: 'hi', preview: { url: 'https://example.com', title: 'x', kind: 'link' } });
    // A player that is not one of ours turns the card into a plain link: nothing loads from elsewhere.
    const sneaky = await sealMessage(key, {
      t: 'text', text: 'hi',
      preview: { url: 'https://example.com', title: 'x', kind: 'video', embed: { provider: 'iframe', src: 'https://evil.example/' } },
    } as unknown as PrivatePayload);
    expect(await openMessage(key, sneaky)).toEqual({ t: 'text', text: 'hi', preview: { url: 'https://example.com', title: 'x', kind: 'link' } });
  }, 30_000);
});
