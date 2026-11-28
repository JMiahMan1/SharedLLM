import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import type { DeviceUnlockPluginInterface } from '../../plugins/deviceUnlock';
import type { PrivateChatInfo } from './privateChatModel';

/**
 * Nothing joins the shared Encrypted Gallery unless a parent asked for it.
 *
 * The gallery is shared between the two people in the chat, so it must not
 * become a record of everything either of them sent. A picture picked from the
 * phone, a GIF from Giphy or the keyboard, a paste, a photo taken in the chat, a
 * clip, a voice note: all of them belong to the conversation they were sent in.
 * Only the "Save to gallery" box on the preview - off until somebody ticks it -
 * puts one in the gallery, and anything can still be added later from its bubble.
 *
 * These tests stand in for the camera and the recorder, because the receipt of
 * that choice is what matters here: what the thread does with it.
 */

const sendPrivateMessage = vi.fn();
const uploadPrivateBlob = vi.fn();
const deletePrivateBlob = vi.fn();
const addPrivateGalleryEntry = vi.fn();
const getPrivateGallery = vi.fn();
const markPrivateReactionsSeen = vi.fn();
const getPrivateChatMarks = vi.fn();
const flagPrivateChatExpectsReply = vi.fn();
const getPrivateLinkPreview = vi.fn();
vi.mock('../../services/api', () => ({
  api: {
    sendPrivateMessage: (...a: unknown[]) => sendPrivateMessage(...a),
    uploadPrivateBlob: (...a: unknown[]) => uploadPrivateBlob(...a),
    deletePrivateBlob: (...a: unknown[]) => deletePrivateBlob(...a),
    addPrivateGalleryEntry: (...a: unknown[]) => addPrivateGalleryEntry(...a),
    getPrivateGallery: (...a: unknown[]) => getPrivateGallery(...a),
    getPrivateBlob: vi.fn(),
    markPrivateReactionsSeen: (...a: unknown[]) => markPrivateReactionsSeen(...a),
    getPrivateChatMarks: (...a: unknown[]) => getPrivateChatMarks(...a),
    flagPrivateChatExpectsReply: (...a: unknown[]) => flagPrivateChatExpectsReply(...a),
    getPrivateLinkPreview: (...a: unknown[]) => getPrivateLinkPreview(...a),
  },
}));

const toastError = vi.fn();
vi.mock('react-hot-toast', () => {
  const toast = Object.assign(vi.fn(), { error: (...a: unknown[]) => toastError(...a), success: vi.fn() });
  return { default: toast };
});

/**
 * The camera as the thread sees it: one button with the box left alone, one with
 * it ticked. `saveToGallery` is the fourth argument, after the edit a picture
 * might carry.
 */
vi.mock('./PrivateCamera', async () => {
  const { createElement } = await import('react');
  return {
    default: ({ onCapture, onVideo }: {
      onCapture: (jpeg: Uint8Array, takenAt: number, edit?: unknown, saveToGallery?: boolean) => Promise<void>;
      onVideo?: () => void;
    }) => createElement('div', {}, [
      createElement('button', { key: 'snap', 'data-testid': 'fake-shutter', onClick: () => void onCapture(new Uint8Array([1, 2, 3, 4]), 1_760_000_000) }, 'snap'),
      createElement('button', { key: 'snap-kept', 'data-testid': 'fake-shutter-kept', onClick: () => void onCapture(new Uint8Array([1, 2, 3, 4]), 1_760_000_000, undefined, true) }, 'snap, kept'),
      createElement('button', { key: 'video', 'data-testid': 'fake-video-mode', onClick: () => onVideo?.() }, 'video'),
    ]),
  };
});

/** The recorder as the thread sees it: one clip left alone, one kept. */
vi.mock('./PrivateVideoRecorder', async () => {
  const { createElement } = await import('react');
  const clip: { blob: Blob; mime: string; takenAt: number; duration: number } =
    { blob: new Blob([new Uint8Array(64)]), mime: 'video/mp4', takenAt: 1_760_000_000, duration: 4 };
  return {
    default: ({ onSend }: {
      onSend: (clip: { blob: Blob; mime: string; takenAt: number; duration: number }, progress: (done: number, count: number) => void, saveToGallery: boolean) => Promise<void>;
    }) => createElement('div', {}, [
      createElement('button', { key: 'clip', 'data-testid': 'fake-record-clip', onClick: () => void onSend(clip, () => undefined, false) }, 'record'),
      createElement('button', { key: 'clip-kept', 'data-testid': 'fake-record-clip-kept', onClick: () => void onSend(clip, () => undefined, true) }, 'record, kept'),
    ]),
  };
});

