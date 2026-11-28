import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, useNavigate } from 'react-router-dom';
import type { ReactNode } from 'react';
import PrivateChatThread from './PrivateChatThread';
import type { PrivateChatInfo } from './privateChatModel';
import { deriveKey, makeVerifier, PBKDF2_ITERATIONS, randomBytes, toBase64 } from '../../lib/privateChatCrypto';
import type { DeviceUnlockPluginInterface } from '../../plugins/deviceUnlock';

/**
 * What a tap on the Private Chat icon does when something is already open.
 *
 * A notification, a launcher icon and an old home-screen shortcut all arrive as
 * `/family?chat=<token>...`, and when that chat is already the selected one
 * nothing about the address has to change. These tests drive the two ways the
 * thread is told to clear what is on top: `?private=1`, and the panel's tap
 * count, which is what shortcuts made before that parameter existed send.
 */

vi.mock('../../services/api', () => ({
  api: {
    getMe: vi.fn(async () => ({ username: 'alex', display_name: 'Alex', role: 'user', avatar: null })),
    getPrivateChatMarks: vi.fn(async () => ({ marks: [] })),
    markPrivateReactionsSeen: vi.fn(async () => ({ seen: [] })),
    sendPrivateMessage: vi.fn(async () => ({ status: 'SUCCESS' })),
    flagPrivateChatExpectsReply: vi.fn(async () => ({ status: 'SUCCESS' })),
    getPrivateLinkPreview: vi.fn(async () => ({ url: '', title: '' })),
  },
}));

// Every section ask has to reach the page, so Beloved is stubbed down to what
// this file is about: which section it was asked for, and whether it is up.
vi.mock('../../lib/beloved/access', () => ({ canSeeBeloved: () => true }));
vi.mock('./beloved/useLoveIndicator', () => ({ useLoveIndicator: () => ({ unseen: 0, markSeen: () => undefined }) }));
vi.mock('./beloved/seen', () => ({ useSeenVisit: () => undefined }));
vi.mock('./beloved/BelovedPage', () => ({
  default: ({ initialTab, initialTabSeq, onClose }: { initialTab?: string | null; initialTabSeq?: number; onClose: () => void }) => (
    <div data-testid="beloved" data-tab={initialTab ?? ''} data-seq={String(initialTabSeq ?? 0)}>
      <button type="button" data-testid="beloved-close" onClick={onClose}>close</button>
    </div>
  ),
}));

let info: PrivateChatInfo;
let raw = new Uint8Array(32);

beforeEach(async () => {
  // A real chat key, so the phone's lock can hand it back: the chat is already
  // unlocked on this device, and the lock screen is another file's subject.
  const kdf = { salt: toBase64(randomBytes(16)), iterations: PBKDF2_ITERATIONS };
  const key = await deriveKey('pw pw pw pw', kdf);
  const base = await crypto.subtle.importKey('raw', new TextEncoder().encode('pw pw pw pw'), 'PBKDF2', false, ['deriveBits']);
  raw = new Uint8Array(await crypto.subtle.deriveBits(
    { name: 'PBKDF2', hash: 'SHA-256', salt: Uint8Array.from(atob(kdf.salt), (c) => c.charCodeAt(0)), iterations: kdf.iterations },
    base, 256,
  ));
  info = {
    token: 'roomQ', members: ['a', 'b'], partner: 'b', partner_name: 'Robin', created_by: 'a',
    created_at: 1, version: 1, kdf: { name: 'PBKDF2-SHA256', ...kdf }, verifier: await makeVerifier(key),
    storage: 'store', beloved: true,
  } as unknown as PrivateChatInfo;
  const plugin: DeviceUnlockPluginInterface = {
    available: async () => ({ available: true }),
    isEnabled: async () => ({ enabled: true }),
    enable: async () => ({ enabled: true }),
    unlock: async () => ({ key: toBase64(raw) }),
    disable: async () => ({ enabled: false }),
  };
  window.__jarvisDeviceUnlockMock = plugin;
});

const wrap = (node: ReactNode) => (
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{node}</QueryClientProvider>
);

function unlocked(extra: { focusSeq?: number } = {}) {
  return (
    <PrivateChatThread
      conversation={{ token: 'roomQ', private: true } as never}
      info={info}
      messages={[]}
      myActorIds={new Set(['a'])}
      onUnlockedChange={() => undefined}
      onChanged={() => undefined}
      focusSeq={extra.focusSeq}
    />
  );
}

function Again({ to }: { to: string }) {
  const navigate = useNavigate();
  return <button type="button" data-testid="again" onClick={() => navigate(to)}>again</button>;
}

afterEach(() => {
  delete window.__jarvisDeviceUnlockMock;
  window.history.replaceState({}, '', '/');
});

describe('the chat icon wins over whatever is on top of it', () => {
  it('opens the chat, not Beloved, when a shortcut says private=1 and still carries a section', async () => {
    render(wrap(<MemoryRouter initialEntries={['/family?chat=roomQ&beloved=notes&private=1']}>{unlocked()}</MemoryRouter>));
    await waitFor(() => expect(screen.getByPlaceholderText('Private message')).toBeTruthy());
    expect(screen.queryByTestId('beloved')).toBeNull();
  });

  it('opens the section a notice names when the chat itself is not asked for', async () => {
    render(wrap(<MemoryRouter initialEntries={['/family?chat=roomQ&beloved=notes']}>{unlocked()}</MemoryRouter>));
    const page = await screen.findByTestId('beloved');
    expect(page.getAttribute('data-tab')).toBe('notes');
  });

  it('closes a Beloved page already open when an old shortcut is tapped again', async () => {
    // The old shortcut has no `&private=1`, and the address is the one the page
    // was opened with, so the panel counts the tap and hands the count down.
    const view = render(wrap(<MemoryRouter initialEntries={['/family?chat=roomQ&beloved=notes']}>{unlocked()}</MemoryRouter>));
    await screen.findByTestId('beloved');
    view.rerender(wrap(<MemoryRouter initialEntries={['/family?chat=roomQ&beloved=notes']}>{unlocked({ focusSeq: 1 })}</MemoryRouter>));
    await waitFor(() => expect(screen.queryByTestId('beloved')).toBeNull());
  });

  it('walks back to the same section when a second note names it after the person moved on', async () => {
    render(
      wrap(
        <MemoryRouter initialEntries={['/family?chat=roomQ&beloved=notes']}>
          {unlocked()}
          <Again to="/family?chat=roomQ&beloved=notes" />
        </MemoryRouter>,
      ),
    );
    const first = await screen.findByTestId('beloved');
    expect(first.getAttribute('data-seq')).toBe('1');
    // The person reads the notes and goes back to Us on their own.
    fireEvent.click(screen.getByTestId('beloved-close'));
    await waitFor(() => expect(screen.queryByTestId('beloved')).toBeNull());
    // A second note notification names the same section: the count is what
    // makes it a new ask, so the page opens on Notes again.
    fireEvent.click(screen.getByTestId('again'));
    const again = await screen.findByTestId('beloved');
    expect(again.getAttribute('data-tab')).toBe('notes');
    expect(again.getAttribute('data-seq')).toBe('2');
  });
});
