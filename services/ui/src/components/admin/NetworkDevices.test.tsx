import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import NetworkDevices from './NetworkDevices';
import { api } from '../../services/api';
import { confirmDialog } from '../ui/confirmDialog';
import type { NetworkDevice } from '../../lib/network';

vi.mock('../../services/api', () => ({
  api: {
    getNetworkDevices: vi.fn(),
    updateNetworkDevice: vi.fn(async () => ({ status: 'ok' })),
    forgetNetworkDevice: vi.fn(async () => ({ status: 'ok' })),
    getUsers: vi.fn(async () => [{
      id: '1', username: 'owner-a', display_name: 'Test Owner', role: 'user', is_admin: false,
    }]),
  },
}));
vi.mock('../ui/confirmDialog', () => ({ confirmDialog: vi.fn(async () => true) }));
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }));

const NOW = Math.floor(Date.now() / 1000);

const DEVICES: NetworkDevice[] = [
  {
    mac: '02:00:00:00:00:01', alias: 'Desk lamp', kind: 'smart_home', make: 'Example Make', model: 'Model A', os: null,
    owner: 'owner-a', notes: null, first_seen: NOW - 30 * 86400, last_seen: NOW - 20, private_mac: false,
    kind_source: 'manual', make_source: 'dns', model_source: 'dns', os_source: null,
    ip: '192.168.10.11', online: true, band: '2.4 GHz',
  },
  {
    mac: '02:00:00:00:00:02', alias: null, kind: 'phone', make: 'Test Make', model: 'Handset', os: 'TestOS',
    owner: null, notes: null, first_seen: NOW - 2 * 86400, last_seen: NOW - 600, private_mac: true,
    kind_source: 'user_agent', make_source: 'user_agent', model_source: 'user_agent', os_source: 'dns',
    ip: '192.168.10.12', online: false, band: null,
  },
  {
    mac: '02:00:00:00:00:03', alias: null, kind: null, make: null, model: null, os: null,
    owner: null, notes: null, first_seen: NOW - 3 * 86400, last_seen: null, private_mac: false,
    kind_source: null, make_source: null, model_source: null, os_source: null,
    ip: null, online: false, band: null,
  },
];

function renderDevices(initialEntry = '/admin/network') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <NetworkDevices />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('NetworkDevices', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getNetworkDevices).mockResolvedValue({ devices: DEVICES });
    vi.mocked(confirmDialog).mockResolvedValue(true);
  });

  it('lists devices by their best name, marking new and private addresses', async () => {
    renderDevices();
    expect(await screen.findByRole('button', { name: 'Edit Desk lamp' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Edit Example Make/ })).toBeNull();
    expect(screen.getAllByText('New').length).toBe(3);
    expect(screen.getByText('Private address')).toBeTruthy();
    expect(screen.getAllByText(/last seen/).length).toBe(2);
  });

  it('filters by new and unknown kind and searches the list', async () => {
    renderDevices();
    await screen.findByRole('button', { name: 'Edit Desk lamp' });

    fireEvent.click(screen.getByRole('button', { name: 'New' }));
    expect(screen.getByRole('button', { name: 'Edit Test Make Handset' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Edit Desk lamp' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Unknown kind' }));
    expect(screen.getByRole('button', { name: 'Edit 02:00:00:00:00:03' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Edit Test Make Handset' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'All' }));
    fireEvent.change(screen.getByLabelText('Search devices'), { target: { value: 'Desk' } });
    expect(screen.getByRole('button', { name: 'Edit Desk lamp' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Edit Test Make Handset' })).toBeNull();
  });

  it('saves only the field that changed and shows where detected details came from', async () => {
    renderDevices();
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Test Make Handset' }));
    const dialog = await screen.findByRole('dialog', { name: 'Edit device' });
    expect(within(dialog).getAllByText(/detected from its browser/).length).toBe(3);
    expect(within(dialog).getByText(/from DNS/)).toBeTruthy();

    fireEvent.change(within(dialog).getByLabelText('Alias'), { target: { value: 'Desk handset' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(api.updateNetworkDevice).toHaveBeenCalledWith(
      '02:00:00:00:00:02', { alias: 'Desk handset' },
    ));
  });

  it('clears a manual detail back to detection with an empty value', async () => {
    renderDevices();
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Desk lamp' }));
    const dialog = await screen.findByRole('dialog', { name: 'Edit device' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Clear Make' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(api.updateNetworkDevice).toHaveBeenCalledWith(
      '02:00:00:00:00:01', { make: '' },
    ));
  });

  it('asks before forgetting a device', async () => {
    renderDevices();
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Desk lamp' }));
    const dialog = await screen.findByRole('dialog', { name: 'Edit device' });

    vi.mocked(confirmDialog).mockResolvedValueOnce(false);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Forget device' }));
    await waitFor(() => expect(confirmDialog).toHaveBeenCalledTimes(1));
    expect(api.forgetNetworkDevice).not.toHaveBeenCalled();

    vi.mocked(confirmDialog).mockResolvedValueOnce(true);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Forget device' }));
    await waitFor(() => expect(api.forgetNetworkDevice).toHaveBeenCalledWith('02:00:00:00:00:01'));
  });

  it('opens the linked device from the new-device notice', async () => {
    renderDevices('/admin/network?device=02%3A00%3A00%3A00%3A00%3A02');
    const dialog = await screen.findByRole('dialog', { name: 'Edit device' });
    expect(within(dialog).getByText('02:00:00:00:00:02')).toBeTruthy();
    expect(within(dialog).getByLabelText('Alias')).toBeTruthy();
  });
});
