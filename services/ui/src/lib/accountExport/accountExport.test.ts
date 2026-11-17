import { describe, expect, it } from 'vitest';
import {
  CHUNK_BYTES,
  DamagedFileError,
  FILE_SUFFIX,
  MAGIC,
  PBKDF2_ITERATIONS,
  WrongPasswordError,
  encryptStream,
  openExport,
  readHeader,
} from './crypto';
import {
  IncompleteExportError,
  addCounts,
  displayValue,
  endChecker,
  exportFileName,
  exportPasswordProblem,
  passwordStrength,
  readExport,
  restoreParts,
  summarize,
} from './model';
import { PAGE_DECRYPTOR, buildEncryptedPage } from './page';

const PASSWORD = 'correct horse battery staple';
// Small chunks so a short payload still spans several of them.
const SMALL = { chunkBytes: 1024 } as const;

function streamOf(...parts: (string | Uint8Array)[]): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const p of parts) controller.enqueue(typeof p === 'string' ? enc.encode(p) : p);
      controller.close();
    },
  });
}

function ndjson(sections = 3, rowsEach = 20): string {
  const lines = [
    { kind: 'manifest', format: 'jarvis-account-export', version: 1, exported_at: '2026-01-01T00:00:00Z',
      subject: { username: 'sam' }, exported_by: { username: 'sam' }, services: ['identity', 'health', 'geo'],
      excluded: [{ what: 'Passwords', why: 'never' }] },
    { kind: 'section', service: 'identity', collections: { profile: [{ username: 'sam', display_name: 'Sam Example' }], theme: [{ data: '{"theme_id":"forest"}' }] } },
  ];
  for (let s = 0; s < sections; s += 1) {
    lines.push({ kind: 'section', service: 'health', collections: { metrics: Array.from({ length: rowsEach }, (_, i) => ({ id: s * 1000 + i, value: Math.random() })) } } as never);
  }
  lines.push({ kind: 'section', service: 'geo', collections: { location_history: [{ t: 1 }, { t: 2 }], trips: [{ id: 't1' }] } } as never);
  lines.push({ kind: 'end', sections: 3, failed: [] } as never);
  return lines.map((l) => JSON.stringify(l)).join('\n') + '\n';
}

async function bytesOf(blob: Blob): Promise<Uint8Array> {
  return new Uint8Array(await blob.arrayBuffer());
}

/** Read a stream to text, letting its own error through (Response.text would wrap it). */
async function textOf(stream: ReadableStream<Uint8Array>): Promise<string> {
  const reader = stream.getReader();
  const dec = new TextDecoder();
  let out = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return out + dec.decode();
    out += dec.decode(value, { stream: true });
  }
}

