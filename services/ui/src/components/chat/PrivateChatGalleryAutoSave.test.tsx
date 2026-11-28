import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import type { DeviceUnlockPluginInterface } from '../../plugins/deviceUnlock';
import type { PrivateChatInfo } from './privateChatModel';

/**
 * Anything a parent sends from Private Chat lands in the Encrypted Gallery too.
 *
 * Before this, taking a photo in the chat and then opening the gallery showed
 * nothing: the picture existed as a message and nowhere else, and the person
 * had to add it again by hand. A clip is in the gallery by its poster, a
 * picture and a recording by their own blob.
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

/** The camera, standing in for a handset: it takes one picture when asked. */
vi.mock('./PrivateCamera', async () => {
  const { createElement } = await import('react');
  return {
    default: ({ onCapture, onVideo }: { onCapture: (jpeg: Uint8Array, takenAt: number) => Promise<void>; onVideo?: () => void }) =>
      createElement('div', {}, [
        createElement('button', { key: 'snap', 'data-testid': 'fake-shutter', onClick: () => void onCapture(new Uint8Array([1, 2, 3, 4]), 1_760_000_000) }, 'snap'),
        createElement('button', { key: 'video', 'data-testid': 'fake-video-mode', onClick: () => onVideo?.() }, 'video'),
      ]),
  };
});

/** The recorder, standing in for a handset: it hands over one finished clip. */
vi.mock('./PrivateVideoRecorder', async () => {
  const { createElement } = await import('react');
  return {
    default: ({ onSend }: { onSend: (clip: { blob: Blob; mime: string; takenAt: number; duration: number }) => Promise<void> }) =>
      createElement('button', {
        'data-testid': 'fake-record-clip',
        onClick: () => void onSend({ blob: new Blob([new Uint8Array(64)]), mime: 'video/mp4', takenAt: 1_760_000_000, duration: 4 }),
      }, 'record'),
  };
});

/** The platform reads a clip's length, frame size and poster frame; here they are known. */
vi.mock('../../lib/privateVideoMedia', async () => {
  const actual = await vi.importActual<Record<string, unknown>>('../../lib/privateVideoMedia');
  return { ...actual, readClip: async () => ({ poster: new Uint8Array([9, 9, 9]), duration: 4, width: 640, height: 360 }) };
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

describe('what is sent from private chat is in the gallery', () => {
  it('keeps a photo taken in the chat by its own blob', async () => {
    const { key } = await open();
    fireEvent.click(screen.getByTestId('private-camera-open'));
    fireEvent.click(await screen.findByTestId('fake-shutter'));

    await waitFor(() => expect(sendPrivateMessage).toHaveBeenCalled(), { timeout: 10_000 });
    const payload = await openMessage(key, sendPrivateMessage.mock.calls.at(-1)?.[1] as string);
    expect(payload).toMatchObject({ t: 'image', mime: 'image/jpeg', blob: '0'.repeat(31) + '1' });
    await waitFor(() => expect(addPrivateGalleryEntry).toHaveBeenCalled(), { timeout: 10_000 });
    expect(await galleryRecord(key)).toMatchObject({ op: 'add', blobs: ['0'.repeat(31) + '1'] });
  }, 40_000);

  it('keeps a clip by its poster, which is what its tile and its comments name', async () => {
    const { key } = await open();
    fireEvent.click(screen.getByTestId('private-camera-open'));
    fireEvent.click(screen.getByTestId('fake-video-mode'));
    fireEvent.click(await screen.findByTestId('fake-record-clip'));

    await waitFor(() => expect(sendPrivateMessage).toHaveBeenCalled(), { timeout: 10_000 });
    const payload = await openMessage(key, sendPrivateMessage.mock.calls.at(-1)?.[1] as string);
    expect(payload).toMatchObject({ t: 'video', mime: 'video/mp4', duration: 4 });
    const poster = payload && 'poster' in payload ? payload.poster : '';
    expect(poster).toBeTruthy();
    await waitFor(() => expect(addPrivateGalleryEntry).toHaveBeenCalled(), { timeout: 10_000 });
    // The poster, not a chunk: a clip's place in the gallery is its still.
    expect(await galleryRecord(key)).toMatchObject({ op: 'add', blobs: [poster] });
  }, 40_000);

  it('says the message was sent even when the gallery record fails', async () => {
    await open();
    // A failure with nothing to say of its own, so the app's own words are what is shown.
    addPrivateGalleryEntry.mockRejectedValue({});
    fireEvent.click(screen.getByTestId('private-camera-open'));
    fireEvent.click(await screen.findByTestId('fake-shutter'));
    await waitFor(() => expect(sendPrivateMessage).toHaveBeenCalled(), { timeout: 10_000 });
    // The picture is sent; only the gallery copy failed, and it is said plainly.
    await waitFor(() => expect(toastError).toHaveBeenCalledWith(expect.stringMatching(/gallery/i)), { timeout: 10_000 });
    expect(deletePrivateBlob).not.toHaveBeenCalled();
  }, 40_000);
});
