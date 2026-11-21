import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import PrivateChatThread from './PrivateChatThread';
import type { DeviceUnlockPluginInterface } from '../../plugins/deviceUnlock';
import type { PrivateChatInfo } from './privateChatModel';
import { deriveKey, makeVerifier, PBKDF2_ITERATIONS, randomBytes, sealMessage, toBase64 } from '../../lib/privateChatCrypto';

const flagPrivateChatExpectsReply = vi.fn();
const getPrivateLinkPreview = vi.fn();
const sendPrivateMessage = vi.fn();
vi.mock('../../services/api', () => ({
  api: {
    flagPrivateChatExpectsReply: (...a: unknown[]) => flagPrivateChatExpectsReply(...a),
    getPrivateLinkPreview: (...a: unknown[]) => getPrivateLinkPreview(...a),
    sendPrivateMessage: (...a: unknown[]) => sendPrivateMessage(...a),
  },
}));

const toastError = vi.fn();
vi.mock('react-hot-toast', () => {
  const toast = Object.assign(vi.fn(), { error: (...a: unknown[]) => toastError(...a), success: vi.fn() });
  return { default: toast };
});

function wrap(node: ReactNode) {
  return <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{node}</QueryClientProvider>;
}

async function chat(password: string): Promise<{ info: PrivateChatInfo; raw: Uint8Array<ArrayBuffer>; key: CryptoKey }> {
  const kdf = { salt: toBase64(randomBytes(16)), iterations: PBKDF2_ITERATIONS };
  const key = await deriveKey(password, kdf);
  const base = await crypto.subtle.importKey('raw', new TextEncoder().encode(password), 'PBKDF2', false, ['deriveBits']);
  const raw = new Uint8Array(await crypto.subtle.deriveBits({ name: 'PBKDF2', hash: 'SHA-256', salt: Uint8Array.from(atob(kdf.salt), (c) => c.charCodeAt(0)), iterations: kdf.iterations }, base, 256));
  return {
    info: { token: 'roomQ', members: ['a', 'b'], partner: 'b', partner_name: 'Robin', created_by: 'a', created_at: 1, version: 1,
      kdf: { name: 'PBKDF2-SHA256', ...kdf }, verifier: await makeVerifier(key), storage: 'store' },
    raw,
    key,
  };
}

function plugin(raw: Uint8Array<ArrayBuffer>): DeviceUnlockPluginInterface {
  return {
    available: vi.fn(async () => ({ available: true })),
    isEnabled: vi.fn(async () => ({ enabled: true })),
    enable: vi.fn(async () => ({ enabled: true })),
    unlock: vi.fn(async () => ({ key: toBase64(raw) })),
    disable: vi.fn(async () => ({ enabled: false })),
  };
}

const thread = (info: PrivateChatInfo, messages: unknown[], onUnlockedChange: (token: string, unlocked: boolean) => void = () => undefined) => (
  <PrivateChatThread
    conversation={{ token: 'roomQ', private: true } as never}
    info={info}
    messages={messages as never}
    myActorIds={new Set(['a'])}
    onUnlockedChange={onUnlockedChange}
    onChanged={() => undefined}
  />
);

afterEach(() => {
  delete window.__jarvisDeviceUnlockMock;
  flagPrivateChatExpectsReply.mockReset();
  getPrivateLinkPreview.mockReset();
  sendPrivateMessage.mockReset();
  toastError.mockReset();
});

describe('private chat thread', () => {
  it('tells the panel which chat is unlocked, so a switched chat never inherits another one\'s unlock', async () => {
    const { info, raw } = await chat('pw pw pw pw');
    window.__jarvisDeviceUnlockMock = plugin(raw);
    const onUnlockedChange = vi.fn();
    const view = render(wrap(thread(info, [], onUnlockedChange)));
    await waitFor(() => expect(onUnlockedChange).toHaveBeenCalledWith('roomQ', true), { timeout: 10_000 });
    view.unmount();
    expect(onUnlockedChange).toHaveBeenLastCalledWith('roomQ', false);
  }, 30_000);

  it('a reply hint that fails is said and sent again; one the server took is not sent twice', async () => {
    const { info, raw, key } = await chat('pw pw pw pw');
    window.__jarvisDeviceUnlockMock = plugin(raw);
    flagPrivateChatExpectsReply
      .mockRejectedValueOnce({ response: { status: 503, data: { detail: 'The server is busy.' } } })
      .mockResolvedValue({ message_id: 2, expects_reply: true });
    const message = async (id: number, actor: string, text: string) => ({
      id, actor_id: actor, actor_type: 'users', actor_display_name: actor, timestamp: 1000 + id, message_type: 'comment',
      message: await sealMessage(key, { t: 'text', text }),
    });
    const messages = [await message(1, 'a', 'Home at 6'), await message(2, 'b', 'Are you bringing bread')];
    const view = render(wrap(thread(info, messages)));
    await waitFor(() => expect(toastError).toHaveBeenCalledTimes(1), { timeout: 10_000 });
    expect(String(toastError.mock.calls[0][0])).toContain('The server is busy.');

    // The thread moves (a refetch): the hint goes again, and this time it is taken.
    view.rerender(wrap(thread(info, [...messages])));
    await waitFor(() => expect(flagPrivateChatExpectsReply).toHaveBeenCalledTimes(2));
    await new Promise((resolve) => setTimeout(resolve, 20));
    view.rerender(wrap(thread(info, [...messages])));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(flagPrivateChatExpectsReply).toHaveBeenCalledTimes(2);
  }, 30_000);

  it('a link preview that cannot be made is said, not silently dropped', async () => {
    const { info, raw } = await chat('pw pw pw pw');
    window.__jarvisDeviceUnlockMock = plugin(raw);
    getPrivateLinkPreview.mockRejectedValue({ response: { status: 404, data: { detail: 'That page has nothing to preview.' } } });
    render(wrap(thread(info, [])));
    const box = await screen.findByPlaceholderText('Private message', undefined, { timeout: 10_000 });
    fireEvent.change(box, { target: { value: 'look https://example.com/page' } });
    await waitFor(() => expect(getPrivateLinkPreview).toHaveBeenCalled(), { timeout: 5000 });
    await waitFor(() => expect(toastError).toHaveBeenCalledWith(
      'No preview for this link: That page has nothing to preview.',
      { id: 'link-preview-https://example.com/page' },
    ));
  }, 30_000);

  it('a message sent again without its preview after a 413 says so', async () => {
    const { info, raw } = await chat('pw pw pw pw');
    window.__jarvisDeviceUnlockMock = plugin(raw);
    getPrivateLinkPreview.mockResolvedValue({ url: 'https://example.com/page', title: 'A page' });
    sendPrivateMessage
      .mockRejectedValueOnce({ response: { status: 413, data: { detail: 'Too large.' } } })
      .mockResolvedValue({ status: 'SUCCESS' });
    render(wrap(thread(info, [])));
    const box = await screen.findByPlaceholderText('Private message', undefined, { timeout: 10_000 });
    fireEvent.change(box, { target: { value: 'look https://example.com/page' } });
    await screen.findByTestId('private-link-draft-card', undefined, { timeout: 5000 });
    fireEvent.click(screen.getByTestId('private-send'));
    await waitFor(() => expect(sendPrivateMessage).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(toastError).toHaveBeenCalledWith('Sent without the link preview: the server refused a message that size.'));
  }, 30_000);
});
