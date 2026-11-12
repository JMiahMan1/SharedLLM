import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { ReactNode } from 'react';
import * as dashboards from '../lib/dashboards';
import * as schedules from '../lib/schedules';
import type { ClimateOverview, Thermostat as ScheduledThermostat } from '../lib/schedules';
import type { EnergySummary, HubEntity, Thermostat } from '../lib/dashboards';
import { api } from '../services/api';
import Rooms from './Rooms';
import Energy from './Energy';
import History from './History';
import Climate from './Climate';

vi.mock('../lib/dashboards', async (importOriginal) => {
  const real = (await importOriginal()) as Record<string, unknown>;
  return {
    ...real,
    getRooms: vi.fn(), getClimate: vi.fn(), getStatisticsStatus: vi.fn(), importStatistics: vi.fn(), getHistory: vi.fn(), getEntities: vi.fn(), getStatistics: vi.fn(),
    getEnergySummary: vi.fn(), getEnergyPrefs: vi.fn(), saveEnergyPrefs: vi.fn(), getEnergyCandidates: vi.fn(),
    suggestTariff: vi.fn(), importEnergyFromHa: vi.fn(), getDashboardPrefs: vi.fn(), saveDashboardPrefs: vi.fn(),
  };
});
vi.mock('../lib/schedules', async (importOriginal) => {
  const real = (await importOriginal()) as Record<string, unknown>;
  return { ...real, getClimate: vi.fn(), holdThermostat: vi.fn(), resumeThermostat: vi.fn() };
});
vi.mock('../services/api', () => ({ api: { callHaService: vi.fn().mockResolvedValue({ status: 'SUCCESS' }) }, apiClient: {} }));
vi.mock('../lib/mediaToken', () => ({ useMediaToken: () => null }));
let mockUser = { username: 'parent', is_admin: true };
vi.mock('../context/AuthContext', () => ({ useAuth: () => ({ user: mockUser }) }));
vi.mock('react-hot-toast', () => {
  const toast = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() });
  return { default: toast };
});

const ent = (entity_id: string, extra: Partial<HubEntity> = {}): HubEntity => ({
  entity_id, domain: entity_id.split('.')[0], name: entity_id.split('.')[1].replace(/_/g, ' '), state: 'off',
  attributes: {}, device_class: null, unit: null, icon: null, area_id: 'den', device_id: null,
  last_changed: '2026-01-01T00:00:00Z', ...extra,
});

function wrap(ui: ReactNode, path = '/') {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

const thermostat = (): Thermostat => ({
  ...ent('climate.hall', {
    state: 'cool',
    attributes: { hvac_modes: ['off', 'cool', 'heat'], temperature: 76, current_temperature: 78, hvac_action: 'cooling',
      target_temp_step: 1, min_temp: 60, max_temp: 90 },
  }),
  area_name: 'Den', power_source: 'area_or_name', sensors: [ent('sensor.den_humidity', { state: '41', unit: '%', device_class: 'humidity' })],
  power: [ent('sensor.unit_power', { state: '2.5', unit: 'kW', device_class: 'power' })],
});

describe('Rooms', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(dashboards.getRooms).mockResolvedValue({
      floors: [],
      areas: [{
        area_id: 'den', name: 'Den', floor_id: null, icon: null, entities: [
          ent('light.lamp', { state: 'on', attributes: { brightness: 128, supported_color_modes: ['brightness'] } }),
          ent('switch.opener'),
          ent('sensor.den_temp', { state: '72.4', unit: '°F', device_class: 'temperature' }),
          ent('binary_sensor.door', { state: 'on', device_class: 'garage_door' }),
          ent('camera.drive'),
        ],
      }],
      unassigned: [ent('vacuum.robot', { state: 'docked', attributes: { battery_level: 90 } })],
    });
  });

  it('builds a card per area from the registry and toggles a light through the service', async () => {
    wrap(<Rooms />);
    const card = await screen.findByTestId('room-card');
    expect(within(card).getByText('Den')).toBeInTheDocument();
    expect(within(card).getByText('72.4 °F')).toBeInTheDocument();
    expect(within(card).getByText('Open')).toBeInTheDocument();
    expect(within(card).getByRole('link', { name: 'Open drive' })).toHaveAttribute('href', '/cameras?camera=camera.drive');
    const toggle = within(card).getByRole('switch', { name: 'Toggle lamp' });
    expect(toggle).toHaveAttribute('aria-checked', 'true');
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-checked', 'false');
    await waitFor(() => expect(api.callHaService).toHaveBeenCalledWith('light', 'toggle', 'light.lamp', null));
    // Things in no room wait behind "Other".
    expect(screen.queryByText('robot')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /Other/ }));
    expect(screen.getByText('robot')).toBeInTheDocument();
  });
});

