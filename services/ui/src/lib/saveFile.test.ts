import { describe, it, expect, vi, beforeEach } from 'vitest';

const native = vi.hoisted(() => ({
  platform: 'web',
  begin: vi.fn(),
  append: vi.fn(),
  discard: vi.fn(),
  save: vi.fn(),
}));
vi.mock('@capacitor/core', () => ({
  Capacitor: { getPlatform: () => native.platform },
  registerPlugin: () => ({
    begin: native.begin,
    append: native.append,
    discard: native.discard,
    save: native.save,
  }),
}));

import { saveBlob, CHUNK_BYTES } from './saveFile';
import { base64ToBytes } from './base64';

const unimplemented = () => Object.assign(new Error('not implemented'), { code: 'UNIMPLEMENTED' });

describe('saveBlob', () => {
  beforeEach(() => {
    native.begin.mockReset().mockResolvedValue({ id: 'abc' });
    native.append.mockReset().mockResolvedValue(undefined);
    native.discard.mockReset().mockResolvedValue(undefined);
    native.save.mockReset();
    URL.createObjectURL = vi.fn(() => 'blob:x');
    URL.revokeObjectURL = vi.fn();
  });

  it('stages the bytes in pieces and then opens the Android save picker', async () => {
    native.platform = 'android';
    native.save.mockResolvedValue({ saved: true });
    const saved = await saveBlob(new Blob(['hi'], { type: 'image/png' }), 'dir/photo.png');
    expect(saved).toBe(true);
    expect(native.append).toHaveBeenCalledWith({ id: 'abc', data: btoa('hi') });
    expect(native.save).toHaveBeenCalledWith({ id: 'abc', filename: 'photo.png', mimeType: 'image/png' });
  });

  it('never sends a large file in one call, and the pieces add up to the file', async () => {
    native.platform = 'android';
    native.save.mockResolvedValue({ saved: true });
    const size = CHUNK_BYTES * 2 + 5;
    const bytes = new Uint8Array(size);
    for (let i = 0; i < size; i += 1) bytes[i] = (i * 31) & 255;
    await saveBlob(new Blob([bytes]), 'backup.jarvis-backup');
    expect(native.append).toHaveBeenCalledTimes(3);
    const pieces = native.append.mock.calls.map(([o]) => base64ToBytes(o.data));
    expect(pieces.map((p) => p.length)).toEqual([CHUNK_BYTES, CHUNK_BYTES, 5]);
    const joined = new Uint8Array(size);
    let at = 0;
    for (const p of pieces) {
      joined.set(p, at);
      at += p.length;
    }
    // Compared as one buffer: toEqual walks 1.5 MB element by element, which
    // took seconds alone and ran past the time limit in a full run.
    expect(Buffer.compare(joined, bytes)).toBe(0);
    const [options] = native.save.mock.calls[0];
    expect(options).not.toHaveProperty('data');
  });

  it('stages nothing for an empty file but still saves it', async () => {
    native.platform = 'android';
    native.save.mockResolvedValue({ saved: true });
    expect(await saveBlob(new Blob([]), 'empty.txt')).toBe(true);
    expect(native.append).not.toHaveBeenCalled();
    expect(native.save).toHaveBeenCalledWith({ id: 'abc', filename: 'empty.txt', mimeType: 'application/octet-stream' });
  });

  it('discards the staged file when a piece fails', async () => {
    native.platform = 'android';
    native.append.mockRejectedValueOnce(Object.assign(new Error('disk full'), { code: 'ERR' }));
    await expect(saveBlob(new Blob(['abc']), 'a.txt')).rejects.toThrow('disk full');
    expect(native.discard).toHaveBeenCalledWith({ id: 'abc' });
    expect(native.save).not.toHaveBeenCalled();
  });

  it('sends the whole file in one call to an app build from before staging', async () => {
    native.platform = 'android';
    native.begin.mockRejectedValue(unimplemented());
    native.save.mockResolvedValue({ saved: true });
    expect(await saveBlob(new Blob(['hi'], { type: 'text/plain' }), 'a.txt')).toBe(true);
    expect(native.save).toHaveBeenCalledWith({ data: btoa('hi'), filename: 'a.txt', mimeType: 'text/plain' });
  });

  it('reports a cancelled picker and a failed save', async () => {
    native.platform = 'android';
    native.save.mockResolvedValueOnce({ saved: false });
    expect(await saveBlob(new Blob(['a']), 'a.jpg')).toBe(false);
    native.save.mockRejectedValueOnce(Object.assign(new Error('disk full'), { code: 'ERR' }));
    await expect(saveBlob(new Blob(['a']), 'a.jpg')).rejects.toThrow('disk full');
  });

  it('falls back to a browser download on an app build without the plugin, and in a browser', async () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    native.platform = 'android';
    native.begin.mockRejectedValue(unimplemented());
    native.save.mockRejectedValueOnce(unimplemented());
    expect(await saveBlob(new Blob(['a']), 'a.jpg')).toBe(true);
    native.platform = 'web';
    expect(await saveBlob(new Blob(['a']), 'b.jpg')).toBe(true);
    expect(click).toHaveBeenCalledTimes(2);
    expect(native.save).toHaveBeenCalledTimes(1);
  });
});

describe('imageKindOf', () => {
  it('names real pictures and refuses anything else', async () => {
    const { imageKindOf } = await import('./saveFile');
    expect(imageKindOf(Uint8Array.from([0xff, 0xd8, 0xff, 0xe0]))).toBe('jpeg');
    expect(imageKindOf(Uint8Array.from([0x89, 0x50, 0x4e, 0x47]))).toBe('png');
    expect(imageKindOf(new TextEncoder().encode('RIFF....WEBP'))).toBe('webp');
    expect(imageKindOf(new TextEncoder().encode('/9j/4AAQ'))).toBeNull(); // a JPEG as base64 text
    expect(imageKindOf(Uint8Array.from([0xef, 0xbf, 0xbd, 0xef]))).toBeNull(); // bytes mangled as text
    expect(imageKindOf(new Uint8Array())).toBeNull();
  });
});
