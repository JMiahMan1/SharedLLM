import { describe, expect, it } from 'vitest';
import { extractMaImage, maItemImage } from './maImage';

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

describe('maItemImage', () => {
  it("finds a library item's cover in metadata.images, then its album's", () => {
    expect(maItemImage({ image: null, metadata: { images: [{ path: 'album-1', proxy_id: 'meta' }] } })).toBe('/imageproxy/meta');
    expect(maItemImage({ image: null, metadata: { images: [] }, album: { image: { proxy_id: 'alb' } } })).toBe('/imageproxy/alb');
    expect(maItemImage({ image: null, album: { metadata: { images: [{ proxy_id: 'albmeta' }] } } })).toBe('/imageproxy/albmeta');
    expect(maItemImage({ image: { proxy_id: 'own' }, album: { image: { proxy_id: 'alb' } } })).toBe('/imageproxy/own');
    expect(maItemImage({ name: 'bare' })).toBeNull();
    expect(maItemImage(null)).toBeNull();
  });
});
