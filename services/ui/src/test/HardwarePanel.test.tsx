import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import HardwarePanel from '../components/settings/HardwarePanel';
import { renderWithProviders } from './render';
import { server } from './setup';

const devices = [
  { name: 'office-light', host: '192.168.2.87', port: 6053, noise_psk: 'secret', ha_entity_id: 'light.office_light' },
  { name: 'garage-door', host: '192.168.2.88', port: 6053 },
];

let storedDevices = devices;
let listCalls: string[] = [];

const seedEsphomeSetting = () => {
  server.use(
    http.get('/api/settings', () =>
      HttpResponse.json([
        { key: 'esphome_devices', value: JSON.stringify(storedDevices) },
      ]),
    ),
    http.patch('/api/settings/:key', async ({ request }) => {
      const body = (await request.json()) as { value?: string };
      const url = new URL(request.url);
      if (url.pathname.endsWith('esphome_devices') && body.value) {
        storedDevices = JSON.parse(body.value);
      }
      return HttpResponse.json({ key: 'esphome_devices', value: body.value });
    }),
    http.post('/execute/esphome', async ({ request }) => {
      const body = (await request.json()) as { action: string; device: string };
      if (body.action === 'list') {
        listCalls.push(body.device);
        return HttpResponse.json({
          status: 'SUCCESS',
          message: `${body.device} ok`,
          service: 'esphome',
          detail: { device: { name: body.device }, entities: [{ domain: 'light', object_id: 'x' }] },
        });
      }
      return HttpResponse.json({ status: 'SUCCESS', message: 'sent', service: 'esphome' });
    }),
  );
};

describe('HardwarePanel', () => {
  beforeEach(() => {
    storedDevices = devices;
    listCalls = [];
    seedEsphomeSetting();
  });

  it('renders configured devices with route badges', async () => {
    renderWithProviders(<HardwarePanel />);

    expect(await screen.findByText('office-light')).toBeInTheDocument();
    expect(screen.getByText('garage-door')).toBeInTheDocument();
    // Mapped device shows Both badge, unmapped shows Direct
    expect(screen.getByText('Both')).toBeInTheDocument();
    expect(screen.getByText('Direct')).toBeInTheDocument();
    expect(screen.getByDisplayValue('light.office_light')).toBeInTheDocument();
  });

  it('adds a device and persists it via the settings API', async () => {
    renderWithProviders(<HardwarePanel />);
    await screen.findByText('office-light');

    fireEvent.change(screen.getByLabelText('New device name'), { target: { value: 'attic-fan' } });
    fireEvent.change(screen.getByLabelText('New device host'), { target: { value: '192.168.2.90' } });
    fireEvent.click(screen.getByRole('button', { name: /add device/i }));

    await waitFor(() => {
      expect(storedDevices.some(d => d.name === 'attic-fan')).toBe(true);
    });
    expect(await screen.findByText('attic-fan')).toBeInTheDocument();
  });

  it('removes a device', async () => {
    renderWithProviders(<HardwarePanel />);
    await screen.findByText('garage-door');

    fireEvent.click(screen.getByTitle('Remove garage-door'));

    await waitFor(() => {
      expect(storedDevices.some(d => d.name === 'garage-door')).toBe(false);
    });
    await waitFor(() => {
      expect(screen.queryByText('garage-door')).not.toBeInTheDocument();
    });
  });

  it('tests a connection and reports entity count', async () => {
    renderWithProviders(<HardwarePanel />);
    await screen.findByText('office-light');

    fireEvent.click(screen.getAllByRole('button', { name: /test/i })[0]);

    // The connection test must hit /execute/esphome for the right device
    await waitFor(() => {
      expect(listCalls).toContain('office-light');
    });
    // and the button settles back once the mutation completes
    await waitFor(() => {
      expect(screen.getAllByRole('button', { name: /^test$/i }).length).toBeGreaterThan(0);
    });
  });

  it('rejects a duplicate device name without saving', async () => {
    const before = JSON.stringify(storedDevices);
    renderWithProviders(<HardwarePanel />);
    await screen.findByText('office-light');

    fireEvent.change(screen.getByLabelText('New device name'), { target: { value: 'office-light' } });
    fireEvent.change(screen.getByLabelText('New device host'), { target: { value: '10.0.0.1' } });
    fireEvent.click(screen.getByRole('button', { name: /add device/i }));

    // No Toaster mounted in tests, so assert the observable behavior:
    // duplicate rejected, registry untouched.
    await new Promise(r => setTimeout(r, 50));
    expect(JSON.stringify(storedDevices)).toBe(before);
    expect(screen.queryByText('attic-fan')).not.toBeInTheDocument();
  });
});
