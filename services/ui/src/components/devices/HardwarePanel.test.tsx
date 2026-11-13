import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import HardwarePanel from './HardwarePanel';
import * as hardware from '../../lib/hardware';
import * as discovered from '../../lib/discoveredDevices';
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
}));
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

describe('HardwarePanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(hardware.getHardware).mockResolvedValue({ devices: [base, tv],
      areas: [{ area_id: 'den', name: 'Den' }, { area_id: 'porch', name: 'Porch' }] });
    vi.mocked(hardware.getHardwareDevice).mockResolvedValue(detail);
  });

  it('lists devices from the registry, grouped by room, with status and abilities', async () => {
    wrap();
    const rows = await screen.findAllByTestId('hardware-device');
    expect(rows).toHaveLength(2);
    expect(screen.getAllByTestId('hardware-group').map((g) => within(g).getByRole('heading', { level: 3 }).textContent))
      .toEqual(['Den 1', 'Porch 1']);
    expect(screen.getByRole('button', { name: 'Den TV, Offline' })).toBeInTheDocument();
    expect(within(rows[1]).getByText('Dim')).toBeInTheDocument();
    expect(within(rows[1]).getByText(/192\.0\.2\.21/)).toBeInTheDocument();
  });

  it('searches, filters by chip and regroups', async () => {
    wrap();
    await screen.findAllByTestId('hardware-device');
    fireEvent.change(screen.getByLabelText('Search devices'), { target: { value: 'roku' } });
    expect(screen.getAllByTestId('hardware-device')).toHaveLength(1);
    fireEvent.change(screen.getByLabelText('Search devices'), { target: { value: '' } });
    fireEvent.click(within(screen.getByTestId('hardware-filters')).getByRole('button', { name: /Offline 1/ }));
    expect(screen.getAllByTestId('hardware-device')).toHaveLength(1);
    fireEvent.click(within(screen.getByTestId('hardware-filters')).getByRole('button', { name: 'All' }));
    fireEvent.change(screen.getByLabelText('Group devices by'), { target: { value: 'integration' } });
    expect(screen.getAllByTestId('hardware-group').map((g) => within(g).getByRole('heading').textContent))
      .toEqual(['Roku 1', 'WiZ 1']);
  });

  it('opens a device with its entities and controls, and renames and disables an entity', async () => {
    vi.mocked(hardware.updateEntity).mockResolvedValue(detail.entity_list[0]);
    wrap();
    fireEvent.click(await screen.findByRole('button', { name: 'Porch lamp, Online' }));
    const sheet = await screen.findByTestId('hardware-detail');
    expect(within(sheet).getByText('1.36')).toBeInTheDocument();
    expect(within(sheet).getByText('02:00:00:00:00:21')).toBeInTheDocument();
    expect(within(sheet).getByRole('slider', { name: 'Porch lamp brightness' })).toBeInTheDocument();
    expect(within(sheet).getAllByTestId('hardware-entity')).toHaveLength(1);
    fireEvent.click(within(sheet).getByRole('button', { name: /Show settings, diagnostics/ }));
    expect(within(sheet).getAllByTestId('hardware-entity')).toHaveLength(2);
    fireEvent.click(within(sheet).getByRole('button', { name: 'Rename Porch lamp' }));
    fireEvent.change(within(sheet).getByLabelText('New name for Porch lamp'), { target: { value: 'Front lamp' } });
    fireEvent.click(within(sheet).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(hardware.updateEntity).toHaveBeenCalledWith('light.porch_lamp', { name: 'Front lamp' }));
    fireEvent.click(within(sheet).getByRole('button', { name: 'Disable Porch lamp Power' }));
    await waitFor(() => expect(hardware.updateEntity).toHaveBeenCalledWith('sensor.porch_lamp_power', { disabled: true }));
  });

  it('moves, disables and removes the device from its sheet', async () => {
    vi.mocked(discovered.updateHubDevice).mockResolvedValue({} as never);
    vi.mocked(hardware.setDeviceDisabled).mockResolvedValue(base);
    vi.mocked(discovered.removeHubDevice).mockResolvedValue({ status: 'removed' });
    wrap();
    fireEvent.click(await screen.findByRole('button', { name: 'Porch lamp, Online' }));
    const sheet = await screen.findByTestId('hardware-detail');
    fireEvent.change(within(sheet).getByLabelText('Room of Porch lamp'), { target: { value: 'den' } });
    await waitFor(() => expect(discovered.updateHubDevice).toHaveBeenCalledWith('d-lamp', { area_id: 'den' }));
    fireEvent.click(within(sheet).getByRole('button', { name: /^Disable$/ }));
    await waitFor(() => expect(hardware.setDeviceDisabled).toHaveBeenCalledWith('d-lamp', true));
    fireEvent.click(within(sheet).getByRole('button', { name: /^Remove$/ }));
    fireEvent.click(within(sheet).getByRole('button', { name: /Yes, remove it/ }));
    await waitFor(() => expect(discovered.removeHubDevice).toHaveBeenCalledWith('d-lamp', false));
  });

  it('shows one row per physical device, with every integration that sees it', async () => {
    const roku: HardwareDevice = { ...tv, id: 'd-roku', status: 'online', unavailable: 0, kind: 'media', group_id: 'd-roku' };
    const player: HardwareDevice = { ...tv, id: 'd-player', domain: 'music_assistant', label: 'Music Assistant',
      area_id: null, area: null, status: 'partial', kind: 'media', group_id: 'd-roku' };
    const addon: HardwareDevice = { ...base, id: 'd-addon', name: 'File editor', domain: 'hassio', label: 'Supervisor',
      service: true, group_id: 'd-addon' };
    vi.mocked(hardware.getHardware).mockResolvedValue({ devices: [{ ...base, kind: 'light' }, roku, player, addon],
      areas: [{ area_id: 'den', name: 'Den' }, { area_id: 'porch', name: 'Porch' }] });
    wrap();
    await screen.findAllByTestId('hardware-device');
    const listed = screen.getAllByTestId('hardware-group').flatMap((g) => within(g).queryAllByTestId('hardware-device'));
    expect(listed).toHaveLength(2);
    const tvRow = screen.getByRole('button', { name: 'Den TV, Online' });
    expect(tvRow).toHaveTextContent('Roku · Music Assistant');
    // Within a room, devices are split by type.
    expect(screen.getAllByTestId('hardware-kind').map((k) => k.querySelector('h4')?.textContent)).toEqual(['TVs & speakers', 'Lights']);
    // Add-ons and assistants are not devices in the house: folded away, still reachable.
    expect(within(screen.getByTestId('hardware-services')).getByText(/Not devices.*\(1\)/)).toBeInTheDocument();
    fireEvent.click(within(screen.getByTestId('hardware-filters')).getByRole('button', { name: /Lights 1/ }));
    expect(screen.getAllByTestId('hardware-group').flatMap((g) => within(g).queryAllByTestId('hardware-device'))).toHaveLength(1);
  });

  it('puts a device with no room into one from the needs-attention list, every view of it', async () => {
    vi.mocked(discovered.updateHubDevice).mockResolvedValue({} as never);
    const lost: HardwareDevice = { ...base, id: 'd-a', name: 'Hall bulb', area_id: null, area: null, kind: 'light', group_id: 'd-a' };
    const lostToo: HardwareDevice = { ...lost, id: 'd-b', domain: 'matter', label: 'Matter', group_id: 'd-a' };
    vi.mocked(hardware.getHardware).mockResolvedValue({ devices: [lost, lostToo, tv],
      areas: [{ area_id: 'den', name: 'Den' }, { area_id: 'porch', name: 'Porch' }] });
    wrap();
    const attention = await screen.findByTestId('needs-attention');
    expect(within(attention).getByRole('button', { name: 'Not answering 1' })).toBeInTheDocument();
    fireEvent.click(within(attention).getByRole('button', { name: 'No room 1' }));
    fireEvent.change(within(attention).getByLabelText('Room for Hall bulb'), { target: { value: 'porch' } });
    await waitFor(() => expect(discovered.updateHubDevice).toHaveBeenCalledWith('d-b', { area_id: 'porch' }));
    expect(discovered.updateHubDevice).toHaveBeenCalledWith('d-a', { area_id: 'porch' });
  });

  it('says when the hub has nothing or cannot answer', async () => {
    vi.mocked(hardware.getHardware).mockResolvedValue({ devices: [], areas: [] });
    wrap();
    expect(await screen.findByTestId('hardware-empty')).toHaveTextContent('no devices yet');
  });
});
