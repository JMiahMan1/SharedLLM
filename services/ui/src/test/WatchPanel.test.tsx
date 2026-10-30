import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const hook = vi.hoisted(() => ({ useWatchBle: vi.fn() }));

vi.mock('../hooks/useWatchBle', () => ({ useWatchBle: hook.useWatchBle }));

const lib = vi.hoisted(() => ({ syncWatchTime: vi.fn(async () => undefined) }));
vi.mock('../lib/watchBle', () => ({ syncWatchTime: lib.syncWatchTime }));

import WatchPanel from '../components/settings/WatchPanel';

const base = {
  supported: true,
  connected: false,
  stats: null,
  connect: vi.fn(async () => undefined),
  disconnect: vi.fn(async () => undefined),
  refresh: vi.fn(async () => undefined),
  sendNotification: vi.fn(async () => undefined),
};

const STATS = {
  steps: 1335,
  goal: 10000,
  active_min: 12,
  battery: 96,
  volts: 4.03,
  uptime: 3661,
  mode: 'deep',
  synced: 0,
  charging: false,
  unplugged: 0,
  left_h: 31,
  last_charge_h: 2.4,
};

describe('WatchPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    hook.useWatchBle.mockReturnValue({ ...base });
  });

  it('says Bluetooth is unavailable off-device', () => {
    hook.useWatchBle.mockReturnValue({ ...base, supported: false });
    render(<WatchPanel />);

    expect(screen.getByText(/bluetooth is not available here/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /connect/i })).not.toBeInTheDocument();
  });

  it('offers Connect before a connection exists', () => {
    render(<WatchPanel />);
    expect(screen.getByRole('button', { name: /connect/i })).toBeInTheDocument();
  });

  it('connects when the button is pressed', async () => {
    const connect = vi.fn(async () => undefined);
    hook.useWatchBle.mockReturnValue({ ...base, connect });
    render(<WatchPanel />);

    fireEvent.click(screen.getByRole('button', { name: /connect/i }));
    await waitFor(() => expect(connect).toHaveBeenCalled());
  });

  it('shows live stats and the action buttons when connected', () => {
    hook.useWatchBle.mockReturnValue({ ...base, connected: true, stats: STATS });
    render(<WatchPanel />);

    const stats = screen.getByTestId('watch-stats');
    expect(stats).toHaveTextContent('96%');
    expect(stats).toHaveTextContent('Deep');
    expect(stats).toHaveTextContent('1h 1m');
    expect(stats.textContent).toMatch(/1[,.]?335/);

    expect(screen.getByRole('button', { name: /sync time/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /send test notification/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /disconnect/i })).toBeInTheDocument();
  });

  it('syncs the watch clock when asked', async () => {
    hook.useWatchBle.mockReturnValue({ ...base, connected: true, stats: STATS });
    render(<WatchPanel />);

    fireEvent.click(screen.getByRole('button', { name: /sync time/i }));
    await waitFor(() => expect(lib.syncWatchTime).toHaveBeenCalled());
  });

  it('sends a test notification when asked', async () => {
    const sendNotification = vi.fn(async () => undefined);
    hook.useWatchBle.mockReturnValue({ ...base, connected: true, stats: STATS, sendNotification });
    render(<WatchPanel />);

    fireEvent.click(screen.getByRole('button', { name: /send test notification/i }));
    await waitFor(() => expect(sendNotification).toHaveBeenCalledWith('Jarvis', expect.any(String)));
  });
});
