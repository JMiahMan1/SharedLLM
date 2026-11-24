import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  MAX_CHOICES,
  PAUSED_STORAGE_KEY,
  animationChoice,
  isAnimatedImage,
  isGifClip,
  isGifHost,
  linkClipKey,
  mayAnimate,
  privatePictureKey,
  resetAnimationChoicesForTests,
  setAnimationChoice,
  showsFileItself,
  startsPaused,
  subscribeAnimationChoices,
  talkFileKey,
} from './animatedImage';
import { fakeGif, fakePngHeader, fakeWebpHeader } from '../test/fakes/fakeGif';
import { fakeJpeg } from '../test/fakes/fakeJpeg';

/** The two-frame 2x2 GIF the private chat's keyboard test sends. */
const TINY_TWO_FRAMES = Uint8Array.from(
  atob('R0lGODlhAgACAPAAAP8AAAAA/yH/C05FVFNDQVBFMi4wAwEAAAAh+QQACgAAACwAAAAAAgACAAACAoRRACH5BAAKAAAALAAAAAACAAIAgAAA/wAAAAIChFEAOw=='),
  (c) => c.charCodeAt(0),
);

beforeEach(() => {
  window.sessionStorage.clear();
  resetAnimationChoicesForTests();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('isAnimatedImage', () => {
  it('a GIF animates only with more than one frame', () => {
    expect(isAnimatedImage(fakeGif(8, 6, [[255, 0, 0], [0, 0, 255]]))).toBe(true);
    expect(isAnimatedImage(fakeGif(8, 6, [[255, 0, 0]]))).toBe(false);
    expect(isAnimatedImage(TINY_TWO_FRAMES)).toBe(true);
  });

  it('a GIF of four bigger frames is read to its second frame', () => {
    const gif = fakeGif(40, 30, [[1, 2, 3], [4, 5, 6], [7, 8, 9], [10, 11, 12]]);
    expect(isAnimatedImage(gif)).toBe(true);
  });

  it('a GIF cut short after its first frame is not called animated', () => {
    const gif = fakeGif(8, 6, [[255, 0, 0], [0, 0, 255]]);
    expect(isAnimatedImage(gif.slice(0, 40))).toBe(false);
  });

  it('WebP by its animation flag, PNG by an acTL of more than one frame', () => {
    expect(isAnimatedImage(fakeWebpHeader(true))).toBe(true);
    expect(isAnimatedImage(fakeWebpHeader(false))).toBe(false);
    expect(isAnimatedImage(fakePngHeader(3))).toBe(true);
    expect(isAnimatedImage(fakePngHeader(1))).toBe(false);
    expect(isAnimatedImage(fakePngHeader())).toBe(false);
  });

  it('judges the bytes, not the name: a JPEG and garbage never animate', () => {
    expect(isAnimatedImage(fakeJpeg({}))).toBe(false);
    expect(isAnimatedImage(new Uint8Array([1, 2, 3]))).toBe(false);
    expect(isAnimatedImage(new Uint8Array())).toBe(false);
  });

  it('only GIF, WebP and PNG are worth reading', () => {
    expect(mayAnimate('image/gif')).toBe(true);
    expect(mayAnimate('IMAGE/WEBP')).toBe(true);
    expect(mayAnimate('image/png')).toBe(true);
    expect(mayAnimate('image/jpeg')).toBe(false);
    expect(mayAnimate('image/heic')).toBe(false);
    expect(mayAnimate(undefined)).toBe(false);
  });
});

describe('who paused what', () => {
  it('nothing chosen plays, unless reduced motion is asked for; a choice always wins', () => {
    expect(startsPaused(null, false)).toBe(false);
    expect(startsPaused(null, true)).toBe(true);
    expect(startsPaused('playing', true)).toBe(false);
    expect(startsPaused('paused', false)).toBe(true);
  });

  it('a choice is kept in sessionStorage and read back after a reload', () => {
    const key = privatePictureKey('room1', 'ab'.repeat(16));
    expect(animationChoice(key)).toBeNull();
    setAnimationChoice(key, 'paused');
    expect(JSON.parse(window.sessionStorage.getItem(PAUSED_STORAGE_KEY)!)).toEqual({ [key]: 'paused' });
    // A reload: the page's memory is gone, the session's storage is not.
    resetAnimationChoicesForTests();
    expect(animationChoice(key)).toBe('paused');
    setAnimationChoice(key, 'playing');
    resetAnimationChoicesForTests();
    expect(animationChoice(key)).toBe('playing');
  });

  it('a new session (empty storage) starts everything playing again', () => {
    setAnimationChoice('talk:7', 'paused');
    window.sessionStorage.clear();
    resetAnimationChoicesForTests();
    expect(animationChoice('talk:7')).toBeNull();
  });

  it('without storage the choice still holds for the page', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('denied');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota');
    });
    setAnimationChoice('talk:1', 'paused');
    expect(animationChoice('talk:1')).toBe('paused');
  });

  it('a damaged stored value is ignored, not trusted', () => {
    window.sessionStorage.setItem(PAUSED_STORAGE_KEY, JSON.stringify({ 'talk:1': 'paused', 'talk:2': 'loud', 'talk:3': 1 }));
    expect(animationChoice('talk:1')).toBe('paused');
    expect(animationChoice('talk:2')).toBeNull();
    resetAnimationChoicesForTests();
    window.sessionStorage.setItem(PAUSED_STORAGE_KEY, '{not json');
    expect(animationChoice('talk:1')).toBeNull();
  });

  it('keeps at most MAX_CHOICES, dropping the oldest', () => {
    for (let i = 0; i <= MAX_CHOICES; i += 1) setAnimationChoice(`talk:${i}`, 'paused');
    expect(animationChoice('talk:0')).toBeNull();
    expect(animationChoice(`talk:${MAX_CHOICES}`)).toBe('paused');
    expect(Object.keys(JSON.parse(window.sessionStorage.getItem(PAUSED_STORAGE_KEY)!))).toHaveLength(MAX_CHOICES);
  });

  it('tells every listener (the bubble and the viewer of one picture)', () => {
    const listener = vi.fn();
    const stop = subscribeAnimationChoices(listener);
    setAnimationChoice('talk:1', 'paused');
    expect(listener).toHaveBeenCalledTimes(1);
    stop();
    setAnimationChoice('talk:1', 'playing');
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it('keys name the message or file, never an object URL', () => {
    expect(privatePictureKey('room1', 'cd'.repeat(16))).toBe(`private:room1:${'cd'.repeat(16)}`);
    expect(talkFileKey({ id: '42', path: '/Talk/a.gif' })).toBe('talk:42');
    expect(talkFileKey({ path: '/Talk/a.gif' })).toBe('talk:/Talk/a.gif');
    expect(linkClipKey('https://giphy.com/gifs/x')).toBe('link:https://giphy.com/gifs/x');
  });
});

describe('family chat files and pasted GIF links', () => {
  it('a GIF or WebP up to the limit is shown as itself (a preview is one still frame)', () => {
    expect(showsFileItself({ mimetype: 'image/gif', size: 120_000 })).toBe(true);
    expect(showsFileItself({ mimetype: 'image/webp', size: '2048' })).toBe(true);
    expect(showsFileItself({ mimetype: 'image/gif', size: 50 * 1024 * 1024 })).toBe(false);
    expect(showsFileItself({ mimetype: 'image/gif' })).toBe(false);
    expect(showsFileItself({ mimetype: 'image/jpeg', size: 1000 })).toBe(false);
  });

  it('GIPHY and Tenor clips are GIFs; other clips are videos', () => {
    expect(isGifHost('media.giphy.com')).toBe(true);
    expect(isGifHost('media1.tenor.com')).toBe(true);
    expect(isGifHost('giphy.com.example.net')).toBe(false);
    expect(isGifHost('notgiphy.com')).toBe(false);
    const clip = (src: string, url = src) => ({ url, title: 'x', kind: 'video' as const, embed: { provider: 'file' as const, src, mime: 'video/mp4' } });
    expect(isGifClip(clip('https://media.giphy.com/media/abc/giphy.mp4'))).toBe(true);
    expect(isGifClip(clip('https://cdn.example/clip.mp4', 'https://tenor.com/view/x'))).toBe(true);
    expect(isGifClip(clip('https://cdn.example/clip.mp4'))).toBe(false);
    expect(isGifClip({ url: 'https://youtu.be/x', kind: 'video', embed: { provider: 'youtube', id: 'x' } })).toBe(false);
  });
});
