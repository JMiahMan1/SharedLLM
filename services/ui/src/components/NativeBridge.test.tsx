import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

const m = vi.hoisted(() => ({
  start: vi.fn(),
  notificationState: vi.fn(),
  openNotificationSettings: vi.fn(),
  toast: Object.assign(vi.fn(), { dismiss: vi.fn() }),
  listeners: {} as Record<string, (data: Record<string, string>) => void>,
}));

vi.mock('@capacitor/core', () => ({ Capacitor: { isNativePlatform: () => true } }));
vi.mock('@capacitor/app', () => ({ App: { addListener: () => Promise.resolve({ remove: () => Promise.resolve() }) } }));
vi.mock('react-hot-toast', () => ({ default: m.toast }));
vi.mock('../context/AuthContext', () => ({ useAuth: () => ({ token: 'k' }) }));
vi.mock('../plugins/chatNotify', () => ({
  default: {
    start: m.start,
    notificationState: m.notificationState,
    openNotificationSettings: m.openNotificationSettings,
    addListener: (event: string, cb: (data: Record<string, string>) => void) => {
      m.listeners[event] = cb;
      return Promise.resolve({ remove: () => Promise.resolve() });
    },
  },
}));

import NativeBridge from './NativeBridge';

const mount = () => render(<MemoryRouter><NativeBridge /></MemoryRouter>);

describe('NativeBridge notification check', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    m.start.mockResolvedValue({ notifications: true });
  });

  it('says so on the device when Android is not letting Jarvis notify', async () => {
    m.notificationState.mockResolvedValue({ app: false, chat: true });
    mount();
    // Long enough to read, then out of the way of the page's own buttons (it used to stay forever).
    await waitFor(() => expect(m.toast).toHaveBeenCalledWith(expect.any(Function), expect.objectContaining({ id: 'jarvis-notifications-off', duration: 15_000 })));
  });

  it('stays quiet for a week after Not now', async () => {
    window.localStorage.setItem('jarvis-notifications-off-snoozed-until', String(Date.now() + 60_000));
    m.notificationState.mockResolvedValue({ app: false, chat: true });
    mount();
    await waitFor(() => expect(m.notificationState).toHaveBeenCalled());
    expect(m.toast).not.toHaveBeenCalled();
    window.localStorage.removeItem('jarvis-notifications-off-snoozed-until');
  });

  it('flags family chat switched off on its own', async () => {
    m.notificationState.mockResolvedValue({ app: true, chat: false });
    mount();
    await waitFor(() => expect(m.toast).toHaveBeenCalled());
  });

  it('stays quiet, and clears an old warning, when notices can show', async () => {
    m.notificationState.mockResolvedValue({ app: true, chat: true });
    mount();
    await waitFor(() => expect(m.toast.dismiss).toHaveBeenCalledWith('jarvis-notifications-off'));
    expect(m.toast).not.toHaveBeenCalled();
  });
});

describe('a nudge while Jarvis is open', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    m.start.mockResolvedValue({ notifications: true });
    m.notificationState.mockResolvedValue({ app: true, chat: true });
  });

  it('shows full screen with who nudged, and closes', async () => {
    mount();
    await waitFor(() => expect(m.listeners.nudge).toBeTypeOf('function'));
    act(() => m.listeners.nudge({ title: 'Private chat', body: 'Alex nudged you in your private chat', path: '/family?chat=room1' }));
    expect(await screen.findByTestId('nudge-overlay')).toHaveTextContent('Alex nudged you in your private chat');
    await userEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(screen.queryByTestId('nudge-overlay')).toBeNull();
  });

  it('a reply reminder never opens the nudge card', async () => {
    mount();
    await waitFor(() => expect(m.listeners.nudge).toBeTypeOf('function'));
    act(() => m.listeners.nudge({ title: 'Private chat', body: 'Alex is waiting to hear back from you', path: '/family?chat=room1',
      kind: 'private_reminder' }));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(screen.queryByTestId('nudge-overlay')).toBeNull();
  });
});
