import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { AxiosError, AxiosHeaders } from 'axios';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import IntegrationsPanel from './IntegrationsPanel';
import * as hub from '../../lib/discoveredDevices';
import type { EntriesList } from '../../lib/discoveredDevices';

vi.mock('../../lib/discoveredDevices', async (importOriginal) => {
  const real = (await importOriginal()) as Record<string, unknown>;
  return {
    failureText: real.failureText,
    failureStatus: real.failureStatus,
    getEntries: vi.fn(),
    getBuilderLink: vi.fn(),
    getManualFlows: vi.fn(),
    addByAddress: vi.fn(),
    reconfigureEntry: vi.fn(),
    removeEntry: vi.fn(),
    removeHubDevice: vi.fn(),
    updateHubDevice: vi.fn(),
  };
});
vi.mock('react-hot-toast', () => {
  const toast = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() });
  return { default: toast };
});

const LIST: EntriesList = {
  areas: [{ area_id: 'den', name: 'Den' }, { area_id: 'porch', name: 'Porch' }],
  entries: [
    {
      entry_id: 'hk1', domain: 'homekit_controller', label: 'HomeKit', title: 'Porch Bridge', state: 'loaded', reason: null,
      source: 'zeroconf', disabled_by: null, entities: 3, unpairs: true, shared: false,
      reconfigure: [],
      devices: [
        { id: 'd1', name: 'Porch Bridge', original_name: 'Porch Bridge', name_by_user: null, area_id: 'porch',
          manufacturer: 'Example Co', model: 'Bridge', via_device_id: null, entities: ['button.porch_bridge_identify'] },
        { id: 'd2', name: 'Porch Light', original_name: 'Porch Light', name_by_user: null, area_id: null,
          manufacturer: 'Example Co', model: 'Bulb', via_device_id: 'd1', entities: ['light.porch_light'] },
      ],
    },
    {
      entry_id: 'rl1', domain: 'reolink', label: 'Reolink', title: 'Camera Hub', state: 'setup_retry', reason: 'login failed',
      source: 'user', disabled_by: null, entities: 0, unpairs: false, shared: false,
      reconfigure: [
        { key: 'host', label: 'Address', type: 'text', value: '192.0.2.90' },
        { key: 'password', label: 'Password', type: 'password', set: true },
      ],
      devices: [],
    },
  ],
};

const wrap = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <IntegrationsPanel />
    </QueryClientProvider>,
  );

function unreachable() {
  return new AxiosError('bad gateway', '502', undefined, undefined, {
    status: 502, statusText: 'Bad Gateway', headers: {}, config: { headers: new AxiosHeaders() },
    data: { detail: 'Could not unpair it from the device: timed out' },
  });
}

describe('IntegrationsPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(hub.getEntries).mockResolvedValue(LIST);
    vi.mocked(hub.getBuilderLink).mockResolvedValue({ configured: false });
  });

  it('lists integrations; the builder link appears only when configured', async () => {
    wrap();
    const rows = await screen.findAllByTestId('integration');
    expect(rows).toHaveLength(2);
    expect(within(rows[1]).getByText('login failed')).toBeInTheDocument();
    expect(screen.queryByTestId('esphome-builder-link')).toBeNull();
    expect(await screen.findByRole('button', { name: /Set up ESPHome Builder/ })).toBeInTheDocument();
  });

  it('shows the builder link from its integration', async () => {
    vi.mocked(hub.getBuilderLink).mockResolvedValue({ configured: true, url: 'http://192.0.2.5:6052/' });
    wrap();
    const link = await screen.findByTestId('esphome-builder-link');
    expect(link).toHaveAttribute('href', 'http://192.0.2.5:6052/');
    expect(link).toHaveAttribute('target', '_blank');
  });

  it('renames and moves a device', async () => {
    vi.mocked(hub.updateHubDevice).mockResolvedValue(LIST.entries[0].devices[1]);
    wrap();
    fireEvent.click(await screen.findByText('Porch Bridge', { selector: 'span' }));
    fireEvent.change(await screen.findByLabelText('Area of Porch Light'), { target: { value: 'den' } });
    await waitFor(() => expect(hub.updateHubDevice).toHaveBeenCalledWith('d2', { area_id: 'den' }));
    fireEvent.click(screen.getByRole('button', { name: 'Rename Porch Light' }));
    const dialog = await screen.findByTestId('rename-dialog');
    fireEvent.change(within(dialog).getByLabelText(/Name/), { target: { value: 'Front light' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(hub.updateHubDevice).toHaveBeenCalledWith('d2', { name_by_user: 'Front light' }));
  });

  it('reconfigures with only what changed and never echoes a secret', async () => {
    vi.mocked(hub.reconfigureEntry).mockResolvedValue(LIST.entries[1]);
    wrap();
    fireEvent.click(await screen.findByRole('button', { name: 'Reconfigure Camera Hub' }));
    const dialog = await screen.findByTestId('reconfigure-dialog');
    expect(within(dialog).getByLabelText('Password')).toHaveValue('');
    fireEvent.change(within(dialog).getByLabelText('Address'), { target: { value: '192.0.2.91' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(hub.reconfigureEntry).toHaveBeenCalledWith('rl1', { data: { host: '192.0.2.91' } }));
  });

  it('unpairs, and offers to remove anyway when the accessory cannot be reached', async () => {
    vi.mocked(hub.removeEntry).mockRejectedValueOnce(unreachable()).mockResolvedValueOnce({ status: 'removed', unpaired: false });
    wrap();
    fireEvent.click(await screen.findByRole('button', { name: 'Remove Porch Bridge' }));
    const dialog = await screen.findByTestId('remove-dialog');
    expect(dialog).toHaveTextContent('unpaired');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Remove' }));
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('Could not unpair');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Remove anyway' }));
    await waitFor(() => expect(hub.removeEntry).toHaveBeenLastCalledWith('hk1', true));
    await waitFor(() => expect(screen.queryByTestId('remove-dialog')).toBeNull());
  });
});
