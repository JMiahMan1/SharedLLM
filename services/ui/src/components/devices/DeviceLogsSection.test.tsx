import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import DeviceLogsSection from './DeviceLogsSection';
import * as deviceLogs from '../../lib/deviceLogs';
import type { StreamItem, ViewLevel } from '../../lib/deviceLogs';
import { NdjsonError } from '../../services/api';

vi.mock('../../lib/deviceLogs', async (importOriginal) => ({
  ...((await importOriginal()) as Record<string, unknown>),
  streamDeviceLogs: vi.fn(),
  requestConfigDump: vi.fn(),
}));

const at = '2026-01-02T03:04:05.006+00:00';

type Opened = { level: ViewLevel; since: number | null; push: (item: StreamItem) => void; signal: AbortSignal };

/** Each stream the section opens stays open until aborted; the test pushes items into it. */
function streams(): Opened[] {
  const opened: Opened[] = [];
  vi.mocked(deviceLogs.streamDeviceLogs).mockImplementation((_id, level, since, onItem, signal) => {
    opened.push({ level, since, push: onItem, signal });
    return new Promise<void>((resolve) => signal.addEventListener('abort', () => resolve()));
  });
  return opened;
}

function renderSection() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={qc}><DeviceLogsSection deviceId="dev1" deviceName="Test node" /></QueryClientProvider>);
}

async function openLogs() {
  fireEvent.click(await screen.findByRole('button', { name: /Show live logs/ }));
  return screen.getByTestId('device-log');
}

describe('DeviceLogsSection', () => {
  beforeEach(() => vi.clearAllMocks());

  it('opens a stream only when asked, tails lines and shows the restart markers in line', async () => {
    const opened = streams();
    renderSection();
    expect(deviceLogs.streamDeviceLogs).not.toHaveBeenCalled();
    const pane = await openLogs();
    await waitFor(() => expect(opened).toHaveLength(1));
    expect(opened[0].level).toBe('debug');
    act(() => {
      opened[0].push({ type: 'hello', seq: 0, level: 'debug', device_id: 'dev1', connected: true });
      opened[0].push({ type: 'line', seq: 1, at, level: 'warning', tag: 'wifi', message: 'weak signal' });
      opened[0].push({ type: 'marker', seq: 2, at, event: 'offline', message: 'Device offline' });
      opened[0].push({ type: 'marker', seq: 3, at, event: 'restart', message: 'Restarted, 2 s offline' });
    });
    await waitFor(() => expect(within(pane).getAllByTestId('log-marker')).toHaveLength(2));
    expect(within(pane).getByTestId('log-line')).toHaveTextContent('[W][wifi] weak signal');
    expect(within(pane).getAllByTestId('log-marker')[1]).toHaveTextContent('Restarted, 2 s offline');
    expect(screen.getByTestId('log-status')).toHaveTextContent('Device offline');
    fireEvent.click(screen.getByRole('button', { name: /Close/ }));
    expect(opened[0].signal.aborted).toBe(true);
  });

  it('filters by level and search, and a new level reopens the stream at that level', async () => {
    const opened = streams();
    renderSection();
    const pane = await openLogs();
    await waitFor(() => expect(opened).toHaveLength(1));
    act(() => {
      opened[0].push({ type: 'line', seq: 1, at, level: 'debug', tag: 'sensor', message: 'reading 21.5' });
      opened[0].push({ type: 'line', seq: 2, at, level: 'error', tag: 'i2c', message: 'bus fault' });
    });
    await waitFor(() => expect(within(pane).getAllByTestId('log-line')).toHaveLength(2));
    fireEvent.change(screen.getByLabelText('Search the log'), { target: { value: 'I2C' } });
    expect(within(pane).getAllByTestId('log-line')).toHaveLength(1);
    fireEvent.change(screen.getByLabelText('Search the log'), { target: { value: '' } });
    fireEvent.change(screen.getByLabelText('Log level'), { target: { value: 'error' } });
    await waitFor(() => expect(opened).toHaveLength(2));
    expect(opened[0].signal.aborted).toBe(true);
    expect(opened[1].level).toBe('error');
  });

  it('pause holds the view and counts what arrives; resume shows it', async () => {
    const opened = streams();
    renderSection();
    const pane = await openLogs();
    await waitFor(() => expect(opened).toHaveLength(1));
    act(() => {
      opened[0].push({ type: 'hello', seq: 0, level: 'debug', device_id: 'dev1', connected: true });
      opened[0].push({ type: 'line', seq: 1, at, level: 'info', tag: 'app', message: 'one' });
    });
    await waitFor(() => expect(within(pane).getAllByTestId('log-line')).toHaveLength(1));
    fireEvent.click(screen.getByRole('button', { name: /Pause/ }));
    act(() => { opened[0].push({ type: 'line', seq: 2, at, level: 'info', tag: 'app', message: 'two' }); });
    await waitFor(() => expect(screen.getByTestId('log-status')).toHaveTextContent('Paused, 1 new line waiting'));
    expect(within(pane).getAllByTestId('log-line')).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', { name: /Resume/ }));
    expect(within(pane).getAllByTestId('log-line')).toHaveLength(2);
  });

  it('asks for a config dump and raises a warnings-only view to info so the dump shows', async () => {
    const opened = streams();
    vi.mocked(deviceLogs.requestConfigDump).mockResolvedValue();
    renderSection();
    await openLogs();
    fireEvent.change(screen.getByLabelText('Log level'), { target: { value: 'warning' } });
    await waitFor(() => expect(opened.at(-1)?.level).toBe('warning'));
    fireEvent.click(screen.getByRole('button', { name: /Show config dump/ }));
    await waitFor(() => expect(deviceLogs.requestConfigDump).toHaveBeenCalledWith('dev1'));
    await waitFor(() => expect(opened.at(-1)?.level).toBe('info'));
  });

  it('says why when the hub refuses the stream', async () => {
    vi.mocked(deviceLogs.streamDeviceLogs).mockRejectedValue(new NdjsonError('Only an admin can read a device\'s logs', 403));
    renderSection();
    await openLogs();
    await waitFor(() => expect(screen.getByTestId('log-status')).toHaveTextContent("Only an admin can read a device's logs"));
    expect(deviceLogs.streamDeviceLogs).toHaveBeenCalledTimes(1);
  });
});
