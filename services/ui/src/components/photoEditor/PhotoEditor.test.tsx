import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import type { Decoded } from '../../lib/photoEdit/decode';
import type { EditRecipe } from '../../lib/photoEdit/recipe';

// Reading and writing pictures needs a real canvas (the Playwright spec covers them); here they answer as told.
const decodeMock = vi.fn<(blob: Blob) => Promise<Decoded>>();
const exportMock = vi.fn();
vi.mock('../../lib/photoEdit/decode', async (actual) => ({
  ...(await actual<typeof import('../../lib/photoEdit/decode')>()),
  decodePicture: (blob: Blob) => decodeMock(blob),
}));
vi.mock('../../lib/photoEdit/encode', async (actual) => ({
  ...(await actual<typeof import('../../lib/photoEdit/encode')>()),
  exportEdit: (...args: unknown[]) => exportMock(...args),
}));

import PhotoEditor from './PhotoEditor';
import { PhotoEditError } from '../../lib/photoEdit/decode';
import { usePhotoEditGate } from './usePhotoEditGate';

function frames(count: number, firstFrameOnly = false): Decoded {
  const frame = { width: 4, height: 3, data: new Uint8ClampedArray(48).fill(200), duration: count > 1 ? 80 : 0 };
  return { frames: Array.from({ length: count }, () => ({ ...frame })), animated: count > 1 || firstFrameOnly, firstFrameOnly, droppedFrames: 0, sourceWidth: 4, sourceHeight: 3 };
}

const original = new Blob([new Uint8Array([0xff, 0xd8, 1, 2])], { type: 'image/jpeg' });

beforeEach(() => {
  decodeMock.mockReset();
  exportMock.mockReset();
});
afterEach(() => vi.useRealTimers());

