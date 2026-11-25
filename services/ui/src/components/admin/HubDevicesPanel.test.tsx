import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { HubDevicesPanel } from './HubDevicesPanel';
import { api } from '../../services/api';
import type { HubDevice, UserProfile } from '../../types/api';

vi.mock('../../services/api', () => ({
  api: {
    getHubDevices: vi.fn(),
    refreshHubDevices: vi.fn(),
    assignHubDevice: vi.fn(),
  },
}));
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }));

const device = (over: Partial<HubDevice>): HubDevice => ({
  device_key: 'hub:mac:02:00:00:00:00:01', hardware_id: 'mac:02:00:00:00:00:01', hub_device_id: 'd1',
  kind: 'voice_satellite', label: 'Test Satellite', entity_ids: ['assist_satellite.test'], owner_username: null,
  owner_name: null, assignment: 'shared', room: null, hub_area: 'Den', manufacturer: null, model: 'Sat',
  last_seen_at: null, archived_at: null, ...over,
});

const USERS = [
  { id: 1, username: 'default', display_name: 'Default', is_system_default: true },
  { id: 2, username: 'member-a', display_name: 'Member A' },
] as unknown as UserProfile[];

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><HubDevicesPanel users={USERS} /></QueryClientProvider>);
}

describe('HubDevicesPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getHubDevices).mockResolvedValue([
      device({}),
      device({ device_key: 'hub:cast:tv', kind: 'tv', label: 'Test TV' }),
    ]);
  });

  it('lists satellites first and offers people but never the default user', async () => {
    renderPanel();
    const rows = await screen.findAllByTestId('hub-device');
    expect(rows).toHaveLength(1);
    const select = within(rows[0]).getByLabelText('Who uses Test Satellite');
    const options = within(select).getAllByRole('option').map((o) => o.textContent);
    expect(options).toEqual(['Shared', 'Member A']);
    fireEvent.click(screen.getByRole('tab', { name: /All/ }));
    expect(await screen.findAllByTestId('hub-device')).toHaveLength(2);
  });

  it('assigns a device to a person and back to shared', async () => {
    vi.mocked(api.assignHubDevice).mockResolvedValue(device({ owner_username: 'member-a', owner_name: 'Member A', assignment: 'person' }));
    renderPanel();
    const select = await screen.findByLabelText('Who uses Test Satellite');
    fireEvent.change(select, { target: { value: 'member-a' } });
    await waitFor(() => expect(api.assignHubDevice).toHaveBeenCalledWith('hub:mac:02:00:00:00:00:01', { owner_username: 'member-a' }));
    await waitFor(() => expect((screen.getByLabelText('Who uses Test Satellite') as HTMLSelectElement).value).toBe('member-a'));
    vi.mocked(api.assignHubDevice).mockResolvedValue(device({}));
    fireEvent.change(screen.getByLabelText('Who uses Test Satellite'), { target: { value: '' } });
    await waitFor(() => expect(api.assignHubDevice).toHaveBeenLastCalledWith('hub:mac:02:00:00:00:00:01', { owner_username: null }));
  });

  it('saves a room only when it changed', async () => {
    vi.mocked(api.assignHubDevice).mockResolvedValue(device({ room: 'Bedroom' }));
    renderPanel();
    const input = await screen.findByLabelText('Room for Test Satellite');
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
    fireEvent.change(input, { target: { value: 'Bedroom' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(api.assignHubDevice).toHaveBeenCalledWith('hub:mac:02:00:00:00:00:01', { room: 'Bedroom' }));
  });

  it('reads HomeHub again on refresh', async () => {
    vi.mocked(api.refreshHubDevices).mockResolvedValue([device({ label: 'Renamed' })]);
    renderPanel();
    await screen.findAllByTestId('hub-device');
    fireEvent.click(screen.getByRole('button', { name: /Refresh from HomeHub/ }));
    expect(await screen.findByText('Renamed')).toBeTruthy();
  });

  it('says so when HomeHub has reported nothing', async () => {
    vi.mocked(api.getHubDevices).mockResolvedValue([]);
    renderPanel();
    expect(await screen.findByText(/No devices from HomeHub yet/)).toBeTruthy();
  });
});
