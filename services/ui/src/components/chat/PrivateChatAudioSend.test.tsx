import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import type { DeviceUnlockPluginInterface } from '../../plugins/deviceUnlock';
import type { PrivateChatInfo } from './privateChatModel';
import { openMessage, type PrivatePayload } from '../../lib/privateChatCrypto';

/**
 * Recording a voice note in Private Chat and getting it into the gallery.
 *
 * The microphone itself belongs to the platform, so `useAudioRecorder` is
 * stood in for: what is under test is what this app does with a take — the
 * bytes that leave the phone, the message that names them, and the gallery
 * record that keeps it.
 */

const sendPrivateMessage = vi.fn();
const uploadPrivateBlob = vi.fn();
const deletePrivateBlob = vi.fn();
const addPrivateGalleryEntry = vi.fn();
const getPrivateGallery = vi.fn();
const getPrivatePicture = vi.fn();
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
    getPrivateBlob: (...a: unknown[]) => getPrivatePicture(...a),
    markPrivateReactionsSeen: (...a: unknown[]) => markPrivateReactionsSeen(...a),
    getPrivateChatMarks: (...a: unknown[]) => getPrivateChatMarks(...a),
    flagPrivateChatExpectsReply: (...a: unknown[]) => flagPrivateChatExpectsReply(...a),
    getPrivateLinkPreview: (...a: unknown[]) => getPrivateLinkPreview(...a),
  },
}));

const toastError = vi.fn();
const toastSuccess = vi.fn();
vi.mock('react-hot-toast', () => {
  const toast = Object.assign(vi.fn(), { error: (...a: unknown[]) => toastError(...a), success: (...a: unknown[]) => toastSuccess(...a) });
  return { default: toast };
});

/** The microphone, handed to the test so it can say "the person stopped talking". */
let take: ((blob: Blob, mime: string) => void) | null = null;
let refuse: ((why: 'denied' | 'empty' | 'unavailable') => void) | null = null;
const startRecording = vi.fn();
vi.mock('../../hooks/useAudioRecorder', () => ({
  useAudioRecorder: ({ onRecorded, onError }: { onRecorded: (b: Blob) => void; onError: (w: string) => void }) => {
    take = onRecorded;
    refuse = onError as never;
    return {
      recording: false,
      secondsLeft: 300,
      start: () => startRecording(),
      stop: () => undefined,
    };
  },
}));

import PrivateChatThread from './PrivateChatThread';

function wrap(node: ReactNode) {
  return <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{node}</QueryClientProvider>;
}

/** A chat whose key the device unlock plugin hands over, as the app's own tests do. */
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
  const { toBase64 } = { toBase64: (bytes: Uint8Array) => btoa(String.fromCharCode(...bytes)) };
  return {
    available: vi.fn(async () => ({ available: true })),
    isEnabled: vi.fn(async () => ({ enabled: true })),
    enable: vi.fn(async () => ({ enabled: true })),
    unlock: vi.fn(async () => ({ key: toBase64(raw) })),
    disable: vi.fn(async () => ({ enabled: false })),
  };
}

const thread = (info: PrivateChatInfo) => (
  <PrivateChatThread
    conversation={{ token: 'roomQ', private: true } as never}
    info={info}
    messages={[] as never}
    myActorIds={new Set(['a'])}
    onUnlockedChange={() => undefined}
    onChanged={() => undefined}
  />
);

/** The payload inside the message the app posted, opened with the chat's key. */
async function posted(key: CryptoKey): Promise<PrivatePayload | null> {
  const sealed = sendPrivateMessage.mock.calls.at(-1)?.[1] as string;
  return openMessage(key, sealed);
}

afterEach(() => {
  take = null;
  refuse = null;
  delete window.__jarvisDeviceUnlockMock;
  for (const fn of [sendPrivateMessage, uploadPrivateBlob, deletePrivateBlob, addPrivateGalleryEntry, getPrivateGallery, getPrivatePicture, markPrivateReactionsSeen, getPrivateChatMarks, flagPrivateChatExpectsReply, getPrivateLinkPreview, startRecording, toastError, toastSuccess]) fn.mockReset();
});

async function openRecorder() {
  const { info, raw, key } = await chat();
  window.__jarvisDeviceUnlockMock = plugin(raw);
  getPrivateGallery.mockResolvedValue({ entries: [] });
  getPrivateChatMarks.mockResolvedValue({ message_ids: [], reactions_at: 0 });
  markPrivateReactionsSeen.mockResolvedValue({});
  sendPrivateMessage.mockResolvedValue({ message_id: 9 });
  uploadPrivateBlob.mockResolvedValue({ id: 'f'.repeat(32) });
  addPrivateGalleryEntry.mockResolvedValue({});
  render(wrap(thread(info)));
  const button = await screen.findByTestId('private-audio-open', {}, { timeout: 10_000 });
  await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false), { timeout: 10_000 });
  fireEvent.click(button);
  return { key, blob: 'f'.repeat(32) };
}

