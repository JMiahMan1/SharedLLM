import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';

/**
 * A tap on a notification, before and after there is a session.
 *
 * What this guards: the phone announces the tap the moment the activity opens,
 * which can be before the bundle's listener exists (a cold start) and before the
 * person's session has been read out of storage (a slow unlock). Either one used
 * to drop the destination and leave the dashboard showing, so a Father's Day
 * message opened on the wrong page and looked like a bug in the message.
 */

const m = vi.hoisted(() => ({
  start: vi.fn(),
  notificationState: vi.fn(),
  openNotificationSettings: vi.fn(),
  takePendingOpen: vi.fn(),
  toast: Object.assign(vi.fn(), { dismiss: vi.fn() }),
  listeners: {} as Record<string, (data: Record<string, string>) => void>,
  auth: { token: 'k' as string | null, isLoading: false },
  voice: vi.fn(),
}));

vi.mock('@capacitor/core', () => ({ Capacitor: { isNativePlatform: () => true } }));
vi.mock('@capacitor/app', () => ({ App: { addListener: () => Promise.resolve({ remove: () => Promise.resolve() }) } }));
vi.mock('react-hot-toast', () => ({ default: m.toast }));
vi.mock('../context/AuthContext', () => ({ useAuth: () => m.auth }));
vi.mock('../plugins/chatNotify', () => ({
  default: {
    start: m.start,
    notificationState: m.notificationState,
    openNotificationSettings: m.openNotificationSettings,
    takePendingOpen: m.takePendingOpen,
    addListener: (event: string, cb: (data: Record<string, string>) => void) => {
      m.listeners[event] = cb;
      return Promise.resolve({ remove: () => Promise.resolve() });
    },
  },
}));
vi.mock('../lib/voiceAssistant', () => ({ openVoiceAssistant: m.voice }));

import NativeBridge from './NativeBridge';

function Where() {
  const location = useLocation();
  return (
    <p data-testid="where" data-key={location.key}>{`${location.pathname}${location.search}`}</p>
  );
}

function mount(initial = '/') {
  render(
    <MemoryRouter initialEntries={[initial]}>
      <NativeBridge />
      <Routes>
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('a notification tap found before the page was ready', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    m.start.mockResolvedValue({ notifications: true });
    m.notificationState.mockResolvedValue({ app: true, chat: true });
    m.takePendingOpen.mockResolvedValue({});
    m.auth = { token: 'k', isLoading: false };
    window.localStorage.clear();
  });

  it('opens the page the tap asked for', async () => {
    m.takePendingOpen.mockResolvedValue({ path: '/family?chat=abc&private=1' });
    mount();
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe('/family?chat=abc&private=1'));
  });

  it('waits for the session instead of losing the tap', async () => {
    // A cold start: the destination arrives while the token is still being read.
    m.auth = { token: null, isLoading: true };
    m.takePendingOpen.mockResolvedValue({ path: '/family?chat=abc&beloved=notes' });
    const { rerender } = render(
      <MemoryRouter initialEntries={['/']}>
        <NativeBridge />
        <Where />
      </MemoryRouter>,
    );
    await act(async () => undefined);
    expect(screen.getByTestId('where').textContent).toBe('/');

    m.auth = { token: 'k', isLoading: false };
    rerender(
      <MemoryRouter initialEntries={['/']}>
        <NativeBridge />
        <Where />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe('/family?chat=abc&beloved=notes'));
  });

  it('a tap that arrives after the listener is attached goes straight there', async () => {
    mount();
    await waitFor(() => expect(m.listeners.open).toBeTypeOf('function'));
    await act(async () => m.listeners.open({ path: '/beloved?x=1' }));
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe('/beloved?x=1'));
  });

  it('a tap while signed out is held, not navigated and lost', async () => {
    m.auth = { token: null, isLoading: false };
    mount();
    await waitFor(() => expect(m.listeners.open).toBeTypeOf('function'));
    await act(async () => m.listeners.open({ path: '/family?chat=abc' }));
    expect(screen.getByTestId('where').textContent).toBe('/');
  });

  it('refuses an address no page of Jarvis reads', async () => {
    m.takePendingOpen.mockResolvedValue({ path: 'https://example.com/phish' });
    mount();
    await act(async () => undefined);
    expect(screen.getByTestId('where').textContent).toBe('/');
  });

  it('a cold start hands the same tap over twice and it opens once', async () => {
    // The plugin keeps the tap for takePendingOpen() and also re-sends it as a
    // retained 'open' event, both within a few milliseconds. Two navigations
    // would run the destination's own arrival twice.
    m.takePendingOpen.mockResolvedValue({ path: '/family?chat=abc&beloved=notes' });
    mount();
    await waitFor(() => expect(m.listeners.open).toBeTypeOf('function'));
    await act(async () => m.listeners.open({ path: '/family?chat=abc&beloved=notes' }));
    expect(screen.getByTestId('where').textContent).toBe('/family?chat=abc&beloved=notes');
    // A navigation gives the address a new key, so an unchanged key is proof the
    // second delivery of the same tap did not navigate again.
    const key = screen.getByTestId('where').getAttribute('data-key');
    await act(async () => m.listeners.open({ path: '/family?chat=abc&beloved=notes' }));
    expect(screen.getByTestId('where').getAttribute('data-key')).toBe(key);
  });

  it('an address that is already showing is still a real navigation', async () => {
    // Same chat, a different tab in the query: it must reach the page that reads it.
    m.takePendingOpen.mockResolvedValue({ path: '/family?chat=abc&beloved=dates' });
    mount('/family?chat=abc&beloved=notes');
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe('/family?chat=abc&beloved=dates'));
  });
});
