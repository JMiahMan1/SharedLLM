import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import toast from 'react-hot-toast';
import { handleKeyboardMedia, keyboardFile, pasteFile, resetKeyboardMediaForTests, startKeyboardMedia, type KeyboardMediaEvent } from './keyboardMedia';

const GIF = 'GIF89a\x01\x00\x01\x00\x00\x00\x00;';
const gifEvent = (extra: Partial<KeyboardMediaEvent> = {}): KeyboardMediaEvent => ({ mime: 'image/gif', name: 'keyboard.gif', data: btoa(GIF), ...extra });

// jsdom has neither a DataTransfer nor a ClipboardEvent that carries one; the browser specs use the real ones.
class FakeDataTransfer {
  files: File[] = [];
  items = { add: (file: File) => this.files.push(file) };
}
class FakeClipboardEvent extends Event {
  clipboardData: FakeDataTransfer;
  constructor(type: string, init: EventInit & { clipboardData: FakeDataTransfer }) {
    super(type, init);
    this.clipboardData = init.clipboardData;
  }
}

beforeEach(() => {
  vi.stubGlobal('DataTransfer', FakeDataTransfer);
  vi.stubGlobal('ClipboardEvent', FakeClipboardEvent);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  document.body.innerHTML = '';
  resetKeyboardMediaForTests();
});

function box(takesFiles: boolean): { el: HTMLTextAreaElement; got: File[] } {
  const el = document.createElement('textarea');
  const got: File[] = [];
  el.addEventListener('paste', (event) => {
    const files = (event as unknown as FakeClipboardEvent).clipboardData.files;
    if (takesFiles && files.length) {
      event.preventDefault();
      got.push(...files);
    }
  });
  document.body.appendChild(el);
  return { el, got };
}

describe('keyboardFile', () => {
  it('turns the keyboard GIF into a GIF file, bytes intact', async () => {
    const file = keyboardFile(gifEvent())!;
    expect(file.type).toBe('image/gif');
    expect(file.name).toBe('keyboard.gif');
    const bytes = new Uint8Array(await file.arrayBuffer());
    expect(String.fromCharCode(...bytes)).toBe(GIF);
  });

  it('refuses types that are not pictures, and broken data', () => {
    expect(keyboardFile(gifEvent({ mime: 'text/html' }))).toBeNull();
    expect(keyboardFile(gifEvent({ mime: 'image/svg+xml' }))).toBeNull();
    expect(keyboardFile(gifEvent({ data: '' }))).toBeNull();
    expect(keyboardFile(gifEvent({ data: '%%%not base64' }))).toBeNull();
  });

  it('names a file the keyboard did not name', () => {
    expect(keyboardFile(gifEvent({ name: undefined, mime: 'image/webp' }))!.name).toBe('keyboard.webp');
  });
});

describe('handleKeyboardMedia', () => {
  it('pastes the GIF into the focused chat box', () => {
    const { el, got } = box(true);
    expect(handleKeyboardMedia(gifEvent(), el)).toBe(true);
    expect(got).toHaveLength(1);
    expect(got[0].type).toBe('image/gif');
  });

  it('says so when the focused box does not take pictures', () => {
    const error = vi.spyOn(toast, 'error');
    const { el, got } = box(false);
    expect(handleKeyboardMedia(gifEvent(), el)).toBe(false);
    expect(got).toHaveLength(0);
    expect(error).toHaveBeenCalledWith('GIFs and stickers can only be sent in a chat message.', expect.anything());
  });

  it('says so when nothing is focused, or the file was too large or unreadable', () => {
    const error = vi.spyOn(toast, 'error');
    expect(handleKeyboardMedia(gifEvent(), null)).toBe(false);
    expect(handleKeyboardMedia({ error: 'too_large' }, null)).toBe(false);
    expect(handleKeyboardMedia({ mime: 'video/mp4', data: 'AAAA' }, null)).toBe(false);
    expect(error.mock.calls.map((c) => c[0])).toEqual([
      'GIFs and stickers can only be sent in a chat message.',
      'That GIF is too large to send.',
      'The keyboard sent a picture Jarvis could not read.',
    ]);
  });

  it('never pastes into a box that has left the page', () => {
    const { el } = box(true);
    el.remove();
    expect(pasteFile(keyboardFile(gifEvent())!, el)).toBe(false);
  });
});

describe('startKeyboardMedia', () => {
  it('listens once, and the plugin event reaches the focused box', async () => {
    let listener: ((event: KeyboardMediaEvent) => void) | null = null;
    const plugin = {
      available: vi.fn(async () => ({ available: true })),
      addListener: vi.fn(async (_name: 'media', fn: (event: KeyboardMediaEvent) => void) => {
        listener = fn;
        return { remove: async () => {} };
      }),
    };
    await startKeyboardMedia(plugin);
    await startKeyboardMedia(plugin);
    expect(plugin.addListener).toHaveBeenCalledTimes(1);
    expect(plugin.addListener).toHaveBeenCalledWith('media', expect.any(Function));

    const { el, got } = box(true);
    el.focus();
    listener!(gifEvent());
    expect(got).toHaveLength(1);
  });

  it('an APK without the plugin is left as it was', async () => {
    const plugin = { available: vi.fn(), addListener: vi.fn(async () => { throw new Error('not implemented'); }) };
    await expect(startKeyboardMedia(plugin)).resolves.toBeNull();
  });

  it('does nothing in a browser', async () => {
    await expect(startKeyboardMedia()).resolves.toBeNull();
  });
});
