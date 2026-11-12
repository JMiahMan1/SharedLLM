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

const sensor = (entity_id: string, name: string, state: string, source: 'companion' | 'jarvis', unit: string | null = null) => ({
  entity_id, name, domain: entity_id.split('.')[0], state, unit, device_class: null, disabled: false, source,
  last_updated: null,
});

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
  sensors: [sensor('sensor.kim_s_phone_steps_sensor', "Kim's Phone Steps sensor", '4200', 'jarvis', 'steps')],
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
    expect(within(rows[1]).getByText('Jarvis')).toBeInTheDocument();
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

  it('formats a last report', () => {
    const now = Date.UTC(2026, 0, 2);
    expect(phones.lastSeenText(null)).toBe('never reported here');
    expect(phones.lastSeenText(now / 1000 - 30, now)).toBe('reported just now');
    expect(phones.lastSeenText(now / 1000 - 3 * 3600, now)).toBe('reported 3 h ago');
    expect(phones.lastSeenText(now / 1000 - 5 * 86400, now)).toBe('reported 5 days ago');
  });
});