const scheduled = (extra: Partial<ScheduledThermostat> = {}): ScheduledThermostat => ({
  entity_id: 'climate.hall', name: 'hall', area: 'Den', state: 'cool', current_temperature: 78, temperature: 76,
  capabilities: { hvac_modes: ['off', 'cool', 'heat'], fan_modes: [] },
  current: null, next: null, hold: null, effective: { source: 'schedule', setpoint: { hvac_mode: 'cool', temperature: 76 } },
  summary: 'Following Weekday: 76, goes to 80 at 5:00 PM', ...extra,
});

const overview = (t: ScheduledThermostat): ClimateOverview => ({
  thermostats: [t], programs: [], settings: {} as ClimateOverview['settings'], away: { active: false, reason: null },
  energy: { in_window: false, window: null, power_kw: null, shed: [] }, can_edit: false, now: '2026-01-05T12:00:00Z', time_zone: 'UTC',
});

describe('Rooms home overview', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUser = { username: 'parent', is_admin: true };
    vi.mocked(dashboards.getRooms).mockResolvedValue({
      floors: [],
      areas: [
        { area_id: 'den', name: 'Den', floor_id: null, icon: null, entities: [
          ent('light.lamp', { state: 'on' }), ent('light.spot', { state: 'on' }), ent('camera.drive'),
        ] },
        { area_id: 'hall', name: 'Hall', floor_id: null, icon: null, entities: [ent('light.ceiling', { state: 'off', area_id: 'hall' })] },
      ],
      unassigned: [],
    });
    vi.mocked(schedules.getClimate).mockResolvedValue(overview(scheduled()));
    vi.mocked(dashboards.getEnergySummary).mockResolvedValue(summary());
  });

  it('surfaces lights by room, thermostats with their schedule, cameras and energy today', async () => {
    wrap(<Rooms />);
    const glance = await screen.findByTestId('home-overview');
    expect(within(glance).getByText('2 lights on in 1 room')).toBeInTheDocument();
    expect(await within(glance).findByText('Following Weekday: 76, goes to 80 at 5:00 PM')).toBeInTheDocument();
    expect(within(glance).getByTestId('glance-cameras')).toHaveAttribute('href', '/cameras');
    expect(await within(glance).findByTestId('glance-energy')).toHaveTextContent('30.0 kWh');
  });

  it('turns a room off at once and puts it back when the hub refuses', async () => {
    vi.mocked(api.callHaService).mockRejectedValueOnce(new Error('refused')).mockResolvedValue({ status: 'SUCCESS' } as never);
    wrap(<Rooms />);
    const glance = await screen.findByTestId('home-overview');
    fireEvent.click(within(glance).getByRole('button', { name: 'Turn off the lights in Den' }));
    expect(within(glance).getByText('No lights on')).toBeInTheDocument();
    await waitFor(() => expect(within(glance).getByText('2 lights on in 1 room')).toBeInTheDocument());
    expect(api.callHaService).toHaveBeenCalledWith('light', 'turn_off', 'light.lamp', null);
  });

  it('says where to go when there are no rooms yet', async () => {
    vi.mocked(dashboards.getRooms).mockResolvedValue({ floors: [], areas: [], unassigned: [] });
    wrap(<Rooms />);
    const empty = await screen.findByTestId('empty-state');
    expect(within(empty).getByRole('link', { name: 'Go to Devices' })).toHaveAttribute('href', '/devices');
  });
});