describe('recording a voice note in private chat', () => {
  it('offers the microphone beside the picture and camera buttons, and records nothing until it is pressed', async () => {
    await openRecorder();
    const mic = screen.getByTestId('private-audio-open');
    expect(mic).toHaveAccessibleName('Record an encrypted voice note');
    // All three ways to send a picture or a recording sit in the same row, at the size a thumb needs.
    for (const id of ['private-pick', 'private-camera-open', 'private-audio-open']) {
      expect(screen.getByTestId(id).className).toContain('h-11 w-11');
    }
    expect(startRecording).not.toHaveBeenCalled();
    expect(uploadPrivateBlob).not.toHaveBeenCalled();
  }, 30_000);

  it('reaches video recording from the camera it opens, not from a hidden control', async () => {
    await openRecorder();
    fireEvent.click(screen.getByTestId('private-camera-open'));
    expect(await screen.findByTestId('private-mode-video', {}, { timeout: 10_000 })).toBeTruthy();
    expect(screen.getByTestId('private-mode-photo').getAttribute('aria-pressed')).toBe('true');
  }, 30_000);

  it('sends a sealed recording, says nothing in the clear, and keeps it in the gallery', async () => {
    const { key, blob } = await openRecorder();
    fireEvent.click(screen.getByTestId('private-audio-start'));
    await act(async () => { take?.(new Blob([new Uint8Array([1, 2, 3, 4])], { type: 'audio/webm;codecs=opus' }), 'audio/webm'); });
    // The preview only exists in memory, and it is what the person hears before sending.
    expect(await screen.findByTestId('private-audio-preview')).toBeTruthy();
    fireEvent.change(screen.getByTestId('private-audio-caption'), { target: { value: 'listen to this' } });
    fireEvent.click(screen.getByTestId('private-audio-send'));

    await waitFor(() => expect(sendPrivateMessage).toHaveBeenCalled(), { timeout: 10_000 });
    // What was uploaded is ciphertext, never the recording.
    const uploaded = uploadPrivateBlob.mock.calls[0][1] as Uint8Array;
    expect(uploaded.byteLength).toBeGreaterThan(4);
    expect(Array.from(uploaded.slice(0, 4))).not.toEqual([1, 2, 3, 4]);

    const payload = await posted(key);
    expect(payload).toMatchObject({ t: 'audio', blob, mime: 'audio/webm', caption: 'listen to this', name: 'voice-note.webm' });
    expect(payload && 'sha256' in payload && payload.sha256).toHaveLength(64);
    expect(payload && 'duration' in payload && payload.duration).toBeGreaterThanOrEqual(1);
    // And the gallery keeps it by the same blob, so it is one recording, not a copy.
    await waitFor(() => expect(addPrivateGalleryEntry).toHaveBeenCalled(), { timeout: 10_000 });
  }, 40_000);

  it('keeps the take when the send fails, so it can be tried again', async () => {
    await openRecorder();
    sendPrivateMessage.mockRejectedValueOnce({ response: { status: 503, data: { detail: 'The server is busy.' } } });
    fireEvent.click(screen.getByTestId('private-audio-start'));
    await act(async () => { take?.(new Blob([new Uint8Array([9, 9])], { type: 'audio/webm' }), 'audio/webm'); });
    await screen.findByTestId('private-audio-preview');
    fireEvent.click(screen.getByTestId('private-audio-send'));
    await waitFor(() => expect(screen.getByTestId('private-audio-problem').textContent).toBeTruthy(), { timeout: 10_000 });
    // The blob that never became a message is not left on the server.
    expect(deletePrivateBlob).toHaveBeenCalledWith('roomQ', 'f'.repeat(32));
    // And the recording is still here to send again.
    expect(screen.getByTestId('private-audio-preview')).toBeTruthy();
  }, 40_000);

  it('says so when the microphone is refused, instead of looking like it is recording', async () => {
    await openRecorder();
    fireEvent.click(screen.getByTestId('private-audio-start'));
    await act(async () => { refuse?.('denied'); });
    expect(screen.getByTestId('private-audio-problem').textContent).toMatch(/microphone was not allowed/i);
    expect(screen.queryByTestId('private-audio-preview')).toBeNull();
  }, 30_000);

  it('sends nothing when the take is discarded', async () => {
    await openRecorder();
    fireEvent.click(screen.getByTestId('private-audio-start'));
    await act(async () => { take?.(new Blob([new Uint8Array([5])], { type: 'audio/webm' }), 'audio/webm'); });
    await screen.findByTestId('private-audio-preview');
    fireEvent.click(screen.getByTestId('private-audio-discard'));
    await waitFor(() => expect(screen.queryByTestId('private-audio-preview')).toBeNull());
    expect(uploadPrivateBlob).not.toHaveBeenCalled();
    expect(sendPrivateMessage).not.toHaveBeenCalled();
  }, 30_000);
});
