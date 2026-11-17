import { describe, expect, it } from 'vitest';
import {
  decryptVideo,
  decryptVideoChunk,
  encryptVideo,
  encryptVideoChunk,
  fromBase64,
  importDataKey,
  IV_BYTES,
  MAX_VIDEO_BYTES,
  MAX_VIDEO_CHUNKS,
  openMessage,
  PrivateVault,
  randomBytes,
  sealMessage,
  toBase64,
  VIDEO_CHUNK_BYTES,
  VideoIntegrityError,
  type VideoPayload,
} from './privateChatCrypto';

const key = () => importDataKey(randomBytes(32));

/** A made-up clip: bytes that count up, so a piece out of place would show. */
function fakeClip(size: number): Uint8Array<ArrayBuffer> {
  const out = new Uint8Array(size);
  for (let i = 0; i < size; i += 1) out[i] = (i * 7 + (i >> 8)) & 0xff;
  return out;
}

/** Encrypt into a fake blob store; returns the store and the sealed reference. */
async function sealInto(k: CryptoKey, plain: Uint8Array<ArrayBuffer>, chunkBytes: number) {
  const store = new Map<string, Uint8Array<ArrayBuffer>>();
  let n = 0;
  const sealed = await encryptVideo(
    k,
    new Blob([plain]),
    async (bytes) => {
      n += 1;
      const id = n.toString(16).padStart(32, '0');
      store.set(id, bytes);
      return id;
    },
    { chunkBytes },
  );
  const fetchChunk = async (id: string) => {
    const got = store.get(id);
    if (!got) throw new Error('404');
    return got.slice();
  };
  return { store, sealed, fetchChunk };
}

async function bytesOf(blob: Blob): Promise<Uint8Array> {
  return new Uint8Array(await blob.arrayBuffer());
}