describe('Climate', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUser = { username: 'parent', is_admin: true };
    vi.mocked(dashboards.getClimate).mockResolvedValue({ thermostats: [thermostat()] });
    vi.mocked(dashboards.getHistory).mockResolvedValue({});
    vi.mocked(dashboards.getEntities).mockResolvedValue([]);
  });

  it('shows the thermostat, its power draw and room sensors, sets a mode and links to schedules', async () => {
    wrap(<Climate />);
    const card = await screen.findByTestId('thermostat-card');
    expect(within(card).getByText('2.50 kW')).toBeInTheDocument();
    expect(within(card).getByText('41 %')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Schedules/ })).toHaveAttribute('href', '/schedules');
    fireEvent.click(within(card).getByRole('button', { name: /Heat/ }));
    await waitFor(() => expect(api.callHaService).toHaveBeenCalledWith('climate', 'set_hvac_mode', 'climate.hall', { hvac_mode: 'heat' }));
    expect(within(card).getByLabelText('hall target')).toHaveTextContent('76');
  });

  it('shows the schedule summary and holds from the Climate page', async () => {
    vi.mocked(schedules.getClimate).mockResolvedValue(overview(scheduled()));
    wrap(<Climate />);
    const card = await screen.findByTestId('thermostat-card');
    expect(await within(card).findByText('Following Weekday: 76, goes to 80 at 5:00 PM')).toBeInTheDocument();
    fireEvent.click(within(card).getByRole('button', { name: 'Hold hall' }));
    expect(await screen.findByTestId('hold-control')).toBeInTheDocument();
  });

  it('resumes a held thermostat from the Climate page', async () => {
    vi.mocked(schedules.getClimate).mockResolvedValue(overview(scheduled({
      hold: { setpoint: { hvac_mode: 'cool', temperature: 72 }, until: null, kind: 'permanent', set_at: '2026-01-05T10:00:00Z', by: null },
    })));
    vi.mocked(schedules.resumeThermostat).mockResolvedValue(scheduled());
    wrap(<Climate />);
    const card = await screen.findByTestId('thermostat-card');
    expect(await within(card).findByText(/Held at 72/)).toBeInTheDocument();
    fireEvent.click(within(card).getByRole('button', { name: 'Resume the schedule of hall' }));
    await waitFor(() => expect(schedules.resumeThermostat).toHaveBeenCalledWith('climate.hall'));
  });

  it('points an admin to Devices when there are no thermostats', async () => {
    vi.mocked(dashboards.getClimate).mockResolvedValue({ thermostats: [] });
    wrap(<Climate />);
    expect(await screen.findByRole('link', { name: 'Go to Devices' })).toHaveAttribute('href', '/devices');
  });
});

const summary = (): EnergySummary => ({
  start: '2026-01-05T07:00:00Z', end: '2026-01-06T07:00:00Z', period: 'hour', currency: 'USD',
  grid: { kwh: 30, series: [{ start: '2026-01-05T08:00:00Z', value: 1.2 }], returned_kwh: 0, cost: 3.25,
    cost_source: 'flat_price', power_w: 1500, stats: ['sensor.house_energy'] },
  solar: null,
  tou: { source: null, periods: [] },
  devices: [{ name: 'Pump', stat: 'sensor.pump_energy', kwh: 6, series: [], power_entity: 'sensor.pump_power', power_w: 900 }],
  untracked_kwh: 24,
  utility: [{ name: 'Utility', stat: 'u:x_energy_consumption', kwh: 28, series: [], cost: 3,
    periods: [{ name: 'on_peak', stat: 'p', kwh: 8 }, { name: 'off_peak', stat: 'q', kwh: 20 }],
    data_until: '2026-01-05T23:00:00Z', rate: 0.11 }],
});

