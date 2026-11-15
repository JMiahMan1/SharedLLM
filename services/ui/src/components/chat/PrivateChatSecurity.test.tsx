import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import PrivateChatSecurity from './PrivateChatSecurity';
import PrivateChatThread from './PrivateChatThread';
import type { DeviceUnlockPluginInterface } from '../../plugins/deviceUnlock';
import type { PrivateChatInfo } from './privateChatModel';
import { deriveKey, makeVerifier, PBKDF2_ITERATIONS, randomBytes, toBase64 } from '../../lib/privateChatCrypto';

const getPrivateChat = vi.fn();
vi.mock('../../services/api', () => ({ api: { getPrivateChat: (...a: unknown[]) => getPrivateChat(...a) } }));

function wrap(node: ReactNode) {
  return <QueryClientProvider client={new QueryClient()}>{node}</QueryClientProvider>;
}

async function chat(password: string): Promise<{ info: PrivateChatInfo; raw: Uint8Array<ArrayBuffer> }> {
  const kdf = { salt: toBase64(randomBytes(16)), iterations: PBKDF2_ITERATIONS };
  const key = await deriveKey(password, kdf);
  const base = await crypto.subtle.importKey('raw', new TextEncoder().encode(password), 'PBKDF2', false, ['deriveBits']);
  const raw = new Uint8Array(await crypto.subtle.deriveBits({ name: 'PBKDF2', hash: 'SHA-256', salt: Uint8Array.from(atob(kdf.salt), (c) => c.charCodeAt(0)), iterations: kdf.iterations }, base, 256));
  return {
    info: { token: 'roomP', members: ['a', 'b'], partner: 'b', partner_name: 'Robin', created_by: 'a', created_at: 1, version: 1,
      kdf: { name: 'PBKDF2-SHA256', ...kdf }, verifier: await makeVerifier(key) },
    raw,
  };
}

function plugin(over: Partial<DeviceUnlockPluginInterface> = {}): DeviceUnlockPluginInterface {
  return {
    available: vi.fn(async () => ({ available: true })),
    isEnabled: vi.fn(async () => ({ enabled: true })),
    enable: vi.fn(async () => ({ enabled: true })),
    unlock: vi.fn(async () => ({ key: '' })),
    disable: vi.fn(async () => ({ enabled: false })),
    ...over,
  };
}

const thread = (info: PrivateChatInfo) => (
  <PrivateChatThread
    conversation={{ token: 'roomP', private: true } as never}
    info={info}
    messages={[]}
    myActorIds={new Set(['a'])}
    onUnlockedChange={() => undefined}
    onChanged={() => undefined}
  />
);

afterEach(() => {
  delete window.__jarvisDeviceUnlockMock;
  getPrivateChat.mockReset();
});

describe('private chat: password and phone lock', () => {
  it('in a browser: Change password is there, the phone lock never is', async () => {
    render(wrap(<PrivateChatSecurity token="roomP" partnerName="Robin" onChanged={() => undefined} onClose={() => undefined} />));
    expect(screen.getByTestId('private-change-password-open')).toBeTruthy();
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId('private-phone-lock-toggle')).toBeNull();
  });

  it('on a phone with the plugin: the toggle is offered, off by default', async () => {
    window.__jarvisDeviceUnlockMock = plugin({ isEnabled: vi.fn(async () => ({ enabled: false })) });
    render(wrap(<PrivateChatSecurity token="roomP" partnerName="Robin" onChanged={() => undefined} onClose={() => undefined} />));
    const toggle = await screen.findByTestId('private-phone-lock-toggle');
    expect(toggle.getAttribute('aria-checked')).toBe('false');
    expect(screen.getByText(/does not affect it/)).toBeTruthy();
  });

  it('a browser lock screen asks for the password only', async () => {
    const { info } = await chat('pw pw pw pw');
    render(wrap(thread(info)));
    expect(await screen.findByTestId('private-password')).toBeTruthy();
    expect(screen.queryByTestId('private-phone-unlock')).toBeNull();
  }, 30_000);

  it('a phone lock that fails falls back to the password, with its reason', async () => {
    const { info } = await chat('pw pw pw pw');
    window.__jarvisDeviceUnlockMock = plugin({ unlock: vi.fn(async () => { throw new Error('Set it up again with the password.'); }) });
    render(wrap(thread(info)));
    expect(await screen.findByTestId('private-password')).toBeTruthy();
    expect(screen.getByTestId('private-wrong').textContent).toMatch(/again with the password/);
  }, 30_000);

  it('the phone lock opens the chat on its own once, offering the password instead', async () => {
    const { info, raw } = await chat('pw pw pw pw');
    const unlock = vi.fn(async () => ({ key: toBase64(raw) }));
    window.__jarvisDeviceUnlockMock = plugin({ unlock });
    render(wrap(thread(info)));
    await waitFor(() => expect(screen.getByTestId('private-chat')).toBeTruthy(), { timeout: 10_000 });
    expect(unlock).toHaveBeenCalledTimes(1);
  }, 30_000);
});
