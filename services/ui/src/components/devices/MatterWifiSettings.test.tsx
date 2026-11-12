import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import MatterWifiSettings from './MatterWifiSettings';
import * as matter from '../../lib/matterCommissioning';
import { wifiProblem } from '../../lib/matterCommissioning';

vi.mock('../../lib/matterCommissioning', async (importOriginal) => ({
  ...((await importOriginal()) as Record<string, unknown>),
  getMatterSettings: vi.fn(),
  setMatterWifi: vi.fn(),
  clearMatterWifi: vi.fn(),
}));
vi.mock('react-hot-toast', () => {
  const toast = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() });
  return { default: toast };
});
vi.mock('../ui/confirmDialog', () => ({ confirmDialog: vi.fn().mockResolvedValue(true) }));

const wrap = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MatterWifiSettings />
    </QueryClientProvider>,
  );

describe('wifiProblem', () => {
  it('checks names and passwords the way the hub does', () => {
    expect(wifiProblem('', 'example-passphrase')).toMatch(/network name/);
    expect(wifiProblem('x'.repeat(33), 'example-passphrase')).toMatch(/32 bytes/);
    expect(wifiProblem('ExampleNet', 'short')).toMatch(/8 to 63/);
    expect(wifiProblem('ExampleNet', 'ab'.repeat(32))).toBeNull();
    expect(wifiProblem('ExampleNet', 'example-passphrase')).toBeNull();
  });
});

describe('MatterWifiSettings', () => {
  beforeEach(() => vi.clearAllMocks());

  it('sets the household Wi-Fi when none is set, then shows only its name', async () => {
    vi.mocked(matter.getMatterSettings).mockResolvedValue({
      bluetooth: { enabled: true, setting: 'MATTER_BLUETOOTH_ADAPTER' },
      wifi: { configured: false, ssid: null, updated_at: null },
    });
    vi.mocked(matter.setMatterWifi).mockResolvedValue({ configured: true, ssid: 'ExampleNet', updated_at: 'x' });
    wrap();
    fireEvent.change(await screen.findByLabelText('Network name'), { target: { value: 'ExampleNet' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'short' } });
    fireEvent.click(screen.getByRole('button', { name: /Save Wi-Fi/ }));
    expect(await screen.findByRole('alert')).toHaveTextContent('8 to 63');
    expect(matter.setMatterWifi).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'example-passphrase' } });
    fireEvent.click(screen.getByRole('button', { name: /Save Wi-Fi/ }));
    await waitFor(() => expect(matter.setMatterWifi).toHaveBeenCalledWith('ExampleNet', 'example-passphrase'));
    expect(await screen.findByTestId('matter-wifi-current')).toHaveTextContent('ExampleNet');
    expect(screen.getByTestId('matter-wifi-current')).toHaveTextContent('Password saved');
    expect(screen.queryByDisplayValue('example-passphrase')).toBeNull();
  });

  it('changes and forgets the saved network', async () => {
    vi.mocked(matter.getMatterSettings).mockResolvedValue({
      bluetooth: { enabled: null, setting: 'MATTER_BLUETOOTH_ADAPTER' },
      wifi: { configured: true, ssid: 'ExampleNet', updated_at: 'x' },
    });
    vi.mocked(matter.clearMatterWifi).mockResolvedValue({ configured: false, ssid: null, updated_at: null });
    wrap();
    await waitFor(() => expect(screen.getByTestId('matter-bluetooth')).toHaveTextContent('not connected'));
    fireEvent.click(await screen.findByRole('button', { name: 'Change' }));
    expect(screen.getByLabelText('Network name')).toHaveValue('ExampleNet');
    expect(screen.getByLabelText('Password')).toHaveValue('');
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    fireEvent.click(screen.getByRole('button', { name: 'Forget' }));
    await waitFor(() => expect(matter.clearMatterWifi).toHaveBeenCalled());
    expect(await screen.findByTestId('matter-wifi-form')).toBeInTheDocument();
  });
});
