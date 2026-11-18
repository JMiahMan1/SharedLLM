import { afterEach, describe, expect, it } from 'vitest';
import {
  hostOf,
  linkPreviewsOn,
  MAX_PREVIEW_DESCRIPTION_CHARS,
  MAX_PREVIEW_IMAGE_BYTES,
  MAX_PREVIEW_TITLE_CHARS,
  playerUrl,
  readLinkPreview,
  setLinkPreviews,
  webAddress,
} from './privateLinkPreview';

const jpegBytes = (n: number) => String.fromCharCode(0xff, 0xd8, 0xff, 0xe0) + 'a'.repeat(Math.max(0, n - 4));
const JPEG = `data:image/jpeg;base64,${btoa(jpegBytes(64))}`;
const PNG = `data:image/png;base64,${btoa('\u0089PNG\r\n\u001a\n' + 'b'.repeat(40))}`;

describe('readLinkPreview', () => {
  it('keeps a well-formed link preview as it is', () => {
    const preview = { url: 'https://example.com/a?b=1', title: 'A page', description: 'About it', site_name: 'Example', kind: 'link', image: JPEG };
    expect(readLinkPreview(preview)).toEqual(preview);
    expect(readLinkPreview({ url: 'http://example.org/', title: 'Old', kind: 'link', image: PNG })).toEqual({
      url: 'http://example.org/', title: 'Old', kind: 'link', image: PNG,
    });
  });

  it.each([
    ['not http', { url: 'javascript:alert(1)', title: 't', kind: 'link' }],
    ['a data address', { url: 'data:text/html,hi', title: 't', kind: 'link' }],
    ['a file address', { url: 'file:///etc/passwd', title: 't', kind: 'link' }],
    ['credentials in it', { url: 'https://me:pw@example.com/', title: 't', kind: 'link' }],
    ['no address', { title: 't', kind: 'link' }],
    ['an address too long', { url: `https://example.com/${'a'.repeat(3000)}`, title: 't', kind: 'link' }],
    ['nothing to show', { url: 'https://example.com/', kind: 'link' }],
    ['not an object', 'https://example.com/'],
    ['a list', [{ url: 'https://example.com/', title: 't' }]],
    ['null', null],
  ])('refuses %s', (_why, raw) => {
    expect(readLinkPreview(raw)).toBeNull();
  });

  it('cuts long words and drops control characters', () => {
    const out = readLinkPreview({
      url: 'https://example.com/', kind: 'link',
      title: `Bad${String.fromCharCode(0x202e, 7)} title ${'t'.repeat(1000)}`,
      description: 'd'.repeat(5000),
      site_name: 42,
    })!;
    expect(out.title!.length).toBe(MAX_PREVIEW_TITLE_CHARS);
    expect(out.title!.startsWith('Bad title')).toBe(true);
    expect(out.title).not.toContain(String.fromCharCode(7));
    expect(out.title).not.toContain(String.fromCharCode(0x202e));
    expect(out.description!.length).toBe(MAX_PREVIEW_DESCRIPTION_CHARS);
    expect(out).not.toHaveProperty('site_name');
  });

  it.each([
    ['a web picture (would load from its site)', 'https://tracker.example/p.gif'],
    ['an SVG (can carry script)', `data:image/svg+xml;base64,${btoa('<svg/>')}`],
    ['a GIF', `data:image/gif;base64,${btoa('GIF89a')}`],
    ['JPEG named but PNG inside', `data:image/jpeg;base64,${btoa('\u0089PNG\r\n\u001a\nxxxx')}`],
    ['not base64', 'data:image/jpeg;base64,<script>'],
    ['too large', `data:image/jpeg;base64,${btoa(jpegBytes(MAX_PREVIEW_IMAGE_BYTES + 3000))}`],
  ])('drops a thumbnail that is %s', (_why, image) => {
    expect(readLinkPreview({ url: 'https://example.com/', title: 't', kind: 'link', image })).toEqual({ url: 'https://example.com/', title: 't', kind: 'link' });
  });

  it('keeps players it knows and turns any other into a plain link', () => {
    const yt = { url: 'https://youtu.be/dQw4w9WgXcQ', title: 'v', kind: 'video', embed: { provider: 'youtube', id: 'dQw4w9WgXcQ' } };
    expect(readLinkPreview(yt)).toEqual(yt);
    const vimeo = { url: 'https://vimeo.com/76979871', title: 'v', kind: 'video', embed: { provider: 'vimeo', id: '76979871', hash: 'abcdef12' } };
    expect(readLinkPreview(vimeo)).toEqual(vimeo);
    const file = { url: 'https://cdn.example/a.mp4', title: 'a.mp4', kind: 'video', embed: { provider: 'file', src: 'https://cdn.example/a.mp4', mime: 'video/mp4' } };
    expect(readLinkPreview(file)).toEqual(file);
    const song = { url: 'https://cdn.example/a.mp3', title: 'a.mp3', kind: 'audio', embed: { provider: 'file', src: 'https://cdn.example/a.mp3', mime: 'audio/mpeg' } };
    expect(readLinkPreview(song)).toEqual(song);

    const plain = { url: 'https://example.com/', title: 'v', kind: 'link' };
    for (const embed of [
      { provider: 'youtube', id: 'x"><script>' },
      { provider: 'vimeo', id: '../../evil' },
      { provider: 'iframe', src: 'https://evil.example/' },
      { provider: 'file', src: 'javascript:alert(1)', mime: 'video/mp4' },
      { provider: 'file', src: 'https://cdn.example/a.mp4', mime: 'text/html' },
      { provider: 'file', src: 'https://cdn.example/a.mp3', mime: 'audio/mpeg' },
    ]) {
      expect(readLinkPreview({ url: 'https://example.com/', title: 'v', kind: 'video', embed })).toEqual(plain);
    }
    // An audio kind with a video file, or a link kind with a player, is not trusted either.
    expect(readLinkPreview({ url: 'https://example.com/', title: 'v', kind: 'audio', embed: file.embed })).toEqual(plain);
    expect(readLinkPreview({ url: 'https://example.com/', title: 'v', kind: 'link', embed: yt.embed })).toEqual(plain);
    // An unlisted Vimeo hash of the wrong shape is dropped, the video kept.
    expect(readLinkPreview({ ...vimeo, embed: { provider: 'vimeo', id: '76979871', hash: 'zz&x=1' } })!.embed).toEqual({ provider: 'vimeo', id: '76979871' });
  });
});