describe('account export file', () => {
  it('round-trips through the password, in many chunks, compressed', async () => {
    const payload = ndjson(30, 40);
    const file = await encryptStream(streamOf(payload), PASSWORD, SMALL);
    const bytes = await bytesOf(file);
    expect(Array.from(bytes.slice(0, 12))).toEqual(Array.from(MAGIC));
    const { header } = await readHeader(file);
    expect(header.kdf.iterations).toBeGreaterThanOrEqual(PBKDF2_ITERATIONS);
    expect(header.compression).toBe('gzip');
    expect(JSON.stringify(header)).not.toContain('Sam Example');
    expect(new TextDecoder().decode(bytes)).not.toContain('Sam Example');
    const opened = await openExport(file, PASSWORD);
    expect(await textOf(opened.stream)).toBe(payload);
  });

  it('round-trips without compression and with a payload that fills its chunks exactly', async () => {
    const exact = 'x'.repeat(1024 * 3);
    const file = await encryptStream(streamOf(exact), PASSWORD, { ...SMALL, compress: false });
    expect(file.size).toBe(16 + (await readHeader(file)).bodyOffset - 16 + 3 * (1024 + 16) + 16);
    expect(await textOf((await openExport(file, PASSWORD)).stream)).toBe(exact);
  });

  it('tells a wrong password from a damaged file', async () => {
    const file = await encryptStream(streamOf(ndjson()), PASSWORD, SMALL);
    await expect(openExport(file, 'not the password at all')).rejects.toBeInstanceOf(WrongPasswordError);
  });

  it('refuses a changed byte, a dropped or swapped chunk, a cut, extra bytes and a changed header', async () => {
    const file = await encryptStream(streamOf('y'.repeat(5000)), PASSWORD, { ...SMALL, compress: false });
    const bytes = await bytesOf(file);
    const { bodyOffset } = await readHeader(file);
    const step = 1024 + 16;
    const read = async (b: Uint8Array) => textOf((await openExport(new Blob([b as BlobPart]), PASSWORD)).stream);

    const flipped = bytes.slice();
    flipped[bodyOffset + 10] ^= 1;
    await expect(read(flipped)).rejects.toBeInstanceOf(DamagedFileError);

    const dropped = new Uint8Array([...bytes.slice(0, bodyOffset + step), ...bytes.slice(bodyOffset + 2 * step)]);
    await expect(read(dropped)).rejects.toBeInstanceOf(DamagedFileError);

    const swapped = new Uint8Array([
      ...bytes.slice(0, bodyOffset),
      ...bytes.slice(bodyOffset + step, bodyOffset + 2 * step),
      ...bytes.slice(bodyOffset, bodyOffset + step),
      ...bytes.slice(bodyOffset + 2 * step),
    ]);
    await expect(read(swapped)).rejects.toBeInstanceOf(DamagedFileError);

    const cut = bytes.slice(0, bodyOffset + 2 * step);
    await expect(read(cut)).rejects.toBeInstanceOf(DamagedFileError);

    const extra = new Uint8Array([...bytes, 1, 2, 3]);
    await expect(read(extra)).rejects.toBeInstanceOf(DamagedFileError);

    // A header edit that leaves the key check intact still breaks every chunk.
    const text = new TextDecoder().decode(bytes.slice(16, bodyOffset));
    const edited = text.replace('"created_at":"2', '"created_at":"1');
    expect(edited).not.toBe(text);
    const headerChanged = new Uint8Array([...bytes.slice(0, 16), ...new TextEncoder().encode(edited), ...bytes.slice(bodyOffset)]);
    await expect(read(headerChanged)).rejects.toBeInstanceOf(DamagedFileError);

    await expect(openExport(new Blob(['not an export at all']), PASSWORD)).rejects.toBeInstanceOf(DamagedFileError);
  });

  it('reads its input a chunk at a time: a large stream never needs to be whole', async () => {
    let pulled = 0;
    const big = new ReadableStream<Uint8Array>({
      pull(controller) {
        if (pulled >= 64) return controller.close();
        pulled += 1;
        controller.enqueue(new Uint8Array(CHUNK_BYTES / 4).fill(pulled));
      },
    });
    let progress = 0;
    const file = await encryptStream(big, PASSWORD, { compress: false, onProgress: (n) => { progress = n; } });
    expect(progress).toBe(64 * (CHUNK_BYTES / 4));
    expect(file.size).toBeGreaterThan(16 * 1024 * 1024);
    const opened = await openExport(file, PASSWORD);
    const reader = opened.stream.getReader();
    let total = 0;
    let biggest = 0;
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      total += value.length;
      biggest = Math.max(biggest, value.length);
    }
    expect(total).toBe(64 * (CHUNK_BYTES / 4));
    expect(biggest).toBeLessThanOrEqual(CHUNK_BYTES);
  });

  it('refuses a weak key stretch', async () => {
    await expect(encryptStream(streamOf('x'), PASSWORD, { iterations: 1000 })).rejects.toThrow(/too weak/);
  });

  it('names files with the suffix', () => {
    expect(exportFileName('sam', new Date('2026-03-04T05:06:07Z'))).toBe(`jarvis-export-sam-2026-03-04${FILE_SUFFIX}`);
    expect(exportFileName('../evil')).toMatch(/^jarvis-export-evil-/);
  });
});

