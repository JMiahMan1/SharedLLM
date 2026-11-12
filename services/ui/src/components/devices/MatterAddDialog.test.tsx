import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import MatterAddDialog from './MatterAddDialog';
import * as discovered from '../../lib/discoveredDevices';
import * as matter from '../../lib/matterCommissioning';

vi.mock('../../lib/discoveredDevices', async (importOriginal) => ({
  ...((await importOriginal()) as Record<string, unknown>),
  commissionMatter: vi.fn(),
}));
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

const READY = {
  bluetooth: { enabled: true, setting: 'MATTER_BLUETOOTH_ADAPTER' },
  wifi: { configured: true, ssid: 'ExampleNet', updated_at: '2030-01-01T00:00:00+00:00' },
};

function open() {
  const onDone = vi.fn();
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MatterAddDialog areas={[]} onDone={onDone} onClose={vi.fn()} />
    </QueryClientProvider>,
  );
  return { onDone, dialog: screen.getByTestId('adopt-dialog') };
}

describe('MatterAddDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(discovered.commissionMatter).mockReset();
    vi.mocked(matter.getMatterSettings).mockResolvedValue(READY);
  });

  it('defaults to a device already on the network and does not ask the hub about Bluetooth', async () => {
    vi.mocked(discovered.commissionMatter).mockResolvedValue({ status: 'adopted', entry_id: 'm1', name: 'Smart Bulb' });
    const { onDone, dialog } = open();
    expect(within(dialog).getByRole('radio', { name: /On the network/ })).toHaveAttribute('aria-checked', 'true');
    fireEvent.change(within(dialog).getByLabelText(/Setup code/), { target: { value: 'MT:Y.K9042C00KCS.U20' } });
    fireEvent.click(within(dialog).getByRole('button', { name: /^Add$/ }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(discovered.commissionMatter).toHaveBeenCalledWith('MT:Y.K9042C00KCS.U20', undefined, undefined);
    expect(matter.getMatterSettings).not.toHaveBeenCalled();
  });

  it('adds a new device over Bluetooth, showing each step, and sends the session (not the Wi-Fi) while it runs', async () => {
    vi.mocked(discovered.commissionMatter)
      .mockResolvedValueOnce({ status: 'pending', session: 'b1', step: 'matter_ble_commission', poll_ms: 300,
        message: 'Finding the device over Bluetooth (0:03 so far).' })
      .mockResolvedValueOnce({ status: 'pending', session: 'b1', step: 'matter_ble_register', poll_ms: 5,
        message: 'The device joined the network; adding its controls.' })
      .mockResolvedValueOnce({ status: 'adopted', entry_id: 'm1', name: 'Smart Bulb' });
    const { onDone, dialog } = open();
    fireEvent.click(within(dialog).getByRole('radio', { name: /over Bluetooth/ }));
    expect(await within(dialog).findByTestId('matter-ble-network')).toHaveTextContent('ExampleNet');
    expect(within(dialog).getByTestId('matter-bluetooth')).toHaveTextContent('ready');
    fireEvent.change(within(dialog).getByLabelText(/Setup code/), { target: { value: '3497-011-2332' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add over Bluetooth' }));
    expect(await within(dialog).findByText('Adding over Bluetooth')).toBeInTheDocument();
    expect(within(dialog).getByTestId('adopt-waiting')).toHaveTextContent('0:03 so far');
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(discovered.commissionMatter).toHaveBeenNthCalledWith(1, '3497-011-2332', undefined, undefined, { method: 'ble' });
    expect(discovered.commissionMatter).toHaveBeenNthCalledWith(2, '3497-011-2332', undefined, undefined,
      { method: 'ble', session: 'b1' });
  });

  it('a network for this device only is sent once, to start', async () => {
    vi.mocked(discovered.commissionMatter)
      .mockResolvedValueOnce({ status: 'pending', session: 'b2', step: 'matter_ble_prepare', poll_ms: 5 })
      .mockResolvedValueOnce({ status: 'adopted', entry_id: 'm1' });
    const { onDone, dialog } = open();
    fireEvent.click(within(dialog).getByRole('radio', { name: /over Bluetooth/ }));
    fireEvent.click(await within(dialog).findByLabelText('Use another network for this device'));
    fireEvent.change(within(dialog).getByLabelText('Network name'), { target: { value: 'OtherNet' } });
    fireEvent.change(within(dialog).getByLabelText('Network password'), { target: { value: 'other-passphrase' } });
    fireEvent.change(within(dialog).getByLabelText(/Setup code/), { target: { value: '34970112332' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add over Bluetooth' }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(discovered.commissionMatter).toHaveBeenNthCalledWith(1, '34970112332', undefined, undefined,
      { method: 'ble', wifi_ssid: 'OtherNet', wifi_password: 'other-passphrase' });
    expect(discovered.commissionMatter).toHaveBeenNthCalledWith(2, '34970112332', undefined, undefined,
      { method: 'ble', session: 'b2' });
  });

  it('says when Bluetooth is off, offers to set the Wi-Fi, and shows the hub\'s error', async () => {
    vi.mocked(matter.getMatterSettings).mockResolvedValue({
      bluetooth: { enabled: false, setting: 'MATTER_BLUETOOTH_ADAPTER' },
      wifi: { configured: false, ssid: null, updated_at: null },
    });
    vi.mocked(discovered.commissionMatter).mockResolvedValue({ status: 'error',
      message: 'The device was not found over Bluetooth. Check that it is powered.' });
    const { dialog } = open();
    fireEvent.click(within(dialog).getByRole('radio', { name: /over Bluetooth/ }));
    await waitFor(() => expect(within(dialog).getByTestId('matter-bluetooth')).toHaveTextContent('MATTER_BLUETOOTH_ADAPTER'));
    expect(within(dialog).getByTestId('matter-wifi-form')).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText(/Setup code/), { target: { value: '34970112332' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add over Bluetooth' }));
    expect(await within(dialog).findByText(/not found over Bluetooth/)).toBeInTheDocument();
  });
});
