import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import AnimatedImage from './AnimatedImage';
import { PAUSED_STORAGE_KEY, resetAnimationChoicesForTests } from '../../lib/animatedImage';
import { fakeGif } from '../../test/fakes/fakeGif';

function dataUrl(bytes: Uint8Array, mime: string): string {
  return `data:${mime};base64,${btoa(String.fromCharCode(...bytes))}`;
}

const MOVING = dataUrl(fakeGif(6, 4, [[255, 0, 0], [0, 0, 255]]), 'image/gif');
const STILL = dataUrl(fakeGif(6, 4, [[255, 0, 0]]), 'image/gif');

let drawn: unknown[] = [];

/** happy-dom loads no pictures: say it has, with a size, so the still can be drawn. */
function loadPicture(img: HTMLElement) {
  Object.defineProperty(img, 'naturalWidth', { configurable: true, value: 6 });
  Object.defineProperty(img, 'naturalHeight', { configurable: true, value: 4 });
  fireEvent.load(img);
}

const realMatchMedia = window.matchMedia;

function setReducedMotion(on: boolean) {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: (query: string) => ({
      matches: on && query.includes('reduce'),
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    }),
  });
}

beforeEach(() => {
  window.sessionStorage.clear();
  resetAnimationChoicesForTests();
  setReducedMotion(false);
  drawn = [];
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(
    () => ({ drawImage: (img: unknown) => drawn.push(img) }) as unknown as CanvasRenderingContext2D,
  );
});

afterEach(() => {
  vi.restoreAllMocks();
  Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: realMatchMedia });
});

function show(src = MOVING, mime = 'image/gif', onOpen = vi.fn()) {
  return render(
    <AnimatedImage
      src={src}
      mediaKey="private:room1:abc"
      mime={mime}
      alt="Dancing alpaca"
      className="w-60"
      imgProps={{ 'data-testid': 'pic' }}
      wrap={(media) => (
        <button type="button" aria-label="View picture" onClick={onOpen}>
          {media}
        </button>
      )}
    />,
  );
}

describe('AnimatedImage', () => {
  it('a GIF that moves gets a labelled pause toggle beside (not inside) the button that opens it', async () => {
    const onOpen = vi.fn();
    show(MOVING, 'image/gif', onOpen);
    const toggle = await screen.findByRole('button', { name: 'Pause animation' });
    expect(toggle).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByRole('button', { name: 'View picture' })).not.toContainElement(toggle);
    // A 44px target.
    expect(toggle.className).toMatch(/min-h-11/);
    expect(toggle.className).toMatch(/min-w-11/);
    fireEvent.click(toggle);
    expect(onOpen).not.toHaveBeenCalled();
    // Tapping the picture itself still opens it.
    fireEvent.click(screen.getByRole('button', { name: 'View picture' }));
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it('pausing freezes the frame on a canvas and remembers it for the session; playing shows the GIF again', async () => {
    show();
    loadPicture(screen.getByTestId('pic'));
    const toggle = await screen.findByRole('button', { name: 'Pause animation' });
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-pressed', 'true');
    const still = screen.getByRole('img', { name: 'Dancing alpaca' });
    expect(still.tagName).toBe('CANVAS');
    expect(screen.getByTestId('pic').style.display).toBe('none');
    // Drawn from the picture on screen, the frame it was showing.
    expect(drawn).toEqual([screen.getByTestId('pic')]);
    expect(JSON.parse(window.sessionStorage.getItem(PAUSED_STORAGE_KEY)!)).toEqual({ 'private:room1:abc': 'paused' });

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-pressed', 'false');
    expect(screen.queryByTestId('animation-still')).toBeNull();
    expect(screen.getByTestId('pic').style.display).toBe('');
    expect(JSON.parse(window.sessionStorage.getItem(PAUSED_STORAGE_KEY)!)).toEqual({ 'private:room1:abc': 'playing' });
  });

  it('a paused GIF is still paused when it is drawn again (scrolled back, another chat and back, a reload)', async () => {
    const first = show();
    fireEvent.click(await screen.findByRole('button', { name: 'Pause animation' }));
    first.unmount();
    // A reload: only sessionStorage is left.
    resetAnimationChoicesForTests();
    show();
    const toggle = await screen.findByRole('button', { name: 'Pause animation' });
    expect(toggle).toHaveAttribute('aria-pressed', 'true');
    loadPicture(screen.getByTestId('pic'));
    expect(screen.getByTestId('animation-still')).toBeInTheDocument();
    expect(drawn).toHaveLength(1);
  });

  it('the toggle is a real button, so the keyboard reaches and presses it', async () => {
    show();
    const toggle = await screen.findByRole('button', { name: 'Pause animation' });
    toggle.focus();
    expect(document.activeElement).toBe(toggle);
    expect(toggle.tagName).toBe('BUTTON');
    expect(toggle).toHaveAttribute('type', 'button');
  });

  it('reduced motion starts it paused, without a moving frame first, until play is pressed this session', async () => {
    setReducedMotion(true);
    const first = show();
    // Not yet known to animate: held invisible, never shown moving.
    expect(screen.getByTestId('pic').className).toMatch(/opacity-0/);
    const toggle = await screen.findByRole('button', { name: 'Pause animation' });
    expect(toggle).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('animation-still')).toBeInTheDocument();
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-pressed', 'false');
    first.unmount();
    show();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Pause animation' })).toHaveAttribute('aria-pressed', 'false'));
  });

  it('a still GIF and a JPEG are plain pictures with no control, and a JPEG is never read', async () => {
    const { unmount } = show(STILL);
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(screen.queryByRole('button', { name: 'Pause animation' })).toBeNull();
    unmount();
    const fetchSpy = vi.spyOn(globalThis, 'fetch');
    render(<AnimatedImage src="blob:fake-photo" mediaKey="talk:9" mime="image/jpeg" alt="Photo" imgProps={{ 'data-testid': 'jpeg' }} />);
    expect(screen.getByTestId('jpeg').tagName).toBe('IMG');
    expect(screen.queryByRole('button', { name: 'Pause animation' })).toBeNull();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('only an in-memory picture is read: an http address is never fetched to find out', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch');
    render(<AnimatedImage src="https://cdn.example/a.gif" mediaKey="talk:10" mime="image/gif" alt="Remote" />);
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: 'Pause animation' })).toBeNull();
  });

  it('two places showing one picture (bubble and viewer) pause together', async () => {
    render(
      <>
        <AnimatedImage src={MOVING} mediaKey="talk:5" mime="image/gif" alt="One" />
        <AnimatedImage src={MOVING} mediaKey="talk:5" mime="image/gif" alt="Two" />
      </>,
    );
    await waitFor(() => expect(screen.getAllByRole('button', { name: 'Pause animation' })).toHaveLength(2));
    const [a, b] = screen.getAllByRole('button', { name: 'Pause animation' });
    fireEvent.click(a);
    expect(b).toHaveAttribute('aria-pressed', 'true');
  });

  it('works without storage', async () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota');
    });
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('denied');
    });
    show();
    const toggle = await screen.findByRole('button', { name: 'Pause animation' });
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-pressed', 'true');
  });
});