describe('video clips in encrypted pieces', () => {
  it('round-trips a clip of several pieces, the last one shorter, and nothing on the wire is the clip', async () => {
    const k = await key();
    const plain = fakeClip(10_000);
    const { store, sealed, fetchChunk } = await sealInto(k, plain.slice(), 4096);
    expect(sealed.chunks).toHaveLength(3);
    expect(sealed.size).toBe(10_000);
    expect(fromBase64(sealed.clip)).toHaveLength(16);
    // Each piece is IV + ciphertext + tag, and none contains its plaintext.
    const lengths = [...store.values()].map((b) => b.length);
    expect(lengths).toEqual([4096 + 28, 4096 + 28, 1808 + 28]);
    for (const [i, piece] of [...store.values()].entries()) {
      const slice = plain.subarray(i * 4096, i * 4096 + 64);
      expect(Buffer.from(piece).includes(Buffer.from(slice))).toBe(false);
    }
    const blob = await decryptVideo(k, { mime: 'video/webm', ...sealed }, fetchChunk);
    expect(blob.type).toBe('video/webm');
    expect(await bytesOf(blob)).toEqual(plain);
  });

  it('a clip of exactly one piece, and one of exactly two full pieces', async () => {
    const k = await key();
    for (const size of [4096, 8192, 1]) {
      const plain = fakeClip(size);
      const { sealed, fetchChunk } = await sealInto(k, plain, 4096);
      expect(sealed.chunks).toHaveLength(Math.ceil(size / 4096));
      expect(await bytesOf(await decryptVideo(k, { mime: 'video/mp4', ...sealed }, fetchChunk))).toEqual(plain);
    }
  });

  it('refuses pieces in the wrong order', async () => {
    const k = await key();
    const { sealed, fetchChunk } = await sealInto(k, fakeClip(12_000), 4096);
    const swapped = { ...sealed, chunks: [sealed.chunks[1], sealed.chunks[0], sealed.chunks[2]] };
    await expect(decryptVideo(k, { mime: 'video/webm', ...swapped }, fetchChunk)).rejects.toBeInstanceOf(VideoIntegrityError);
  });

  it('refuses a clip cut short: a piece dropped, or the count and size changed to match', async () => {
    const k = await key();
    const { sealed, fetchChunk } = await sealInto(k, fakeClip(12_000), 4096);
    // The list shortened but the size left: the count no longer fits.
    await expect(decryptVideo(k, { mime: 'video/webm', ...sealed, chunks: sealed.chunks.slice(0, 2) }, fetchChunk)).rejects.toBeInstanceOf(
      VideoIntegrityError,
    );
    // The size shortened to fit two pieces: every piece was bound to three pieces of 12,000 bytes.
    await expect(
      decryptVideo(k, { mime: 'video/webm', ...sealed, chunks: sealed.chunks.slice(0, 2), size: 8192 }, fetchChunk),
    ).rejects.toBeInstanceOf(VideoIntegrityError);
    // A piece the server no longer has.
    await expect(
      decryptVideo(k, { mime: 'video/webm', ...sealed }, async (id) => {
        if (id === sealed.chunks[2]) throw new Error('404');
        return fetchChunk(id);
      }),
    ).rejects.toThrow();
  });

  it('refuses a truncated piece and a changed byte', async () => {
    const k = await key();
    const { sealed, fetchChunk } = await sealInto(k, fakeClip(9000), 4096);
    const cut = async (id: string) => (await fetchChunk(id)).slice(0, -1);
    await expect(decryptVideo(k, { mime: 'video/webm', ...sealed }, cut)).rejects.toBeInstanceOf(VideoIntegrityError);
    const flipped = async (id: string, index: number) => {
      const bytes = await fetchChunk(id);
      if (index === 1) bytes[IV_BYTES + 5] ^= 1;
      return bytes;
    };
    await expect(decryptVideo(k, { mime: 'video/webm', ...sealed }, flipped)).rejects.toBeInstanceOf(VideoIntegrityError);
    // A changed IV is a changed byte too.
    const iv = async (id: string) => {
      const bytes = await fetchChunk(id);
      bytes[0] ^= 1;
      return bytes;
    };
    await expect(decryptVideo(k, { mime: 'video/webm', ...sealed }, iv)).rejects.toBeInstanceOf(VideoIntegrityError);
  });

  it('refuses a piece from another clip, even at the same place', async () => {
    const k = await key();
    const a = await sealInto(k, fakeClip(9000), 4096);
    const b = await sealInto(k, fakeClip(9000), 4096);
    const mixed = async (id: string, index: number) => (index === 1 ? b.fetchChunk(b.sealed.chunks[1]) : a.fetchChunk(id));
    await expect(decryptVideo(k, { mime: 'video/webm', ...a.sealed }, mixed)).rejects.toBeInstanceOf(VideoIntegrityError);
  });

  it('refuses the wrong key', async () => {
    const { sealed, fetchChunk } = await sealInto(await key(), fakeClip(5000), 4096);
    await expect(decryptVideo(await key(), { mime: 'video/webm', ...sealed }, fetchChunk)).rejects.toBeInstanceOf(VideoIntegrityError);
  });

  it('binds each piece to its index, count, size and clip id', async () => {
    const k = await key();
    const clip = randomBytes(16);
    const plain = fakeClip(100);
    const piece = await encryptVideoChunk(k, clip, 1, 3, 9000, plain.slice());
    expect(await decryptVideoChunk(k, clip, 1, 3, 9000, piece)).toEqual(plain);
    for (const [c, i, n, s] of [[clip, 0, 3, 9000], [clip, 1, 2, 9000], [clip, 1, 3, 8999], [randomBytes(16), 1, 3, 9000]] as const) {
      await expect(decryptVideoChunk(k, c, i, n, s, piece)).rejects.toBeInstanceOf(VideoIntegrityError);
    }
  });

  it('refuses an empty clip and one over the limit before anything is uploaded', async () => {
    const k = await key();
    let uploads = 0;
    const upload = async () => {
      uploads += 1;
      return '0'.repeat(32);
    };
    await expect(encryptVideo(k, new Blob([]), upload)).rejects.toThrow('empty');
    await expect(encryptVideo(k, new Blob([new Uint8Array(2048)]), upload, { maxBytes: 1024 })).rejects.toThrow('limited');
    expect(uploads).toBe(0);
  });

  it('the limits: 4 MiB pieces, 50 MiB clips, at most 13 pieces, each piece well under the 25 MB blob limit', () => {
    expect(VIDEO_CHUNK_BYTES).toBe(4 * 1024 * 1024);
    expect(MAX_VIDEO_BYTES).toBe(50 * 1024 * 1024);
    expect(MAX_VIDEO_CHUNKS).toBe(13);
    expect(VIDEO_CHUNK_BYTES + IV_BYTES + 16).toBeLessThan(25 * 1024 * 1024);
  });

  it('reports progress per piece both ways', async () => {
    const k = await key();
    const plain = fakeClip(9000);
    const seen: string[] = [];
    const store: Uint8Array<ArrayBuffer>[] = [];
    const sealed = await encryptVideo(k, new Blob([plain]), async (b) => {
      store.push(b);
      return (store.length).toString(16).padStart(32, '0');
    }, { chunkBytes: 4096, onProgress: (d, n) => seen.push(`up ${d}/${n}`) });
    await decryptVideo(k, { mime: 'video/webm', ...sealed }, async (_id, i) => store[i].slice(), (d, n) => seen.push(`down ${d}/${n}`));
    expect(seen).toEqual(['up 1/3', 'up 2/3', 'up 3/3', 'down 1/3', 'down 2/3', 'down 3/3']);
  });
});

