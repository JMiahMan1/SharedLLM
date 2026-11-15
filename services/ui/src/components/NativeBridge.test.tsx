import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const m = vi.hoisted(() => ({
  start: vi.fn(),
  notificationState: vi.fn(),
  openNotificationSettings: vi.fn(),
  toast: Object.assign(vi.fn(), { dismiss: vi.fn() }),
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
    addListener: () => Promise.resolve({ remove: () => Promise.resolve() }),
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
    await waitFor(() => expect(m.toast).toHaveBeenCalledWith(expect.any(Function), expect.objectContaining({ id: 'jarvis-notifications-off', duration: Infinity })));
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