describe('viewer model', () => {
  it('reads sections, joins repeated ones and summarises', async () => {
    const opened = await readExport(streamOf(ndjson(2, 3)));
    expect(opened.manifest.subject.username).toBe('sam');
    expect(opened.services.health.metrics).toHaveLength(6);
    const summary = summarize(opened);
    expect(summary.find((s) => s.service === 'health')?.total).toBe(6);
    expect(summary[0].label).toBe('Profile and settings');
  });

  it('refuses a stream without its end line', async () => {
    const cut = ndjson().split('\n').slice(0, -2).join('\n');
    await expect(readExport(streamOf(cut))).rejects.toBeInstanceOf(IncompleteExportError);
    await expect(readExport(streamOf(''))).rejects.toBeInstanceOf(IncompleteExportError);
  });

  it('restores in the manifest order and never sends the location trail back', async () => {
    const parts = restoreParts(await readExport(streamOf(ndjson(1, 1))));
    expect(parts.map((p) => p.service)).toEqual(['identity', 'health', 'geo']);
    expect(Object.keys(parts[2].collections)).toEqual(['trips']);
  });

  it('checks the end of the stream while passing it through', async () => {
    const good = endChecker();
    expect(await textOf(streamOf(ndjson()).pipeThrough(good.stream))).toContain('"kind":"end"');
    const bad = endChecker();
    await expect(textOf(streamOf(ndjson().split('\n').slice(0, -2).join('\n')).pipeThrough(bad.stream))).rejects.toBeInstanceOf(IncompleteExportError);
  });

  it('adds counts and shows values plainly', () => {
    expect(addCounts({ a: { add: 1, conflict: 2 }, b: { add: 3, unchanged: 4 } })).toEqual({ add: 4, update: 0, conflict: 2, unchanged: 4, skipped: 0 });
    expect(displayValue('{"theme_id":"forest"}')).toBe('{"theme_id":"forest"}');
    expect(displayValue('{}')).toBe('');
    expect(displayValue({ $base64: 'AAA' })).toBe('(picture)');
    expect(displayValue(3)).toBe('3');
  });

  it('hints at password strength', () => {
    expect(passwordStrength('short').score).toBe(0);
    expect(passwordStrength('aaaaaaaaaaaa').score).toBe(1);
    expect(passwordStrength(PASSWORD).score).toBe(3);
    expect(exportPasswordProblem(PASSWORD, PASSWORD)).toBeNull();
    expect(exportPasswordProblem(PASSWORD, 'other')).toMatch(/match/);
    expect(exportPasswordProblem('abcdefghij', 'abcdefghij')).toMatch(/stronger/);
  });
});

describe('encrypted page', () => {
  async function pageOpener(): Promise<(bytes: Uint8Array, password: string) => Promise<string>> {
    return new Function(`${PAGE_DECRYPTOR}; return jarvisOpenExport;`)() as (bytes: Uint8Array, password: string) => Promise<string>;
  }

  it('holds only the encrypted file and opens it with the password', async () => {
    const payload = ndjson(3, 5);
    const file = await bytesOf(await encryptStream(streamOf(payload), PASSWORD, SMALL));
    const html = buildEncryptedPage(file, 'Export of <sam>');
    expect(html).not.toContain('Sam Example');
    expect(html).not.toContain('<sam>');
    expect(html).toContain("default-src 'none'");
    const open = await pageOpener();
    expect(await open(file, PASSWORD)).toBe(payload);
    await expect(open(file, 'wrong password here')).rejects.toThrow(/does not open/);
    const damaged = file.slice();
    damaged[damaged.length - 5] ^= 1;
    await expect(open(damaged, PASSWORD)).rejects.toThrow(/changed or damaged/);
  });
});
