import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import PrivateSlideshow from './PrivateSlideshow';
import type { GalleryItem } from './privateGalleryModel';
import type { PrivateVault } from '../../lib/privateChatCrypto';

// Decryption is not what is tested here: the picture and clip hooks answer as told.
const clip = { url: '', failed: '', progress: { done: 0, count: 1 } };
vi.mock('./usePrivateVideo', () => ({
  usePrivateVideo: (_token: string, video: unknown) => (video ? clip : { url: '', failed: '', progress: { done: 0, count: 0 } }),
}));
vi.mock('./usePrivatePicture', () => ({
  noSave: {},
  usePrivatePicture: () => ({ url: 'blob:poster', failed: false }),
}));

const vault = { subscribe: () => () => undefined, unlocked: true } as unknown as PrivateVault;

function item(blob: string, video = false): GalleryItem {
  return {
    messageId: 1,
    blob,
    mime: 'image/jpeg',
    at: 1,
    when: 1,
    own: false,
    actorName: 'Robin',
    comments: [],
    ...(video ? { video: { mime: 'video/webm', size: 10, duration: 2, poster: blob, chunks: ['c'], clip: 'k', chunk_bytes: 10 } as never } : {}),
  };
}

const ITEMS = [item('v'.repeat(32), true), item('p'.repeat(32))];

function show(onClose = vi.fn()) {
  render(<PrivateSlideshow token="roomP" vault={vault} items={ITEMS} startIndex={0} onClose={onClose} />);
  return onClose;
}

const position = () => screen.getByTestId('private-slideshow-position').textContent;

beforeEach(() => {
  vi.useFakeTimers();
  window.localStorage.removeItem('private-slideshow-settings');
  clip.url = '';
  clip.failed = '';
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined);
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('private slideshow: a clip that cannot play', () => {
  it('says the video could not be decrypted, then moves on instead of freezing', async () => {
    clip.failed = 'bad piece';
    show();
    expect(position()).toBe('1 of 2');
    const note = screen.getByTestId('private-slideshow-video-failed');
    expect(note.textContent).toContain('could not be decrypted (bad piece)');
    expect(note.textContent).toContain('Skipping to the next one.');
    await act(async () => {
      vi.advanceTimersByTime(4000);
    });
    expect(position()).toBe('2 of 2');
    expect(screen.queryByTestId('private-slideshow-video-failed')).toBeNull();
  });

  it('says so when play() is refused even muted, and moves on', async () => {
    clip.url = 'blob:clip';
    const play = vi
      .spyOn(HTMLMediaElement.prototype, 'play')
      .mockRejectedValue(new DOMException('The request is not allowed', 'NotAllowedError'));
    show();
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(play).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId('private-slideshow-video-failed').textContent).toContain('would not play (The request is not allowed)');
    await act(async () => {
      vi.advanceTimersByTime(4000);
    });
    expect(position()).toBe('2 of 2');
  });

  it('a play() interrupted by a pause is not a failure', async () => {
    clip.url = 'blob:clip';
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockRejectedValue(new DOMException('interrupted', 'AbortError'));
    show();
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.queryByTestId('private-slideshow-video-failed')).toBeNull();
    await act(async () => {
      vi.advanceTimersByTime(4000);
    });
    // A playing clip advances when it ends, not on a timer.
    expect(position()).toBe('1 of 2');
  });

  it('a clip the browser cannot play says so', async () => {
    clip.url = 'blob:clip';
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
    show();
    fireEvent.error(screen.getByTestId('private-slideshow-video'));
    expect(screen.getByTestId('private-slideshow-video-failed').textContent).toContain('could not be played');
  });
});

describe('private slideshow: closing', () => {
  it('hands back the picture last on screen', () => {
    clip.failed = 'bad piece';
    const onClose = show();
    fireEvent.keyDown(document, { key: 'ArrowRight' });
    fireEvent.click(screen.getByRole('button', { name: 'Close slideshow' }));
    expect(onClose).toHaveBeenCalledWith(ITEMS[1]);
  });
});
