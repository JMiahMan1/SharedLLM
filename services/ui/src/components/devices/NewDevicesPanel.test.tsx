import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import NewDevicesPanel from './NewDevicesPanel';
import * as discovered from '../../lib/discoveredDevices';
import type { DiscoveredDevice } from '../../lib/discoveredDevices';

vi.mock('../../lib/discoveredDevices', async (importOriginal) => ({
  failureText: ((await importOriginal()) as Record<string, unknown>).failureText,
  groupFlows: ((await importOriginal()) as Record<string, unknown>).groupFlows,
  getDiscovered: vi.fn(),
  scanForDevices: vi.fn(),
  adoptDevice: vi.fn(),
  ignoreDevice: vi.fn(),
  commissionMatter: vi.fn(),
  getManualFlows: vi.fn(),
  addByAddress: vi.fn(),
  getEntries: vi.fn().mockResolvedValue({ areas: [{ area_id: 'den', name: 'Den' }], entries: [] }),
}));
vi.mock('react-hot-toast', () => {
  const toast = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() });
  return { default: toast };
});

const base: DiscoveredDevice = {
  id: 'esphome:aabbccddee01', integration: 'esphome', label: 'ESPHome', name: 'Porch Bulb', model: 'Example.RGBWW',
  manufacturer: 'Espressif', host: '192.0.2.34', mac: 'aabbccddee01', source: 'mdns', first_seen: null,
  last_seen: null, known: false, entry_id: null, entry_title: null, ignored: false, new: true, adoptable: true,
  unsupported: null,
  needs: [{ key: 'noise_psk', label: 'Encryption key', type: 'password', help: 'From the YAML.' }],
};
const tv: DiscoveredDevice = {
  ...base, id: 'samsungtv:x1', integration: 'samsungtv', label: 'Samsung TV', name: 'Den TV', host: '192.0.2.70',
  adoptable: false, unsupported: 'Needs accepting on the TV, which is not supported yet.', needs: [],
};
const old: DiscoveredDevice = { ...base, id: 'wiz:aabbcc0a1b2c', name: 'Old Bulb', label: 'WiZ', ignored: true, new: false, needs: [] };

const wrap = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <NewDevicesPanel />
    </QueryClientProvider>,
  );