/** The platform reads a clip's length, frame size and poster frame; here they are known. */
vi.mock('../../lib/privateVideoMedia', async () => {
  const actual = await vi.importActual<Record<string, unknown>>('../../lib/privateVideoMedia');
  return { ...actual, readClip: async () => ({ poster: new Uint8Array([9, 9, 9]), duration: 4, width: 640, height: 360 }) };
});

/**
 * The editor gate a picked photo goes through, answered "send as is" in one tap.
 * A GIF picked from the phone must travel the same path as any other picture.
 */
vi.mock('../photoEditor/LazyPhotoEditor', async () => {
  const { createElement } = await import('react');
  return {
    default: ({ onAsIs, onCancel }: { onAsIs?: () => void; onCancel: () => void }) =>
      createElement('div', {}, [
        createElement('button', { key: 'as-is', 'data-testid': 'fake-as-is', onClick: () => (onAsIs ? onAsIs() : onCancel()) }, 'send as is'),
      ]),
  };
});

import PrivateChatThread from './PrivateChatThread';
import { openMessage } from '../../lib/privateChatCrypto';

function wrap(node: ReactNode) {
  return <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{node}</QueryClientProvider>;
}

async function chat(): Promise<{ info: PrivateChatInfo; raw: Uint8Array<ArrayBuffer>; key: CryptoKey }> {
  const { deriveKey, makeVerifier, PBKDF2_ITERATIONS, randomBytes, toBase64 } = await import('../../lib/privateChatCrypto');
  const kdf = { salt: toBase64(randomBytes(16)), iterations: PBKDF2_ITERATIONS };
  const key = await deriveKey('pw pw pw pw', kdf);
  const base = await crypto.subtle.importKey('raw', new TextEncoder().encode('pw pw pw pw'), 'PBKDF2', false, ['deriveBits']);
  const raw = new Uint8Array(await crypto.subtle.deriveBits({ name: 'PBKDF2', hash: 'SHA-256', salt: Uint8Array.from(atob(kdf.salt), (c) => c.charCodeAt(0)), iterations: kdf.iterations }, base, 256));
  return {
    info: { token: 'roomQ', members: ['a', 'b'], partner: 'b', partner_name: 'Robin', created_by: 'a', created_at: 1, version: 1,
      kdf: { name: 'PBKDF2-SHA256', ...kdf }, verifier: await makeVerifier(key), storage: 'store' },
    raw,
    key,
  };
}

function plugin(raw: Uint8Array<ArrayBuffer>): DeviceUnlockPluginInterface {
  const toBase64 = (bytes: Uint8Array) => btoa(String.fromCharCode(...bytes));
  return {
    available: vi.fn(async () => ({ available: true })),
    isEnabled: vi.fn(async () => ({ enabled: true })),
    enable: vi.fn(async () => ({ enabled: true })),
    unlock: vi.fn(async () => ({ key: toBase64(raw) })),
    disable: vi.fn(async () => ({ enabled: false })),
  };
}

/** The gallery record the app wrote, opened with the chat key. */
async function galleryRecord(key: CryptoKey): Promise<{ op?: string; blobs?: string[] } | null> {
  const sealed = addPrivateGalleryEntry.mock.calls.at(-1)?.[1] as string;
  return sealed ? (await openMessage(key, sealed)) as never : null;
}

/** The payload inside the message the app posted, opened with the chat's key. */
async function posted(key: CryptoKey) {
  const sealed = sendPrivateMessage.mock.calls.at(-1)?.[1] as string;
  return openMessage(key, sealed);
}

afterEach(() => {
  delete window.__jarvisDeviceUnlockMock;
  for (const fn of [sendPrivateMessage, uploadPrivateBlob, deletePrivateBlob, addPrivateGalleryEntry, getPrivateGallery, markPrivateReactionsSeen, getPrivateChatMarks, flagPrivateChatExpectsReply, getPrivateLinkPreview, toastError]) fn.mockReset();
});

async function open() {
  const { info, raw, key } = await chat();
  window.__jarvisDeviceUnlockMock = plugin(raw);
  getPrivateGallery.mockResolvedValue({ entries: [] });
  getPrivateChatMarks.mockResolvedValue({ message_ids: [], reactions_at: 0 });
  markPrivateReactionsSeen.mockResolvedValue({});
  sendPrivateMessage.mockResolvedValue({ message_id: 9 });
  addPrivateGalleryEntry.mockResolvedValue({});
  uploadPrivateBlob.mockImplementation(async () => ({ id: `${String(uploadPrivateBlob.mock.calls.length)}`.padStart(32, '0') }));
  render(wrap(
    <PrivateChatThread
      conversation={{ token: 'roomQ', private: true } as never}
      info={info}
      messages={[] as never}
      myActorIds={new Set(['a'])}
      onUnlockedChange={() => undefined}
      onChanged={() => undefined}
    />,
  ));
  const camera = await screen.findByTestId('private-camera-open', {}, { timeout: 10_000 });
  await waitFor(() => expect((camera as HTMLButtonElement).disabled).toBe(false), { timeout: 10_000 });
  return { key };
}

