import { describe, expect, it } from 'vitest';
import { extractMaImage } from './maImage';

describe('extractMaImage', () => {
  it('prefers the MA proxy id over the provider-relative path', () => {
    const image = [
      { type: 'thumb', path: 'album-716', provider: 'opensubsonic', proxy_id: 'abc123' },
    ];
    expect(extractMaImage(image)).toBe('/imageproxy/abc123');
  });

  it('falls back to remote and relative URLs when there is no proxy id', () => {
    expect(extractMaImage({ remote_url: 'https://cdn.example/cover.jpg' })).toBe(
      'https://cdn.example/cover.jpg',
    );
    expect(extractMaImage({ path: 'album-716', provider: 'opensubsonic' })).toBe('album-716');
  });

  it('passes strings through and unwraps nested image objects', () => {
    expect(extractMaImage(' http://ma:8095/imageproxy/x ')).toBe('http://ma:8095/imageproxy/x');
    expect(extractMaImage({ image: { proxy_id: 'nested' } })).toBe('/imageproxy/nested');
    expect(extractMaImage(null)).toBeNull();
  });
});
