import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import HardwarePanel from './HardwarePanel';
import * as hardware from '../../lib/hardware';
import * as discovered from '../../lib/discoveredDevices';
import * as dashboards from '../../lib/dashboards';
import { api } from '../../services/api';
import toast from 'react-hot-toast';
import type { HardwareDetail, HardwareDevice } from '../../lib/hardware';

vi.mock('../../lib/hardware', async (importOriginal) => ({
  ...((await importOriginal()) as Record<string, unknown>),
  getHardware: vi.fn(),
  getHardwareDevice: vi.fn(),
  setDeviceDisabled: vi.fn(),
  updateEntity: vi.fn(),
}));
vi.mock('../../lib/discoveredDevices', async (importOriginal) => ({
  ...((await importOriginal()) as Record<string, unknown>),
  updateHubDevice: vi.fn(),
  removeHubDevice: vi.fn(),
}));
vi.mock('../../lib/dashboards', async (importOriginal) => ({
  ...((await importOriginal()) as Record<string, unknown>),
  runService: vi.fn().mockResolvedValue({}),
  getRooms: vi.fn(),
  getHistory: vi.fn().mockResolvedValue({}),
}));
vi.mock('../../services/api', async (importOriginal) => {
  const real = (await importOriginal()) as { api: Record<string, unknown> };
  return { ...real, api: { ...real.api, callHaService: vi.fn() } };
});
vi.mock('react-hot-toast', () => {
  const toast = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() });
  return { default: toast };
});

const base: HardwareDevice = {
  id: 'd-lamp', name: 'Porch lamp', original_name: 'Porch lamp', name_by_user: null, manufacturer: 'WiZ',
  model: 'Bulb', sw_version: '1.36', hw_version: null, serial_number: null, area_id: 'porch', area: 'Porch',
  domain: 'wiz', label: 'WiZ', entry_id: 'e1', entry_title: 'Porch lamp', entry_state: 'loaded', entry_reason: null,
  connections: [{ type: 'ip', value: '192.0.2.21' }, { type: 'mac', value: '02:00:00:00:00:21' }],
  configuration_url: null, via_device_id: null, via_device_name: null, disabled_by: null, entities: 2, unavailable: 0,
  status: 'online', last_seen: new Date().toISOString(), abilities: ['dim', 'power', 'sense'],
};
const tv: HardwareDevice = { ...base, id: 'd-tv', name: 'Den TV', manufacturer: 'Roku', model: 'Ultra', area_id: 'den',
  area: 'Den', domain: 'roku', label: 'Roku', status: 'offline', unavailable: 2, abilities: ['play_media'] };

const detail: HardwareDetail = {
  ...base, children: [], entry: null,
  entity_list: [
    { entity_id: 'light.porch_lamp', domain: 'light', platform: 'wiz', name: 'Porch lamp', name_by_user: null,
      original_name: null, entity_category: null, disabled_by: null, hidden_by: null, area_id: null, icon: null,
      voice: { exposed: true, default: true, chosen: false },
      capabilities: { abilities: ['dim', 'power'], services: ['light.turn_on'], details: {}, protocol: {} },
      state: { entity_id: 'light.porch_lamp', domain: 'light', name: 'Porch lamp', state: 'on',
        attributes: { brightness: 128 }, device_class: null, unit: null, icon: null, area_id: 'porch',
        device_id: 'd-lamp', last_changed: '', abilities: ['dim', 'power'] } },
    { entity_id: 'sensor.porch_lamp_power', domain: 'sensor', platform: 'wiz', name: 'Porch lamp Power',
      name_by_user: null, original_name: 'Power', entity_category: 'diagnostic', disabled_by: null, hidden_by: null,
      area_id: null, icon: null, capabilities: null, state: null },
  ],
};

const wrap = () => render(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <HardwarePanel />
  </QueryClientProvider>,
);

const lampState = detail.entity_list[0].state!;
const rooms = (lamp = lampState) => ({ floors: [], unassigned: [], areas: [
  { area_id: 'porch', name: 'Porch', floor_id: null, icon: null, entities: [lamp] },
] });