describe('the video message', () => {
  const ids = (n: number) => Array.from({ length: n }, (_, i) => (i + 1).toString(16).padStart(32, 'a'));
  const good = (): VideoPayload => ({
    t: 'video', mime: 'video/webm', size: 9000, duration: 12.5, width: 640, height: 360, poster: 'b'.repeat(32),
    chunks: ids(3), clip: toBase64(randomBytes(16)), chunk_bytes: 4096, name: 'clip.webm', sent_at: 1_700_000_000, taken_at: 1_699_999_000,
  });

  it('seals and opens with every field inside the encryption', async () => {
    const k = await key();
    const payload = good();
    const sealed = await sealMessage(k, payload);
    for (const secret of ['video', 'clip.webm', payload.poster, payload.chunks[0], '12.5']) expect(sealed).not.toContain(secret);
    expect(await openMessage(k, sealed)).toEqual(payload);
    expect(await openMessage(await key(), sealed)).toBeNull();
  });

  it('opens as unreadable when any part is the wrong shape', async () => {
    const k = await key();
    const bad: Array<Partial<VideoPayload> & Record<string, unknown>> = [
      { mime: 'text/html' },
      { mime: 'video/x-unknown' },
      { poster: 'not-a-blob' },
      { chunks: [] },
      { chunks: ['../../etc', ...ids(2)] },
      { chunks: ids(MAX_VIDEO_CHUNKS + 1), size: (MAX_VIDEO_CHUNKS + 1) * 4096 },
      { size: 0 },
      { size: 20_000 }, // five pieces' worth, three named
      { chunk_bytes: VIDEO_CHUNK_BYTES + 1 },
      { clip: toBase64(randomBytes(8)) },
      { clip: '***' },
    ];
    for (const change of bad) {
      expect(await openMessage(k, await sealMessage(k, { ...good(), ...change } as VideoPayload)), JSON.stringify(change)).toBeNull();
    }
  });

  it('drops odd optional fields but keeps the clip', async () => {
    const k = await key();
    const opened = await openMessage(k, await sealMessage(k, { ...good(), duration: Number.NaN, width: -1, sent_at: 'soon' } as unknown as VideoPayload));
    expect(opened).toMatchObject({ t: 'video', duration: 0 });
    expect(opened).not.toHaveProperty('width');
    expect(opened).not.toHaveProperty('sent_at');
  });
});

describe('the vault and clips', () => {
  it('a clip becomes an in-memory object URL that is revoked on close and on lock, and a locked vault refuses', async () => {
    const created: string[] = [];
    const revoked: string[] = [];
    const real = { create: URL.createObjectURL, revoke: URL.revokeObjectURL };
    URL.createObjectURL = (() => {
      const url = `blob:test/${created.length}`;
      created.push(url);
      return url;
    }) as typeof URL.createObjectURL;
    URL.revokeObjectURL = ((url: string) => revoked.push(url)) as typeof URL.revokeObjectURL;
    try {
      const vault = new PrivateVault();
      await expect(vault.encryptVideo(new Blob([fakeClip(10)]), async () => 'x')).rejects.toThrow('locked');
      vault.adopt(await key());
      const store: Uint8Array<ArrayBuffer>[] = [];
      const sealed = await vault.encryptVideo(new Blob([fakeClip(5000)]), async (b) => {
        store.push(b);
        return (store.length).toString(16).padStart(32, '0');
      });
      expect(store).toHaveLength(1); // a 5,000-byte clip is one 4 MiB piece
      const payload: VideoPayload = { t: 'video', mime: 'video/webm', duration: 1, poster: 'c'.repeat(32), ...sealed };
      const first = await vault.videoUrl(payload, async (_id, i) => store[i].slice());
      const second = await vault.videoUrl(payload, async (_id, i) => store[i].slice());
      expect(vault.liveUrls).toBe(2);
      vault.revoke(first);
      expect(revoked).toEqual([first]);
      vault.revoke(first); // twice is harmless
      expect(revoked).toEqual([first]);
      vault.lock();
      expect(revoked).toEqual([first, second]);
      expect(vault.liveUrls).toBe(0);
      await expect(vault.videoUrl(payload, async (_id, i) => store[i].slice())).rejects.toThrow('locked');
    } finally {
      URL.createObjectURL = real.create;
      URL.revokeObjectURL = real.revoke;
    }
  });
});