describe('NewDevicesPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(discovered.getDiscovered).mockResolvedValue({ devices: [base, tv, old] });
  });

  it('lists new devices, offers Add only where the hub can add them, and says why not', async () => {
    wrap();
    const rows = await screen.findAllByTestId('new-device');
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByRole('button', { name: 'Add Porch Bulb' })).toBeInTheDocument();
    expect(within(rows[1]).queryByRole('button', { name: /^Add/ })).toBeNull();
    expect(within(rows[1]).getByText(/not supported yet/)).toBeInTheDocument();
    expect(screen.queryByText('Old Bulb · WiZ')).toBeNull();
  });

  it('asks for the key, shows the hub\'s refusal, and adds on the right key', async () => {
    vi.mocked(discovered.adoptDevice)
      .mockResolvedValueOnce({ status: 'needs_input', message: 'Enter the node\'s encryption key.', error: 'That key was not accepted.',
        needs: base.needs })
      .mockResolvedValueOnce({ status: 'adopted', entry_id: 'e1', name: 'Porch Bulb', entities: ['light.porch_bulb'] });
    wrap();
    fireEvent.click(await screen.findByRole('button', { name: 'Add Porch Bulb' }));
    const dialog = await screen.findByTestId('adopt-dialog');
    const add = within(dialog).getByRole('button', { name: /Add/ });
    expect(add).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText(/Encryption key/), { target: { value: 'wrong' } });
    fireEvent.click(add);
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('That key was not accepted.');
    fireEvent.change(within(dialog).getByLabelText(/Encryption key/), { target: { value: 'right' } });
    fireEvent.change(within(dialog).getByLabelText(/Name/), { target: { value: 'Front porch' } });
    fireEvent.click(add);
    await waitFor(() => expect(screen.queryByTestId('adopt-dialog')).toBeNull());
    expect(discovered.adoptDevice).toHaveBeenLastCalledWith('esphome:aabbccddee01',
      { name: 'Front porch', input: { noise_psk: 'right' } });
  });

  it('ignores, un-ignores and scans', async () => {
    vi.mocked(discovered.ignoreDevice).mockResolvedValue({ ...base, ignored: true });
    vi.mocked(discovered.scanForDevices).mockResolvedValue({ devices: [] });
    wrap();
    fireEvent.click(await screen.findByRole('button', { name: 'Ignore Porch Bulb' }));
    await waitFor(() => expect(discovered.ignoreDevice).toHaveBeenCalledWith('esphome:aabbccddee01', true));
    fireEvent.click(screen.getByRole('button', { name: /Show ignored/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Stop ignoring Old Bulb' }));
    await waitFor(() => expect(discovered.ignoreDevice).toHaveBeenCalledWith('wiz:aabbcc0a1b2c', false));
    fireEvent.click(screen.getByRole('button', { name: /Scan now/ }));
    await waitFor(() => expect(discovered.scanForDevices).toHaveBeenCalled());
    expect(await screen.findByTestId('new-devices-empty')).toBeInTheDocument();
  });

  it('adds a Matter device by its setup code', async () => {
    vi.mocked(discovered.commissionMatter).mockResolvedValue({ status: 'adopted', entry_id: 'm1', name: 'Smart Bulb' });
    wrap();
    fireEvent.click(await screen.findByRole('button', { name: /Add Matter device/ }));
    const dialog = await screen.findByTestId('adopt-dialog');
    fireEvent.change(within(dialog).getByLabelText(/Setup code/), { target: { value: '3497-011-2332' } });
    fireEvent.click(within(dialog).getByRole('button', { name: /Add/ }));
    await waitFor(() => expect(discovered.commissionMatter).toHaveBeenCalledWith('3497-011-2332', undefined, undefined));
    await waitFor(() => expect(screen.queryByTestId('adopt-dialog')).toBeNull());
  });

  it('puts a new Matter device straight into the chosen room', async () => {
    vi.mocked(discovered.commissionMatter).mockResolvedValue({ status: 'adopted', entry_id: 'm1', name: 'Smart Bulb' });
    wrap();
    fireEvent.click(await screen.findByRole('button', { name: /Add Matter device/ }));
    const dialog = await screen.findByTestId('adopt-dialog');
    // The rooms come from the hub, so the picker appears once they arrive.
    fireEvent.change(await within(dialog).findByLabelText(/Room/), { target: { value: 'den' } });
    fireEvent.change(within(dialog).getByLabelText(/Setup code/), { target: { value: '3497-011-2332' } });
    fireEvent.click(within(dialog).getByRole('button', { name: /^Add$/ }));
    await waitFor(() => expect(discovered.commissionMatter).toHaveBeenCalledWith('3497-011-2332', undefined, 'den'));
  });

  it('adds a device by address after the TV is accepted', async () => {
    vi.mocked(discovered.getManualFlows).mockResolvedValue([
      { domain: 'samsungtv', label: 'Samsung TV', fields: [{ key: 'host', label: 'Address', type: 'text' }] },
    ]);
    vi.mocked(discovered.addByAddress)
      .mockResolvedValueOnce({ status: 'pending', session: 's1', step: 'accept_on_tv', message: 'Choose Allow on the TV.', poll_ms: 10 })
      .mockResolvedValueOnce({ status: 'adopted', entry_id: 'tv1', name: 'Den TV', entities: ['media_player.den_tv'] });
    wrap();
    fireEvent.click(await screen.findByRole('button', { name: /Add by address/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'Samsung TV' }));
    const dialog = await screen.findByTestId('adopt-dialog');
    fireEvent.change(within(dialog).getByLabelText(/Address/), { target: { value: '192.0.2.70' } });
    fireEvent.click(within(dialog).getByRole('button', { name: /Add/ }));
    expect(await within(dialog).findByText('Accept on your TV')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByTestId('adopt-dialog')).toBeNull());
    expect(discovered.addByAddress).toHaveBeenNthCalledWith(1, 'samsungtv',
      { name: undefined, area_id: undefined, input: { host: '192.0.2.70' }, host: '192.0.2.70', url: undefined });
    expect(discovered.addByAddress).toHaveBeenNthCalledWith(2, 'samsungtv',
      { name: undefined, area_id: undefined, input: { host: '192.0.2.70', session: 's1' }, host: '192.0.2.70', url: undefined });
  });
});
