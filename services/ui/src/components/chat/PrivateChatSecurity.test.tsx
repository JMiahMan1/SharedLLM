import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import PrivateChatSecurity from './PrivateChatSecurity';
import PrivateChatThread from './PrivateChatThread';
import type { DeviceUnlockPluginInterface } from '../../plugins/deviceUnlock';
import type { PrivateChatInfo } from './privateChatModel';
import { deriveKey, makeVerifier, PBKDF2_ITERATIONS, randomBytes, sealMessage, toBase64 } from '../../lib/privateChatCrypto';
import type { PrivateChatReminders } from './privateChatModel';

const getPrivateChat = vi.fn();
/** What an older server without the reminders route answers. */
const notFound = () => Object.assign(new Error('Request failed with status code 404'), { response: { status: 404, data: { detail: 'Not Found' } } });
const getPrivateChatReminders = vi.fn(async (): Promise<PrivateChatReminders> => {
  throw notFound();
});
const setPrivateChatReminders = vi.fn();
const flagPrivateChatExpectsReply = vi.fn(async () => ({}));
vi.mock('../../services/api', () => ({
  api: {
    getPrivateChat: (...a: unknown[]) => getPrivateChat(...a),
    getPrivateChatReminders: (...a: unknown[]) => getPrivateChatReminders(...(a as [])),
    setPrivateChatReminders: (...a: unknown[]) => setPrivateChatReminders(...a),
    flagPrivateChatExpectsReply: (...a: unknown[]) => flagPrivateChatExpectsReply(...(a as [])),
  },
}));

function wrap(node: ReactNode) {
  return <QueryClientProvider client={new QueryClient()}>{node}</QueryClientProvider>;
}

async function chat(password: string): Promise<{ info: PrivateChatInfo; raw: Uint8Array<ArrayBuffer>; key: CryptoKey }> {
  const kdf = { salt: toBase64(randomBytes(16)), iterations: PBKDF2_ITERATIONS };
  const key = await deriveKey(password, kdf);
  const base = await crypto.subtle.importKey('raw', new TextEncoder().encode(password), 'PBKDF2', false, ['deriveBits']);
  const raw = new Uint8Array(await crypto.subtle.deriveBits({ name: 'PBKDF2', hash: 'SHA-256', salt: Uint8Array.from(atob(kdf.salt), (c) => c.charCodeAt(0)), iterations: kdf.iterations }, base, 256));
  return {
    info: { token: 'roomP', members: ['a', 'b'], partner: 'b', partner_name: 'Robin', created_by: 'a', created_at: 1, version: 1,
      kdf: { name: 'PBKDF2-SHA256', ...kdf }, verifier: await makeVerifier(key) },
    raw,
    key,
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
  delete window.__jarvisPrivatePreviewMock;
  getPrivateChat.mockReset();
  getPrivateChatReminders.mockReset();
  getPrivateChatReminders.mockImplementation(async (): Promise<PrivateChatReminders> => {
    throw notFound();
  });
  setPrivateChatReminders.mockReset();
  flagPrivateChatExpectsReply.mockClear();
});

