import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import PhonesPanel from './PhonesPanel';
import * as phones from '../../lib/phones';
import type { Phone, PhoneList } from '../../lib/phones';

vi.mock('../../lib/phones', async (importOriginal) => {
  const real = (await importOriginal()) as Record<string, unknown>;
  return {
    ...real,
    getPhones: vi.fn(),
    addJarvisPhone: vi.fn(),
    updatePhone: vi.fn(),
    removePhone: vi.fn(),
    savePhoneSettings: vi.fn(),
  };
});
vi.mock('../ui/confirmDialog', () => ({ confirmDialog: vi.fn(async () => true) }));
vi.mock('react-hot-toast', () => {
  const toast = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() });
  return { default: toast };
});

const sensor = (entity_id: string, name: string, state: string, source: 'companion' | 'jarvis', unit: string | null = null,
  device_class: string | null = null) => ({
  entity_id, name, domain: entity_id.split('.')[0], state, unit, device_class, disabled: false, source,
  last_updated: null,
});
const ALARM = '2030-03-18T13:30:00+00:00';

const companion: Phone = {
  entry_id: 'entry-one', name: "Robin's Phone", kind: 'companion',
  app: { app_name: 'Home Assistant', app_version: '2026.10.1', os_name: 'Android', os_version: '16', manufacturer: 'Acme', model: 'P1' },
  jarvis_user: 'robin', jarvis_fallback: true, companion_last_seen: Date.now() / 1000 - 300, companion_fresh: true,
  encrypted: false, push: { relay: true, local_channel: false }, notify_service: 'notify.mobile_app_robin_s_phone',
  state: 'loaded',
  sensors: [
    sensor('sensor.robin_s_phone_battery_level', "Robin's Phone Battery level", '81', 'companion', '%'),
    sensor('sensor.robin_s_phone_wifi_bssid', "Robin's Phone Wi-Fi BSSID", 'x', 'companion'),
  ],
};
const jarvisOnly: Phone = {
  ...companion, entry_id: 'entry-two', name: "Kim's Phone", kind: 'jarvis', jarvis_user: 'kim', companion_last_seen: null,
  companion_fresh: false, push: { relay: false, local_channel: false }, notify_service: 'notify.mobile_app_kim_s_phone',
  sensors: [
    sensor('sensor.kim_s_phone_steps_sensor', "Kim's Phone Steps sensor", '4200', 'jarvis', 'steps'),
    sensor('sensor.kim_s_phone_battery_state', "Kim's Phone Battery state", 'charging', 'jarvis'),
    sensor('sensor.kim_s_phone_charger_type', "Kim's Phone Charger type", 'wireless', 'jarvis'),
    sensor('binary_sensor.kim_s_phone_is_charging', "Kim's Phone Is charging", 'on', 'jarvis', null, 'plug'),
    sensor('sensor.kim_s_phone_next_alarm', "Kim's Phone Next alarm", ALARM, 'jarvis', null, 'timestamp'),
  ],
};
const data: PhoneList = {
  phones: [companion, jarvisOnly],
  settings: { external_url: 'https://hub.example.invalid', internal_url: '', companion_fresh_minutes: 30,
    jarvis_fallback: true, auto_jarvis_phones: true, jarvis_phone_name: "{name}'s Phone",
    push_url_allowlist: ['https://relay.example.invalid/'] },
};

const wrap = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <PhonesPanel />
    </QueryClientProvider>,
  );

