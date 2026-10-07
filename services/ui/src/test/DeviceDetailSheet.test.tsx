import { describe, it, expect, beforeEach } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import CompanionDevicesPanel from '../components/settings/CompanionDevicesPanel';
import { renderWithProviders } from './render';
import { server } from './setup';

/**
 * Opening a device and seeing what it actually reported.
 *
 * The claims under test are the ones that keep the panel honest: a row opens
 * into the device's own data, a watch shows the steps it contributed (not a
 * fused family total), a device that has reported nothing says so instead of
 * drawing an empty chart, and the battery chip beside the row still does its
 * own job without the sheet stealing the click.
 */
const PHONE = {
  device_key: 'phone-abc12345',
  kind: 'phone',
  label: '',
  manufacturer: 'Google',
  model: 'Pixel 7',
  os_version: '14',
  app_version: '1.5.0',
  app_build: '24',
  registered_by: 'self',
  owner_username: 'default',
  last_seen_at: new Date().toISOString(),
  first_seen_at: '2026-09-01T10:00:00',
  capabilities: { camera: true, storage: 'ok' },
  battery: { at: new Date().toISOString(), pct: 72, usb: false },
};

const WATCH = {
  device_key: 'esphome:744dbd2c9728',
  kind: 'watch',
  label: 'Jarvis Watch',
  registered_by: 'paired',
  owner_username: 'default',
  last_seen_at: new Date().toISOString(),
};

let steps = { watch: { '2026-10-05': 4200, '2026-10-06': 8100 } } as Record<string, Record<string, number>>;

const activity = (key: string) => ({
  device: key === WATCH.device_key ? WATCH : PHONE,
  counts: { app_open: 3, battery: 2 },
  events: [
    { event: 'battery', at: '2026-10-06T18:00:00', extra: { pct: 72, usb: false } },
    { event: 'app_open', at: '2026-10-06T17:00:00', extra: {} },
  ],
  first_seen_at: '2026-09-01T10:00:00',
  last_seen_at: '2026-10-06T18:00:00',
});

const mock = () => {
  server.use(
    http.get('/api/user-panel/devices', () => HttpResponse.json([PHONE, WATCH])),
    http.get('/api/user-panel/devices/:key/activity', ({ params }) =>
      HttpResponse.json(activity(String(params.key))),
    ),
    http.get('/api/user-panel/devices/:key/battery', ({ params }) =>
      HttpResponse.json(
        String(params.key) === PHONE.device_key
          ? [
              { at: '2026-10-06T10:00:00', pct: 90, usb: false },
              { at: '2026-10-06T18:00:00', pct: 72, usb: false },
            ]
          : [],
      ),
    ),
    http.get('/api/geo/steps/sources', () =>
      HttpResponse.json({
        user_id: 'default',
        days: 7,
        sources: steps,
        hourly: {},
        last_synced: 1759700000.5,
      }),
    ),
  );
};

const open = async (key: string) => {
  renderWithProviders(<CompanionDevicesPanel />);
  fireEvent.click(await screen.findByTestId(`device-open-${key}`));
  return screen.findByTestId('device-detail-sheet');
};

describe('the device detail sheet', () => {
  beforeEach(() => {
    steps = { watch: { '2026-10-05': 4200, '2026-10-06': 8100 } };
    mock();
  });

  it('opens from a row and shows what the device is', async () => {
    await open(PHONE.device_key);
    expect(screen.getByTestId('device-detail-name')).toHaveTextContent('Google Pixel 7');
    expect(screen.getByTestId('device-fact-Model')).toHaveTextContent('Pixel 7');
    expect(screen.getByTestId('device-fact-OS')).toHaveTextContent('14');
    expect(screen.getByTestId('device-detail-capabilities')).toHaveTextContent('camera');
  });

  it('shows the steps this device contributed', async () => {
    await open(WATCH.device_key);
    const chart = await screen.findByTestId('device-steps');
    expect(chart).toHaveTextContent('8,100');
    expect(chart).toHaveTextContent('10-06');
  });

  it('says so when a device has reported no steps', async () => {
    steps = {};
    await open(WATCH.device_key);
    expect(await screen.findByTestId('device-steps-empty')).toHaveTextContent(
      /Nothing reported by this device yet/i,
    );
  });

  it('shows what the device has been doing', async () => {
    await open(PHONE.device_key);
    const events = await screen.findByTestId('device-activity');
    expect(events).toHaveTextContent('app open');
    expect(screen.getByTestId('device-count-app_open')).toHaveTextContent('3');
    expect(events).toHaveTextContent('pct: 72');
  });

  it('links out for the whole picture', async () => {
    await open(PHONE.device_key);
    expect(screen.getByTestId('device-link-health')).toHaveAttribute('href', '/fitness');
    expect(screen.getByTestId('device-link-wander')).toHaveAttribute('href', '/wander');
  });

  it('closes when the scrim is tapped', async () => {
    await open(PHONE.device_key);
    fireEvent.click(screen.getByTestId('device-detail-scrim'));
    await waitFor(() => expect(screen.queryByTestId('device-detail-sheet')).not.toBeInTheDocument());
  });

  it('leaves the battery chip to do its own job', async () => {
    renderWithProviders(<CompanionDevicesPanel />);
    const chip = await screen.findByTestId(`device-battery-${PHONE.device_key}`);
    fireEvent.click(chip);
    expect(await screen.findByText(/2 readings/i)).toBeInTheDocument();
    expect(screen.queryByTestId('device-detail-sheet')).not.toBeInTheDocument();
  });
});
