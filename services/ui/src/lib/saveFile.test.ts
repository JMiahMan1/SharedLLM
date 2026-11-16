import { describe, it, expect, vi, beforeEach } from 'vitest';

const native = vi.hoisted(() => ({ platform: 'web', save: vi.fn() }));
vi.mock('@capacitor/core', () => ({
  Capacitor: { getPlatform: () => native.platform },
  registerPlugin: () => ({ save: native.save }),
}));

import { saveBlob } from './saveFile';

describe('saveBlob', () => {
  beforeEach(() => {
    native.save.mockReset();
    URL.createObjectURL = vi.fn(() => 'blob:x');
    URL.revokeObjectURL = vi.fn();
  });

  it('hands the bytes to the Android save picker in the app', async () => {
    native.platform = 'android';
    native.save.mockResolvedValue({ saved: true });
    const saved = await saveBlob(new Blob(['hi'], { type: 'image/png' }), 'dir/photo.png');
    expect(saved).toBe(true);
    expect(native.save).toHaveBeenCalledWith({ data: btoa('hi'), filename: 'photo.png', mimeType: 'image/png' });
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
    native.save.mockRejectedValueOnce(Object.assign(new Error('not implemented'), { code: 'UNIMPLEMENTED' }));
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