describe('PhonesPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(phones.getPhones).mockResolvedValue(data);
  });

  it('lists every phone with its key sensors and where each value comes from', async () => {
    wrap();
    const rows = await screen.findAllByTestId('phone');
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText(/Companion app 2026.10.1/)).toBeInTheDocument();
    expect(within(rows[0]).getByText(/reported 5 min ago/)).toBeInTheDocument();
    expect(within(rows[0]).getByText('81 %')).toBeInTheDocument();
    expect(within(rows[0]).getByText('App')).toBeInTheDocument();
    expect(within(rows[0]).queryByText(/BSSID/)).toBeNull(); // behind "All sensors"
    fireEvent.click(within(rows[0]).getByText(/All sensors/));
    expect(within(rows[0]).getByText(/BSSID/)).toBeInTheDocument();
    expect(within(rows[1]).getByText('Jarvis only (no companion app)')).toBeInTheDocument();
    expect(within(rows[1]).getAllByText('Jarvis').length).toBeGreaterThan(0);
  });

  it('maps a phone to a Jarvis user, adds a Jarvis phone and removes a phone', async () => {
    vi.mocked(phones.updatePhone).mockResolvedValue({});
    vi.mocked(phones.addJarvisPhone).mockResolvedValue({ entry_id: 'entry-three', name: "Sam's Phone" });
    vi.mocked(phones.removePhone).mockResolvedValue({});
    wrap();
    const field = await screen.findByLabelText("Jarvis user for Robin's Phone");
    fireEvent.change(field, { target: { value: 'sam' } });
    fireEvent.blur(field);
    await waitFor(() => expect(phones.updatePhone).toHaveBeenCalledWith('entry-one', { jarvis_user: 'sam' }));
    fireEvent.click(screen.getByLabelText("Let Jarvis fill in for Robin's Phone"));
    await waitFor(() => expect(phones.updatePhone).toHaveBeenCalledWith('entry-one', { jarvis_fallback: false }));
    fireEvent.change(screen.getByLabelText('Jarvis username for a new phone'), { target: { value: 'sam' } });
    fireEvent.click(screen.getByRole('button', { name: /Add phone/ }));
    await waitFor(() => expect(phones.addJarvisPhone).toHaveBeenCalledWith('sam'));
    fireEvent.click(screen.getByRole('button', { name: "Remove Kim's Phone" }));
    await waitFor(() => expect(phones.removePhone).toHaveBeenCalledWith('entry-two'));
  });

  it('edits and saves the settings', async () => {
    vi.mocked(phones.savePhoneSettings).mockResolvedValue(data.settings);
    wrap();
    fireEvent.click(await screen.findByRole('button', { name: /Settings/ }));
    const form = screen.getByTestId('phone-settings');
    fireEvent.change(within(form).getByLabelText('External URL'), { target: { value: 'https://new.example.invalid' } });
    fireEvent.change(within(form).getByLabelText('Push relays'), { target: { value: 'https://a.example.invalid/\n\n' } });
    fireEvent.click(within(form).getByRole('button', { name: /Save settings/ }));
    await waitFor(() => expect(phones.savePhoneSettings).toHaveBeenCalled());
    const sent = vi.mocked(phones.savePhoneSettings).mock.calls[0][0];
    expect(sent.external_url).toBe('https://new.example.invalid');
    expect(sent.push_url_allowlist).toEqual(['https://a.example.invalid/']);
  });

  it('shows the charging state and next alarm of each phone', async () => {
    wrap();
    const rows = await screen.findAllByTestId('phone');
    expect(within(rows[1]).getByTestId('phone-power')).toHaveTextContent('Charging (wireless)');
    expect(within(rows[1]).getByTestId('phone-alarm')).toHaveTextContent(`Next alarm ${phones.alarmText(ALARM)}`);
    expect(within(rows[1]).getByText('Wireless')).toBeInTheDocument();
    expect(within(rows[1]).getByText('Yes')).toBeInTheDocument();
    // A phone whose app never reported them shows neither line.
    expect(within(rows[0]).queryByTestId('phone-power')).toBeNull();
    expect(within(rows[0]).queryByTestId('phone-alarm')).toBeNull();
  });

  it('reads charging and alarm sensors as words', () => {
    const p = (...sensors: phones.PhoneSensor[]): Phone => ({ ...jarvisOnly, sensors });
    expect(phones.alarmText(ALARM, 'UTC')).toMatch(/Mon.*1:30/);
    expect(phones.phoneStatus(p(sensor('sensor.x_battery_state', 'x', 'discharging', 'jarvis'),
      sensor('sensor.x_charger_type', 'x', 'none', 'jarvis'),
      sensor('sensor.x_next_alarm', 'x', 'unavailable', 'jarvis', null, 'timestamp')))).toEqual({
      power: 'On battery', alarm: 'No alarm set' });
    expect(phones.phoneStatus(p(sensor('sensor.x_battery_state', 'x', 'full', 'jarvis'),
      sensor('sensor.x_charger_type', 'x', 'ac', 'jarvis'))).power).toBe('Full (AC)');
    expect(phones.phoneStatus(p(sensor('binary_sensor.x_is_charging', 'x', 'on', 'jarvis'))).power).toBe('Charging');
    expect(phones.phoneStatus(p(sensor('sensor.x_battery_state', 'x', 'unknown', 'jarvis')))).toEqual({ power: null, alarm: null });
    // An app too old to report them: clearly unavailable, never "no alarm".
    const old = 'not reported by the Jarvis app on this phone (an older version; update the app)';
    expect(phones.phoneStatus(p({ ...sensor('sensor.x_battery_state', 'x', 'unavailable', 'jarvis'), reason: old },
      { ...sensor('sensor.x_charger_type', 'x', 'unavailable', 'jarvis'), reason: old },
      { ...sensor('sensor.x_next_alarm', 'x', 'unavailable', 'jarvis', null, 'timestamp'), reason: old }))).toEqual({
      power: 'Charging state unavailable', alarm: 'Next alarm unavailable' });
    expect(phones.sensorText({ ...sensor('sensor.x_next_alarm', 'x', 'unavailable', 'jarvis', null, 'timestamp'),
      reason: old })).toBe('unavailable');
    expect(phones.sensorText(sensor('sensor.x_next_alarm', 'x', 'unavailable', 'jarvis', null, 'timestamp'))).toBe('None set');
    expect(phones.sensorText(sensor('sensor.x_battery_state', 'x', 'not_charging', 'jarvis'))).toBe('Not charging');
    expect(phones.sensorText(sensor('sensor.x_battery_level', 'x', '81', 'jarvis', '%'))).toBe('81 %');
    expect(phones.sensorText(sensor('sensor.x_battery_level', 'x', 'unknown', 'jarvis', '%'))).toBe('unknown');
  });

  it('formats a last report', () => {
    const now = Date.UTC(2026, 0, 2);
    expect(phones.lastSeenText(null)).toBe('never reported here');
    expect(phones.lastSeenText(now / 1000 - 30, now)).toBe('reported just now');
    expect(phones.lastSeenText(now / 1000 - 3 * 3600, now)).toBe('reported 3 h ago');
    expect(phones.lastSeenText(now / 1000 - 5 * 86400, now)).toBe('reported 5 days ago');
  });
  it('shows the room beacon, its failure reason, the hub registration and the room sensor', async () => {
    const beacon = { state: 'advertising' as const, reason: null, uuid: '00000000-0000-4000-8000-000000000001', major: 1, minor: 2,
      tx_power: -59, tx_power_level: 'low', reported_at: '2030-03-18T13:30:00+00:00', hub_registered: true, hub_detail: null };
    vi.mocked(phones.getPhones).mockResolvedValue({ ...data, phones: [
      { ...companion, beacon, sensors: [...companion.sensors,
        { ...sensor('sensor.robin_s_phone_room', "Robin's Phone Room", 'Den', 'jarvis'), last_updated: new Date().toISOString() }] },
      { ...jarvisOnly, beacon: { ...beacon, state: 'unavailable', reason: 'Bluetooth advertising is not supported on this phone',
        hub_registered: false, hub_detail: 'The device hub does not accept beacon registrations yet' } },
    ] });
    wrap();
    const rows = await screen.findAllByTestId('phone');
    expect(within(rows[0]).getByTestId('phone-beacon')).toHaveTextContent('Room beacon: on');
    expect(within(rows[0]).getByTestId('phone-room-sensor')).toHaveTextContent('Room: Den (just now)');
    expect(within(rows[0]).queryByTestId('phone-beacon-hub')).toBeNull();
    expect(within(rows[1]).getByTestId('phone-beacon'))
      .toHaveTextContent('Room detection unavailable: Bluetooth advertising is not supported on this phone');
    expect(within(rows[1]).getByTestId('phone-beacon-hub')).toHaveTextContent('does not accept beacon registrations yet');
  });

  it('words the beacon states and the room sensor', () => {
    const base = { reason: null, uuid: null, major: null, minor: null, tx_power: null, tx_power_level: null, reported_at: null,
      hub_registered: true, hub_detail: null };
    const ph = (b: Partial<Phone['beacon']> | null) => ({ ...companion, beacon: b === null ? null : { ...base, state: 'advertising', ...b } } as Phone);
    expect(phones.beaconText(ph(null))).toBeNull();
    expect(phones.beaconText(ph({ state: 'bluetooth_off' }))?.line).toBe('Room beacon: waiting for Bluetooth');
    expect(phones.beaconText(ph({ state: 'disabled' }))?.line).toBe('Room beacon: off');
    expect(phones.beaconText(ph({ state: 'unavailable' }))?.line).toBe('Room detection unavailable: the phone did not say why');
    expect(phones.roomText(sensor('sensor.x_room', 'x', 'not_home', 'jarvis'))).toBe('Away');
    expect(phones.roomText(sensor('sensor.x_room', 'x', 'unknown', 'jarvis'))).toBe('Not known');
    expect(phones.roomSensor({ ...jarvisOnly, sensors: [sensor('sensor.x_room_distance', 'x', '2', 'jarvis')] })).toBeUndefined();
  });
});