describe('HardwarePanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(hardware.getHardware).mockResolvedValue({ devices: [{ ...base, kind: 'light' }, { ...tv, kind: 'media' }],
      areas: [{ area_id: 'den', name: 'Den' }, { area_id: 'porch', name: 'Porch' }] });
    vi.mocked(hardware.getHardwareDevice).mockResolvedValue(detail);
    vi.mocked(dashboards.getRooms).mockResolvedValue(rooms());
  });

  it('draws devices as tiles by room, each with a plain state line', async () => {
    wrap();
    await screen.findByRole('button', { name: 'Porch lamp, On, 50%' });
    expect(screen.getAllByTestId('device-tile')).toHaveLength(2);
    expect(screen.getAllByTestId('hardware-group').map((g) => within(g).getByRole('heading', { level: 3 }).textContent))
      .toEqual(['Den 1', 'Porch 1']);
    expect(screen.getByRole('button', { name: /^Den TV, Offline/ })).toBeInTheDocument();
    // No capability words or addresses on a tile.
    expect(screen.queryByText('Dim')).toBeNull();
    expect(screen.queryByText(/192\.0\.2\.21/)).toBeNull();
  });

  it('searches, filters by type and needs attention, and regroups', async () => {
    wrap();
    await screen.findByRole('button', { name: 'Porch lamp, On, 50%' });
    fireEvent.change(screen.getByLabelText('Search devices'), { target: { value: 'roku' } });
    expect(screen.getAllByTestId('device-tile')).toHaveLength(1);
    fireEvent.change(screen.getByLabelText('Search devices'), { target: { value: '' } });
    const filters = screen.getByTestId('hardware-filters');
    fireEvent.click(within(filters).getByRole('button', { name: 'Needs attention (1)' }));
    expect(screen.getAllByTestId('device-tile')).toHaveLength(1);
    fireEvent.click(within(filters).getByRole('button', { name: /^All/ }));
    fireEvent.click(within(filters).getByRole('button', { name: /^Lights/ }));
    expect(screen.getAllByTestId('device-tile')).toHaveLength(1);
    fireEvent.click(within(filters).getByRole('button', { name: /^All/ }));
    fireEvent.click(screen.getByRole('radio', { name: 'Integrations' }));
    expect(screen.getAllByTestId('hardware-group').map((g) => within(g).getByRole('heading').textContent))
      .toEqual(['Roku 1', 'WiZ 1']);
  });

  it('toggles from a tile, drawing the change at once', async () => {
    vi.mocked(api.callHaService).mockResolvedValue({ status: 'SUCCESS' });
    wrap();
    const power = await screen.findByRole('switch', { name: 'Porch lamp power' });
    expect(power).toHaveAttribute('aria-checked', 'true');
    fireEvent.click(power);
    expect(power).toHaveAttribute('aria-checked', 'false');
    await waitFor(() => expect(api.callHaService).toHaveBeenCalledWith('light', 'turn_off', 'light.porch_lamp', null));
  });

  it('rolls a refused command back and shows the hub\'s words', async () => {
    vi.mocked(api.callHaService).mockRejectedValue(new Error('Porch lamp did not answer'));
    wrap();
    const power = await screen.findByRole('switch', { name: 'Porch lamp power' });
    fireEvent.click(power);
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Porch lamp did not answer', expect.anything()));
    expect(power).toHaveAttribute('aria-checked', 'true');
    expect(toast.success).not.toHaveBeenCalled();
  });

  it('opens a device with its control first and renames an entity under Device info', async () => {
    vi.mocked(hardware.updateEntity).mockResolvedValue(detail.entity_list[0]);
    wrap();
    fireEvent.click(await screen.findByRole('button', { name: 'Porch lamp, On, 50%' }));
    const sheet = await screen.findByTestId('hardware-detail');
    expect(within(sheet).getByRole('slider', { name: 'Porch lamp brightness' })).toBeInTheDocument();
    expect(within(sheet).getByText('02:00:00:00:00:21')).not.toBeVisible();
    fireEvent.click(within(sheet).getByRole('button', { name: 'Rename Porch lamp' }));
    fireEvent.change(within(sheet).getByLabelText('New name for Porch lamp'), { target: { value: 'Front lamp' } });
    fireEvent.click(within(within(sheet).getByTestId('device-info')).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(hardware.updateEntity).toHaveBeenCalledWith('light.porch_lamp', { name: 'Front lamp' }));
    fireEvent.click(within(sheet).getByRole('button', { name: 'Hide Porch lamp Power' }));
    await waitFor(() => expect(hardware.updateEntity).toHaveBeenCalledWith('sensor.porch_lamp_power', { disabled: true }));
  });

  it('switches whether voice and chat may control an entity', async () => {
    vi.mocked(hardware.updateEntity).mockResolvedValue(detail.entity_list[0]);
    wrap();
    fireEvent.click(await screen.findByRole('button', { name: 'Porch lamp, On, 50%' }));
    const sheet = await screen.findByTestId('hardware-detail');
    const toggle = within(sheet).getByRole('switch', { name: 'Voice can control Porch lamp' });
    expect(toggle).toHaveAttribute('aria-checked', 'true');
    fireEvent.click(toggle);
    await waitFor(() => expect(hardware.updateEntity).toHaveBeenCalledWith('light.porch_lamp', { voice: false }));
    // An entity the hub reports no setting for (an older hub) shows no switch.
    expect(within(sheet).queryByRole('switch', { name: 'Voice can control Porch lamp Power' })).toBeNull();
  });

  it('moves, turns off and removes the device from its sheet, asking before removing', async () => {
    vi.mocked(discovered.updateHubDevice).mockResolvedValue({} as never);
    vi.mocked(hardware.setDeviceDisabled).mockResolvedValue(base);
    vi.mocked(discovered.removeHubDevice).mockResolvedValue({ status: 'removed' });
    wrap();
    fireEvent.click(await screen.findByRole('button', { name: 'Porch lamp, On, 50%' }));
    const sheet = await screen.findByTestId('hardware-detail');
    fireEvent.change(within(sheet).getByLabelText('Room of Porch lamp'), { target: { value: 'den' } });
    await waitFor(() => expect(discovered.updateHubDevice).toHaveBeenCalledWith('d-lamp', { area_id: 'den' }));
    fireEvent.click(within(sheet).getByRole('button', { name: 'Turn off in the hub' }));
    await waitFor(() => expect(hardware.setDeviceDisabled).toHaveBeenCalledWith('d-lamp', true));
    fireEvent.click(within(sheet).getByRole('button', { name: 'Remove device' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(discovered.removeHubDevice).toHaveBeenCalledWith('d-lamp', false));
  });

  it('shows one tile per physical device, and folds away what is not a device', async () => {
    const roku: HardwareDevice = { ...tv, id: 'd-roku', status: 'online', unavailable: 0, kind: 'media', group_id: 'd-roku' };
    const player: HardwareDevice = { ...tv, id: 'd-player', domain: 'music_assistant', label: 'Music Assistant',
      area_id: null, area: null, status: 'partial', kind: 'media', group_id: 'd-roku' };
    const addon: HardwareDevice = { ...base, id: 'd-addon', name: 'File editor', domain: 'hassio', label: 'Supervisor',
      service: true, group_id: 'd-addon' };
    vi.mocked(hardware.getHardware).mockResolvedValue({ devices: [{ ...base, kind: 'light' }, roku, player, addon],
      areas: [{ area_id: 'den', name: 'Den' }, { area_id: 'porch', name: 'Porch' }] });
    wrap();
    await screen.findByRole('button', { name: 'Porch lamp, On, 50%' });
    expect(screen.getAllByTestId('device-tile')).toHaveLength(2);
    expect(within(screen.getByTestId('hardware-services')).getByText(/Not devices/)).toBeInTheDocument();
  });

  it('puts a device with no room into one from Needs attention, every view of it', async () => {
    vi.mocked(discovered.updateHubDevice).mockResolvedValue({} as never);
    const lost: HardwareDevice = { ...base, id: 'd-a', name: 'Hall bulb', area_id: null, area: null, kind: 'light', group_id: 'd-a' };
    const lostToo: HardwareDevice = { ...lost, id: 'd-b', domain: 'matter', label: 'Matter', group_id: 'd-a' };
    vi.mocked(hardware.getHardware).mockResolvedValue({ devices: [lost, lostToo, tv],
      areas: [{ area_id: 'den', name: 'Den' }, { area_id: 'porch', name: 'Porch' }] });
    wrap();
    fireEvent.click(await screen.findByRole('button', { name: 'Needs attention (2)' }));
    const list = screen.getByTestId('no-room-list');
    fireEvent.change(within(list).getByLabelText('Room for Hall bulb'), { target: { value: 'porch' } });
    await waitFor(() => expect(discovered.updateHubDevice).toHaveBeenCalledWith('d-b', { area_id: 'porch' }));
    expect(discovered.updateHubDevice).toHaveBeenCalledWith('d-a', { area_id: 'porch' });
  });

  it('says when the hub has nothing', async () => {
    vi.mocked(hardware.getHardware).mockResolvedValue({ devices: [], areas: [] });
    wrap();
    expect(await screen.findByTestId('hardware-empty')).toHaveTextContent('No devices yet');
  });
});