describe('private chat: password and phone lock', () => {
  it('in a browser: Change password is there, the phone lock never is', async () => {
    render(wrap(<PrivateChatSecurity token="roomP" partnerName="Robin" onChanged={() => undefined} onClose={() => undefined} />));
    expect(screen.getByTestId('private-change-password-open')).toBeTruthy();
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId('private-phone-lock-toggle')).toBeNull();
    expect(screen.queryByTestId('private-preview-toggle')).toBeNull();
  });

  it('on a phone with the preview plugin: message text in notifications is offered, off, and says it is less private', async () => {
    window.__jarvisPrivatePreviewMock = {
      available: vi.fn(async () => ({ available: true })),
      isEnabled: vi.fn(async () => ({ enabled: false })),
      enable: vi.fn(async () => ({ enabled: true })),
      disable: vi.fn(async () => ({ enabled: false })),
    };
    render(wrap(<PrivateChatSecurity token="roomP" partnerName="Robin" onChanged={() => undefined} onClose={() => undefined} />));
    const toggle = await screen.findByTestId('private-preview-toggle');
    expect(toggle.getAttribute('aria-checked')).toBe('false');
    expect(screen.getByText(/less private/)).toBeTruthy();
  });

  it('a preview plugin that is missing from the APK is not offered', async () => {
    window.__jarvisPrivatePreviewMock = {
      available: vi.fn(async () => { throw new Error('not implemented'); }),
      isEnabled: vi.fn(),
      enable: vi.fn(),
      disable: vi.fn(),
    };
    render(wrap(<PrivateChatSecurity token="roomP" partnerName="Robin" onChanged={() => undefined} onClose={() => undefined} />));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId('private-preview-toggle')).toBeNull();
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

const REMINDERS: PrivateChatReminders = {
  enabled: true, available: true, delay_s: 6 * 3600, question_delay_s: 2 * 3600, quiet: '21:30-07:30', daily_max: 2,
};

describe('private chat: reply reminders', () => {
  it('the switch is on by default, says the rules, and turns off on the server', async () => {
    getPrivateChatReminders.mockImplementation(async () => REMINDERS);
    setPrivateChatReminders.mockImplementation(async (_token: string, enabled: boolean) => ({ ...REMINDERS, enabled }));
    render(wrap(<PrivateChatSecurity token="roomP" partnerName="Robin" onChanged={() => undefined} onClose={() => undefined} />));
    const toggle = await screen.findByTestId('private-reminders-toggle');
    expect(toggle.getAttribute('aria-checked')).toBe('true');
    const summary = screen.getByTestId('private-reminders-summary').textContent ?? '';
    expect(summary).toMatch(/after 6 hours \(2 hours if your phone sees a question/);
    expect(summary).toMatch(/never between 21:30 and 07:30, at most 2 times a day/);
    expect(summary).toMatch(/never says what was written/);
    toggle.click();
    await waitFor(() => expect(screen.getByTestId('private-reminders-toggle').getAttribute('aria-checked')).toBe('false'));
    expect(setPrivateChatReminders).toHaveBeenCalledWith('roomP', false);
  });

  it('an older server without reminders, or one with them off, shows no switch', async () => {
    render(wrap(<PrivateChatSecurity token="roomP" partnerName="Robin" onChanged={() => undefined} onClose={() => undefined} />));
    expect(await screen.findByTestId('private-change-password-open')).toBeTruthy();
    expect(screen.queryByTestId('private-reminders')).toBeNull();
  });

  it('an older server (404) shows nothing, but any other failure to load the setting is said', async () => {
    render(wrap(<PrivateChatSecurity token="roomP" partnerName="Robin" onChanged={() => undefined} onClose={() => undefined} />));
    expect(await screen.findByTestId('private-change-password-open')).toBeTruthy();
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId('private-reminders-error')).toBeNull();
  });

  it('a server error loading the reminders is shown instead of hiding the switch', async () => {
    getPrivateChatReminders.mockImplementation(async () => {
      throw Object.assign(new Error('Request failed with status code 502'), { response: { status: 502, data: { detail: 'The reminder store is down.' } } });
    });
    render(wrap(<PrivateChatSecurity token="roomP" partnerName="Robin" onChanged={() => undefined} onClose={() => undefined} />));
    const error = await screen.findByTestId('private-reminders-error');
    expect(error.textContent).toContain('The reminder store is down.');
    expect(error.getAttribute('role')).toBe('alert');
    expect(screen.queryByTestId('private-reminders')).toBeNull();
  });

  it('sends only the bit and the id when the partner asked something, once', async () => {
    const { info, raw, key } = await chat('pw pw pw pw');
    window.__jarvisDeviceUnlockMock = plugin({ unlock: vi.fn(async () => ({ key: toBase64(raw) })) });
    const message = async (id: number, actor: string, text: string) => ({
      id, actor_id: actor, actor_type: 'users', actor_display_name: actor, timestamp: 1000 + id, message_type: 'comment',
      message: await sealMessage(key, { t: 'text', text }),
    });
    const messages = [await message(1, 'a', 'Home at 6'), await message(2, 'b', 'Are you bringing bread'), await message(3, 'b', 'love you')];
    const view = render(wrap(
      <PrivateChatThread
        conversation={{ token: 'roomP', private: true } as never}
        info={{ ...info, storage: 'store' }}
        messages={messages as never}
        myActorIds={new Set(['a'])}
        onUnlockedChange={() => undefined}
        onChanged={() => undefined}
      />,
    ));
    await waitFor(() => expect(flagPrivateChatExpectsReply).toHaveBeenCalledTimes(1), { timeout: 10_000 });
    expect(flagPrivateChatExpectsReply).toHaveBeenCalledWith('roomP', 3, true);
    view.rerender(wrap(
      <PrivateChatThread
        conversation={{ token: 'roomP', private: true } as never}
        info={{ ...info, storage: 'store' }}
        messages={messages as never}
        myActorIds={new Set(['a'])}
        onUnlockedChange={() => undefined}
        onChanged={() => undefined}
      />,
    ));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(flagPrivateChatExpectsReply).toHaveBeenCalledTimes(1);
  }, 30_000);

  it('says nothing when the newest message is mine or asks nothing', async () => {
    const { info, raw, key } = await chat('pw pw pw pw');
    window.__jarvisDeviceUnlockMock = plugin({ unlock: vi.fn(async () => ({ key: toBase64(raw) })) });
    const sealed = async (text: string) => sealMessage(key, { t: 'text', text });
    const messages = [
      { id: 1, actor_id: 'b', actor_type: 'users', timestamp: 1, message_type: 'comment', message: await sealed('Are you home?') },
      { id: 2, actor_id: 'a', actor_type: 'users', timestamp: 2, message_type: 'comment', message: await sealed('Yes') },
      { id: 3, actor_id: 'b', actor_type: 'users', timestamp: 3, message_type: 'comment', message: await sealed('Lovely') },
    ];
    render(wrap(
      <PrivateChatThread
        conversation={{ token: 'roomP', private: true } as never}
        info={{ ...info, storage: 'store' }}
        messages={messages as never}
        myActorIds={new Set(['a'])}
        onUnlockedChange={() => undefined}
        onChanged={() => undefined}
      />,
    ));
    await waitFor(() => expect(screen.getByText('Lovely')).toBeTruthy(), { timeout: 10_000 });
    expect(flagPrivateChatExpectsReply).not.toHaveBeenCalled();
  }, 30_000);
});