describe('the gallery keeps only what a parent asked it to keep', () => {
  it('sends a photo taken in the chat without putting it in the gallery', async () => {
    const { key } = await open();
    fireEvent.click(screen.getByTestId('private-camera-open'));
    fireEvent.click(await screen.findByTestId('fake-shutter'));

    await waitFor(() => expect(sendPrivateMessage).toHaveBeenCalled(), { timeout: 10_000 });
    expect(await posted(key)).toMatchObject({ t: 'image', mime: 'image/jpeg', blob: '0'.repeat(31) + '1' });
    // The box was left alone, so the picture stays in the conversation.
    expect(addPrivateGalleryEntry).not.toHaveBeenCalled();
  }, 40_000);

  it('keeps a photo taken in the chat when the box was ticked', async () => {
    const { key } = await open();
    fireEvent.click(screen.getByTestId('private-camera-open'));
    fireEvent.click(await screen.findByTestId('fake-shutter-kept'));

    await waitFor(() => expect(addPrivateGalleryEntry).toHaveBeenCalled(), { timeout: 10_000 });
    expect(await galleryRecord(key)).toMatchObject({ op: 'add', blobs: ['0'.repeat(31) + '1'] });
  }, 40_000);

  it('keeps a clip by its poster, which is what its tile and its comments name', async () => {
    const { key } = await open();
    fireEvent.click(screen.getByTestId('private-camera-open'));
    fireEvent.click(screen.getByTestId('fake-video-mode'));
    fireEvent.click(await screen.findByTestId('fake-record-clip-kept'));

    await waitFor(() => expect(sendPrivateMessage).toHaveBeenCalled(), { timeout: 10_000 });
    const payload = await posted(key);
    expect(payload).toMatchObject({ t: 'video', mime: 'video/mp4', duration: 4 });
    const poster = payload && 'poster' in payload ? payload.poster : '';
    expect(poster).toBeTruthy();
    await waitFor(() => expect(addPrivateGalleryEntry).toHaveBeenCalled(), { timeout: 10_000 });
    // The poster, not a chunk: a clip's place in the gallery is its still.
    expect(await galleryRecord(key)).toMatchObject({ op: 'add', blobs: [poster] });
  }, 40_000);

  it('sends a clip without keeping it unless the box was ticked', async () => {
    await open();
    fireEvent.click(screen.getByTestId('private-camera-open'));
    fireEvent.click(screen.getByTestId('fake-video-mode'));
    fireEvent.click(await screen.findByTestId('fake-record-clip'));

    await waitFor(() => expect(sendPrivateMessage).toHaveBeenCalled(), { timeout: 10_000 });
    expect(addPrivateGalleryEntry).not.toHaveBeenCalled();
  }, 40_000);

  it('sends a GIF picked from the phone without keeping it', async () => {
    await open();
    // A GIF from the keyboard or Giphy arrives as a picked file, and the editor
    // gate answers "send as is" - the same path an ordinary photo takes.
    const gif = new File([new Uint8Array([71, 73, 70, 56])], 'party.gif', { type: 'image/gif' });
    fireEvent.change(screen.getByTestId('private-file-input'), { target: { files: [gif] } });
    fireEvent.click(await screen.findByTestId('fake-as-is', {}, { timeout: 10_000 }));

    await waitFor(() => expect(sendPrivateMessage).toHaveBeenCalled(), { timeout: 10_000 });
    expect(addPrivateGalleryEntry).not.toHaveBeenCalled();
  }, 40_000);

  it('says the message was sent even when the gallery record it asked for fails', async () => {
    await open();
    // A failure with nothing to say of its own, so the app's own words are what is shown.
    addPrivateGalleryEntry.mockRejectedValue({});
    fireEvent.click(screen.getByTestId('private-camera-open'));
    fireEvent.click(await screen.findByTestId('fake-shutter-kept'));

    await waitFor(() => expect(sendPrivateMessage).toHaveBeenCalled(), { timeout: 10_000 });
    // The picture is sent; only the gallery copy failed, and it is said plainly.
    await waitFor(() => expect(toastError).toHaveBeenCalledWith(expect.stringMatching(/gallery/i)), { timeout: 10_000 });
    expect(deletePrivateBlob).not.toHaveBeenCalled();
  }, 40_000);
});