describe('PhotoEditor', () => {
  it('sends the original untouched when nothing was changed, with an empty recipe', async () => {
    decodeMock.mockResolvedValue(frames(1));
    const onDone = vi.fn();
    render(<PhotoEditor source={original} maxBytes={1000} onDone={onDone} onCancel={() => {}} />);
    await screen.findByTestId('photo-editor-tools');
    fireEvent.click(screen.getByTestId('photo-editor-done'));
    await waitFor(() => expect(onDone).toHaveBeenCalledOnce());
    const result = onDone.mock.calls[0][0];
    expect(result.blob).toBe(original);
    expect(result.original).toBe(original);
    expect(result.recipe.filter).toBeNull();
    expect(exportMock).not.toHaveBeenCalled();
  });

  it('builds the recipe from the taps: a filter, a slider, a crop shape, words and a sticker', async () => {
    decodeMock.mockResolvedValue(frames(1));
    const edited = new Blob(['x'], { type: 'image/jpeg' });
    exportMock.mockImplementation(async (_d: Decoded, recipe: EditRecipe, source: Blob) => ({ blob: edited, mime: 'image/jpeg', width: 3, height: 3, animated: false, recipe, original: source }));
    const onDone = vi.fn();
    render(<PhotoEditor source={original} maxBytes={1000} onDone={onDone} onCancel={() => {}} />);
    await screen.findByTestId('photo-editor-tools');

    fireEvent.click(screen.getByRole('button', { name: 'Noir filter' }));
    expect(screen.getByRole('button', { name: 'Noir filter' })).toHaveAttribute('aria-pressed', 'true');

    fireEvent.click(screen.getByTestId('photo-editor-tool-adjust'));
    fireEvent.change(screen.getByLabelText('Warmth'), { target: { value: '40' } });

    fireEvent.click(screen.getByTestId('photo-editor-tool-crop'));
    fireEvent.click(screen.getByTestId('photo-editor-aspect-square'));
    fireEvent.click(screen.getByRole('button', { name: 'Turn right' }));
    fireEvent.click(screen.getByRole('button', { name: 'Flip left to right' }));

    fireEvent.click(screen.getByTestId('photo-editor-tool-text'));
    fireEvent.change(screen.getByLabelText('Words to add'), { target: { value: 'Happy day' } });
    fireEvent.click(screen.getByRole('button', { name: 'Classic' }));
    fireEvent.click(screen.getByTestId('photo-editor-text-add'));

    fireEvent.click(screen.getByTestId('photo-editor-tool-stickers'));
    fireEvent.click(screen.getByRole('button', { name: 'Add a heart sticker' }));
    // The newest item is selected and can be removed with one tap.
    fireEvent.click(screen.getByRole('button', { name: 'Add 🌈' }));
    fireEvent.click(screen.getByTestId('photo-editor-remove'));

    fireEvent.click(screen.getByTestId('photo-editor-done'));
    await waitFor(() => expect(onDone).toHaveBeenCalledOnce());
    const [, recipe, source, options] = exportMock.mock.calls[0];
    expect(source).toBe(original);
    expect(options.maxBytes).toBe(1000);
    expect(recipe).toMatchObject({ filter: 'noir', rotate: 90, flipH: true, aspect: 'square', adjust: { warmth: 40 } });
    expect(recipe.crop).not.toBeNull();
    expect(recipe.overlays.map((o: { kind: string }) => o.kind)).toEqual(['text', 'sticker']);
    expect(recipe.overlays[0]).toMatchObject({ text: 'Happy day', font: 'serif', pill: true });
    expect(onDone.mock.calls[0][0].blob).toBe(edited);
  });

  it('moves, resizes, turns and removes the selected item from the keyboard', async () => {
    decodeMock.mockResolvedValue(frames(1));
    exportMock.mockImplementation(async (_d: Decoded, recipe: EditRecipe, source: Blob) => ({ blob: source, mime: 'image/jpeg', width: 1, height: 1, animated: false, recipe, original: source }));
    const onDone = vi.fn();
    render(<PhotoEditor source={original} maxBytes={1000} onDone={onDone} onCancel={() => {}} />);
    await screen.findByTestId('photo-editor-tools');
    fireEvent.click(screen.getByTestId('photo-editor-tool-stickers'));
    fireEvent.click(screen.getByRole('button', { name: 'Add ⭐' }));
    fireEvent.click(screen.getByRole('button', { name: 'Make it bigger' }));
    fireEvent.click(screen.getByRole('button', { name: 'Turn it' }));
    fireEvent.click(screen.getByTestId('photo-editor-done'));
    await waitFor(() => expect(onDone).toHaveBeenCalledOnce());
    expect(exportMock.mock.calls[0][1].overlays[0]).toMatchObject({ kind: 'emoji', emoji: '⭐', scale: 1.2, rotation: 15 });
  });

  it('says when only the first frame of an animation can be edited, and keeps Send as is', async () => {
    decodeMock.mockResolvedValue(frames(1, true));
    const onAsIs = vi.fn();
    render(<PhotoEditor source={original} maxBytes={1000} onDone={() => {}} onAsIs={onAsIs} onCancel={() => {}} />);
    expect(await screen.findByTestId('photo-editor-notice')).toHaveTextContent('only edit the first frame');
    fireEvent.click(screen.getByTestId('photo-editor-as-is'));
    await waitFor(() => expect(onAsIs).toHaveBeenCalledOnce());
  });

  it('says how many frames an animation edit covers', async () => {
    decodeMock.mockResolvedValue(frames(12));
    render(<PhotoEditor source={original} maxBytes={1000} onDone={() => {}} onCancel={() => {}} />);
    expect(await screen.findByTestId('photo-editor-notice')).toHaveTextContent('all 12 frames');
  });

  it('shows a picture it cannot read, with Send as is still there', async () => {
    decodeMock.mockRejectedValue(new PhotoEditError('This picture cannot be edited on this phone. You can still send it as it is.'));
    render(<PhotoEditor source={original} maxBytes={1000} onDone={() => {}} onAsIs={() => {}} onCancel={() => {}} />);
    expect(await screen.findByTestId('photo-editor-load-error')).toHaveTextContent('cannot be edited');
    expect(screen.getByTestId('photo-editor-done')).toBeDisabled();
    expect(screen.getByTestId('photo-editor-as-is')).toBeEnabled();
  });

  it('keeps the editor open with the reason when finishing fails', async () => {
    decodeMock.mockResolvedValue(frames(1));
    exportMock.mockRejectedValue(new PhotoEditError('The edited picture would be over 25 MB.'));
    const onDone = vi.fn();
    render(<PhotoEditor source={original} maxBytes={1000} onDone={onDone} onCancel={() => {}} />);
    await screen.findByTestId('photo-editor-tools');
    fireEvent.click(screen.getByRole('button', { name: 'Pop filter' }));
    fireEvent.click(screen.getByTestId('photo-editor-done'));
    expect(await screen.findByTestId('photo-editor-error')).toHaveTextContent('over 25 MB');
    expect(onDone).not.toHaveBeenCalled();
  });

  it('labels every control', async () => {
    decodeMock.mockResolvedValue(frames(1));
    render(<PhotoEditor source={original} maxBytes={1000} onDone={() => {}} onCancel={() => {}} />);
    await screen.findByTestId('photo-editor-tools');
    for (const tool of ['filters', 'adjust', 'crop', 'text', 'stickers', 'draw']) {
      fireEvent.click(screen.getByTestId(`photo-editor-tool-${tool}`));
      for (const control of [...document.querySelectorAll('button, input, textarea, [role="button"]')]) {
        const name = control.getAttribute('aria-label') || control.textContent?.trim() || (control.id && document.querySelector(`label[for="${control.id}"]`)?.textContent);
        expect(name, control.outerHTML.slice(0, 120)).toBeTruthy();
      }
    }
  });

  it('closes on Escape', async () => {
    decodeMock.mockResolvedValue(frames(1));
    const onCancel = vi.fn();
    render(<PhotoEditor source={original} maxBytes={1000} onDone={() => {}} onCancel={onCancel} />);
    await screen.findByTestId('photo-editor-tools');
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onCancel).toHaveBeenCalledOnce();
  });
});

describe('usePhotoEditGate', () => {
  it('answers each question: as is, edited, or closed', async () => {
    decodeMock.mockResolvedValue(frames(1));
    const { result, rerender } = renderHook(() => usePhotoEditGate({ maxBytes: 1000 }));
    const show = () => render(<>{result.current.element}</>);

    let answer!: Promise<unknown>;
    act(() => {
      answer = result.current.ask(original);
    });
    rerender();
    expect(result.current.open).toBe(true);
    const view = show();
    fireEvent.click(await screen.findByTestId('photo-editor-as-is'));
    await expect(answer).resolves.toEqual({ kind: 'as-is' });
    view.unmount();
    rerender();
    expect(result.current.open).toBe(false);

    act(() => {
      answer = result.current.ask(original);
    });
    rerender();
    const second = show();
    await screen.findByTestId('photo-editor-tools');
    fireEvent.click(screen.getByTestId('photo-editor-close'));
    await expect(answer).resolves.toBeNull();
    second.unmount();

    act(() => {
      answer = result.current.ask(original);
    });
    rerender();
    const third = show();
    await screen.findByTestId('photo-editor-tools');
    fireEvent.click(screen.getByTestId('photo-editor-done'));
    await expect(answer).resolves.toMatchObject({ kind: 'edited', result: { blob: original, original } });
    third.unmount();
  });
});
