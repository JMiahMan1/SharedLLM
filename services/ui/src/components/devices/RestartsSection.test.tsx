import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import RestartsSection from './RestartsSection';
import * as restarts from '../../lib/restarts';
import { countsText, memoryLines, type DeviceRestarts, type RestartEvent } from '../../lib/restarts';

vi.mock('../../lib/restarts', async (importOriginal) => ({
  ...((await importOriginal()) as Record<string, unknown>),
  getDeviceRestarts: vi.fn(),
  setRestartLogLevel: vi.fn(),
}));

const ago = (min: number) => new Date(Date.now() - min * 60_000).toISOString();

const event = (over: Partial<RestartEvent> = {}): RestartEvent => ({
  id: 'e1', at: ago(10), reconnected_at: ago(10), kind: 'restart', summary: 'Restarted (Exception panic), 4 s offline',
  evidence: 'its uptime went back from 2.1 h to 3 s', cause: 'Reset reason: Exception panic', hint: null,
  downtime_s: 4, expected: false, error: 'SocketClosedAPIError: Connection lost', silent_s: 0.4,
  reset_reason: 'Exception panic', last_reset_reason: null, last_crash: null,
  memory: { heap_free: { before: 12_288, lowest: 12_288, after: 199_680 } },
  context_text: ['A voice run ended 24 s before (ok)'], log_level: 'warning', dropped_lines: 0,
  lines_before: [[ago(10), 'E', '[E][ring_buffer:62]: Failed to allocate 32768 bytes']], lines_after: [],
  ...over,
});

const base = (over: Partial<DeviceRestarts> = {}): DeviceRestarts => ({
  supported: true, device_id: 'dev1', name: 'test-node', title: 'Test node', connected: true, log_level: 'warning',
  log_levels: ['off', 'error', 'warning', 'info', 'debug'], subscribed: true, subscribe_error: null,
  sensors: ['heap_free', 'reset_reason', 'uptime'], has_debug: true, guidance: null, current: {},
  buffer: { lines: 12, max: 200, dropped: 0 }, offline: null,
  counts_1h: { restart: 1, drop: 0, unknown: 0 }, counts_24h: { restart: 1, drop: 2, unknown: 0 },
  events: [event()], recent_lines: [[ago(1), 'W', '[W][wifi:1]: signal weak']],
  ...over,
});

function renderSection() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><RestartsSection deviceId="dev1" /></QueryClientProvider>);
}

describe('RestartsSection', () => {
  beforeEach(() => vi.clearAllMocks());

  it('lists restarts with their cause, context, memory and the lines before', async () => {
    vi.mocked(restarts.getDeviceRestarts).mockResolvedValue(base());
    renderSection();
    const section = await screen.findByTestId('section-restarts');
    expect(within(section).getByTestId('restart-summary')).toHaveTextContent('Last 24 hours: 1 restart, 2 dropped connections.');
    const row = within(section).getByTestId('restart-event');
    expect(row).toHaveTextContent('Restarted (Exception panic), 4 s offline');
    expect(row).toHaveTextContent('10 min ago · Reset reason: Exception panic');
    fireEvent.click(within(row).getByText('Restarted (Exception panic), 4 s offline'));
    const detail = within(row).getByTestId('restart-event-detail');
    expect(detail).toHaveTextContent('its uptime went back from 2.1 h to 3 s');
    expect(detail).toHaveTextContent('A voice run ended 24 s before (ok)');
    expect(detail).toHaveTextContent('Free heap: 12 kB before, 195 kB after');
    expect(within(detail).getByTestId('restart-lines')).toHaveTextContent('Failed to allocate 32768 bytes');
    expect(screen.queryByTestId('restart-guidance')).toBeNull();
  });

  it('shows the YAML to add when the device cannot report why it restarted', async () => {
    vi.mocked(restarts.getDeviceRestarts).mockResolvedValue(base({
      has_debug: false, sensors: [],
      guidance: { missing: ['reset reason (debug component)', 'uptime sensor'], yaml: 'debug:\ntext_sensor:\n  - platform: debug\n',
        text: 'Without a reset reason the hub cannot say why the device restarted.' },
      events: [event({ kind: 'unknown', summary: 'Disconnected; restart or dropped connection not known, 2 s offline',
        cause: null, reset_reason: null, hint: 'The node closed the connection or no longer knew it.' })],
    }));
    renderSection();
    const guide = await screen.findByTestId('restart-guidance');
    expect(guide).toHaveTextContent('Missing: reset reason (debug component), uptime sensor.');
    expect(within(guide).getByTestId('restart-yaml').textContent).toContain('platform: debug');
    expect(screen.getByTestId('restart-event')).toHaveTextContent('Restart or drop not known');
  });

  it('changes the log level the hub keeps', async () => {
    vi.mocked(restarts.getDeviceRestarts).mockResolvedValue(base());
    vi.mocked(restarts.setRestartLogLevel).mockResolvedValue(base({ log_level: 'debug' }));
    renderSection();
    const select = await screen.findByLabelText('Log lines kept from the device');
    expect(select).toHaveValue('warning');
    fireEvent.change(select, { target: { value: 'debug' } });
    await waitFor(() => expect(restarts.setRestartLogLevel).toHaveBeenCalledWith('dev1', 'debug'));
    await waitFor(() => expect(screen.getByLabelText('Log lines kept from the device')).toHaveValue('debug'));
  });

  it('says when nothing happened, and shows nothing for a device that is not an ESPHome node', async () => {
    vi.mocked(restarts.getDeviceRestarts).mockResolvedValue(base({ events: [], counts_24h: { restart: 0, drop: 0, unknown: 0 } }));
    const { unmount } = renderSection();
    expect(await screen.findByTestId('restart-summary')).toHaveTextContent('No restarts or dropped connections recorded.');
    unmount();
    vi.mocked(restarts.getDeviceRestarts).mockResolvedValue({ supported: false, device_id: 'dev1' });
    const { container } = renderSection();
    await waitFor(() => expect(restarts.getDeviceRestarts).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(container.querySelector('[data-testid="section-restarts"]')).toBeNull());
  });

  it('shows five events until asked for all', async () => {
    const events = Array.from({ length: 7 }, (_, i) => event({ id: `e${i}`, summary: `Restart ${i}` }));
    vi.mocked(restarts.getDeviceRestarts).mockResolvedValue(base({ events }));
    renderSection();
    await screen.findByTestId('section-restarts');
    expect(screen.getAllByTestId('restart-event')).toHaveLength(5);
    fireEvent.click(screen.getByRole('button', { name: 'Show all 7' }));
    expect(screen.getAllByTestId('restart-event')).toHaveLength(7);
  });
});

describe('restart helpers', () => {
  it('words counts and memory', () => {
    expect(countsText({ restart: 0, drop: 0, unknown: 0 })).toBeNull();
    expect(countsText({ restart: 2, drop: 0, unknown: 1 })).toBe('2 restarts, 1 disconnect of unknown cause');
    expect(memoryLines({ psram_free: { before: 2 * 1024 * 1024, lowest: 1024 * 1024, after: null } }))
      .toEqual(['Free PSRAM: 2.0 MB before, lowest 1.0 MB']);
  });
});
