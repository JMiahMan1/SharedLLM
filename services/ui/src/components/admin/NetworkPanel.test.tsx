import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import NetworkPanel from './NetworkPanel';
import { api } from '../../services/api';
import type { NetworkStatus } from '../../lib/network';

vi.mock('../../services/api', () => ({
  api: {
    getNetworkStatus: vi.fn(),
    checkNetwork: vi.fn(),
    saveRouterSettings: vi.fn(),
    getRouterLog: vi.fn(async () => ({ lines: [{ id: 1, time: 1_792_000_000, level: 'err', msg: 'kernel: oops' }] })),
    getNetworkUsage: vi.fn(async (hours: number) => ({ hours, devices: [{ mac: 'aa', name: 'server', ip: '192.168.2.205', bytes: 5 * 1024 ** 3 }] })),
  },
}));

const HOGGED: NetworkStatus = {
  configured: true,
  connected: true,
  internet: true,
  checked_at: Date.now() / 1000,
  router: { model: 'Test router', version: '25.12', uptime: 3 * 86400, memory_free_pct: 58 },
  radios: [{
    device: 'wl0-ap0', ssid: 'Home-Devices', band: '2.4 GHz', channel: 4, busy_pct: 82,
    clients: [
      { mac: 'aa', name: 'server', ip: '192.168.2.205', signal: -45, rx_mbps: 72.2, tx_mbps: 19.5, airtime_pct: 55, connected_s: 100 },
      { mac: 'bb', name: 'LivingRoomTv', ip: '192.168.2.160', signal: -78, rx_mbps: 65, tx_mbps: 65, airtime_pct: 0.4, connected_s: 100 },
    ],
  }],
  conntrack: { count: 1200, max: 15360 },
  events: [{ time: Date.now() / 1000, title: 'server is crowding the 2.4 GHz Wi-Fi', body: 'Other devices may lose their connection.', tag: 'admin:network:airtime:wl0' }],
};

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><NetworkPanel /></QueryClientProvider>);
}

describe('NetworkPanel', () => {
  beforeEach(() => vi.clearAllMocks());

  it('offers setup before a router is connected', async () => {
    vi.mocked(api.getNetworkStatus).mockResolvedValue({ configured: false, internet: true });
    renderPanel();
    expect(await screen.findByText('Not set up')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Set up' })).toBeTruthy();
    expect(screen.queryByText('Router log')).toBeNull();
  });

  it('shows a crowded channel and the device crowding it', async () => {
    vi.mocked(api.getNetworkStatus).mockResolvedValue(HOGGED);
    renderPanel();
    expect(await screen.findByText('Connected')).toBeTruthy();
    expect(screen.getByText('Congested · 82% busy')).toBeTruthy();
    expect(screen.getByText('55% airtime')).toBeTruthy();
    // Under 1% is not worth showing.
    expect(screen.queryByText('0% airtime')).toBeNull();
    expect(screen.getByText(/Weak \(-78 dBm\)/)).toBeTruthy();
    expect(screen.getByText('server is crowding the 2.4 GHz Wi-Fi')).toBeTruthy();
    expect(await screen.findByText('5.00 GB')).toBeTruthy();
    expect(await screen.findByText(/kernel: oops/)).toBeTruthy();
  });

  it('keeps the stored password when the field is left blank', async () => {
    vi.mocked(api.getNetworkStatus).mockResolvedValue(HOGGED);
    vi.mocked(api.saveRouterSettings).mockResolvedValue(HOGGED);
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: 'Router login' }));
    expect(screen.getByLabelText('Router password').getAttribute('placeholder')).toBe('Unchanged');
    fireEvent.click(screen.getByRole('button', { name: 'Save and check' }));
    await waitFor(() => expect(api.saveRouterSettings).toHaveBeenCalledWith({
      url: 'https://192.168.2.1', username: 'jarvis', password: undefined,
    }));
  });
});