describe('Energy', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUser = { username: 'parent', is_admin: true };
    vi.mocked(dashboards.getEnergySummary).mockResolvedValue(summary());
    vi.mocked(dashboards.getEnergyPrefs).mockResolvedValue({
      energy_sources: [{ type: 'grid', stat_energy_from: 'sensor.house_energy' }], device_consumption: [],
      utility_sources: [{ name: 'Utility', stat_energy: 'u:x_energy_consumption', stat_periods: { on_peak: 'p' } }],
      tariff: { currency: 'USD', flat_price: 0.1, periods: [], default_period: { name: 'Off-peak', price: null } },
    });
    vi.mocked(dashboards.getEnergyCandidates).mockResolvedValue([]);
  });

  it('shows usage, cost, devices, the utility split and moves between ranges', async () => {
    wrap(<Energy />);
    expect(await screen.findByText('30.0 kWh')).toBeInTheDocument();
    expect(screen.getByText('$3.25')).toBeInTheDocument();
    expect(screen.getByText('at the flat rate')).toBeInTheDocument();
    expect(screen.getByTestId('energy-device')).toHaveTextContent('Pump');
    expect(screen.getByTestId('tou-split')).toHaveTextContent('On peak');
    expect(screen.getByTestId('utility-card')).toHaveTextContent('publishes about a day late');
    fireEvent.click(screen.getByRole('tab', { name: 'Month' }));
    await waitFor(() => expect(dashboards.getEnergySummary).toHaveBeenLastCalledWith('month', expect.any(Date)));
    fireEvent.click(screen.getByRole('button', { name: 'Previous' }));
    await waitFor(() => expect(dashboards.getEnergySummary).toHaveBeenCalledTimes(3));
  });

  it('lets an admin fill rate periods from the utility data and save', async () => {
    vi.mocked(dashboards.suggestTariff).mockResolvedValue({
      periods: [{ name: 'On-peak', price: null, months: [6], weekdays: [0, 1, 2, 3, 4], start: '15:00', end: '19:00' }],
      months_seen: [6], hours_compared: 500,
    });
    vi.mocked(dashboards.saveEnergyPrefs).mockImplementation(async (p) => p as never);
    wrap(<Energy />);
    fireEvent.click(await screen.findByRole('button', { name: /Settings/ }));
    fireEvent.click(await screen.findByRole('button', { name: /Suggest from utility data/ }));
    const period = await screen.findByTestId('tariff-period');
    expect(period).toHaveTextContent('Mon-Fri, Jun, 15:00-19:00');
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(dashboards.saveEnergyPrefs).toHaveBeenCalled());
    const saved = vi.mocked(dashboards.saveEnergyPrefs).mock.calls[0][0];
    expect(saved.tariff?.periods[0].start).toBe('15:00');
  });

  it('shows an admin the long-term history status next to the settings', async () => {
    vi.mocked(dashboards.getStatisticsStatus).mockResolvedValue({
      metadata: 12, statistics: { rows: 100, first: '2024-03-01T00:00:00Z', last: new Date().toISOString() },
      statistics_short_term: { rows: 10, first: null, last: null }, last_import: null, last_error: null,
    });
    wrap(<Energy />);
    fireEvent.click(await screen.findByRole('button', { name: /Settings/ }));
    const card = await screen.findByTestId('statistics-status');
    expect(await within(card).findByText(/Hourly history for 12 sensors/)).toBeInTheDocument();
    vi.mocked(dashboards.importStatistics).mockResolvedValue({});
    fireEvent.click(within(card).getByRole('button', { name: 'Import history from Home Assistant' }));
    await waitFor(() => expect(dashboards.importStatistics).toHaveBeenCalled());
  });

  it('hides settings from members', async () => {
    mockUser = { username: 'kid', is_admin: false };
    wrap(<Energy />);
    await screen.findByText('30.0 kWh');
    expect(screen.queryByRole('button', { name: /Settings/ })).toBeNull();
  });
});

describe('History', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(dashboards.getEntities).mockResolvedValue([
      { entity_id: 'sensor.den_temp', name: 'Den temperature', domain: 'sensor', unit: '°F', device_class: 'temperature', state: '70' },
      { entity_id: 'switch.opener', name: 'Opener', domain: 'switch', unit: null, device_class: null, state: 'off' },
    ]);
    vi.mocked(dashboards.getHistory).mockResolvedValue({
      'sensor.den_temp': [{ t: new Date(Date.now() - 3600_000).toISOString(), s: '70' }],
      'switch.opener': [{ t: new Date(Date.now() - 3600_000).toISOString(), s: 'on' }],
    });
  });

  it('reads entities from the URL, charts numbers and draws states as a timeline', async () => {
    wrap(<History />, '/?e=sensor.den_temp,switch.opener&r=6h');
    expect(await screen.findByTestId('history-chart')).toBeInTheDocument();
    expect(await screen.findByTestId('history-timeline')).toHaveTextContent('Opener');
    const [ids, start, end] = vi.mocked(dashboards.getHistory).mock.calls[0];
    expect(ids).toEqual(['sensor.den_temp', 'switch.opener']);
    expect((end.getTime() - start.getTime()) / 3600_000).toBeCloseTo(6);
    expect(dashboards.getStatistics).not.toHaveBeenCalled();
  });

  it('adds an entity from the search and uses statistics for long ranges', async () => {
    vi.mocked(dashboards.getStatistics).mockResolvedValue({ 'sensor.den_temp': [{ start: Date.now() - 86400_000, end: Date.now(), mean: 71 }] });
    wrap(<History />, '/?r=30d');
    fireEvent.change(screen.getByLabelText('Search entities'), { target: { value: 'den' } });
    fireEvent.click(await screen.findByRole('option', { name: /Den temperature/ }));
    expect(await screen.findByTestId('history-chip')).toHaveTextContent('Den temperature');
    await waitFor(() => expect(dashboards.getStatistics).toHaveBeenCalledWith(
      ['sensor.den_temp'], expect.any(Date), expect.any(Date), 'hour', ['mean', 'min', 'max']));
    expect(await screen.findByTestId('history-chart')).toBeInTheDocument();
  });
});