describe('players and addresses', () => {
  it('play YouTube from its no-cookie host and Vimeo with Do Not Track', () => {
    expect(playerUrl({ provider: 'youtube', id: 'dQw4w9WgXcQ' })).toMatch(/^https:\/\/www\.youtube-nocookie\.com\/embed\/dQw4w9WgXcQ\?/);
    expect(playerUrl({ provider: 'vimeo', id: '76979871', hash: 'abcdef12' })).toBe('https://player.vimeo.com/video/76979871?autoplay=1&dnt=1&h=abcdef12');
    expect(playerUrl({ provider: 'file', src: 'https://cdn.example/a.mp4', mime: 'video/mp4' })).toBe('https://cdn.example/a.mp4');
  });

  it('read hosts and web addresses', () => {
    expect(hostOf('https://www.example.com/a')).toBe('example.com');
    expect(hostOf('nonsense')).toBe('');
    expect(webAddress('https://example.com')).toBe('https://example.com');
    expect(webAddress('ftp://example.com')).toBeNull();
    expect(webAddress(7)).toBeNull();
  });
});

describe('the Link previews setting', () => {
  afterEach(() => localStorage.clear());

  it('is on until this device turns it off, per chat', () => {
    expect(linkPreviewsOn('chat1')).toBe(true);
    setLinkPreviews('chat1', false);
    expect(linkPreviewsOn('chat1')).toBe(false);
    expect(linkPreviewsOn('chat2')).toBe(true);
    setLinkPreviews('chat1', true);
    expect(linkPreviewsOn('chat1')).toBe(true);
  });
});
