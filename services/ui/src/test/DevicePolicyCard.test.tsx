import { describe, it, expect } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import DevicePolicyCard from '../components/guardian/DevicePolicyCard';
import { renderWithProviders } from './render';
import { server } from './setup';

describe('DevicePolicyCard', () => {
  it('renders device policy options and saves updates', async () => {
    let savedRules: Record<string, unknown> | null = null;

    server.use(
      http.get('/api/screen-time/:username/status', () =>
        HttpResponse.json({
          username: 'kid',
          day: '2026-10-27',
          rules: {
            os_update_policy: {
              policy: 'windowed',
              window_start_min: 120,
              window_end_min: 360,
            },
            vpn_lockdown: false,
            wifi_networks: [
              { ssid: 'Home-WiFi', password: 'secret-pass-123', security: 'wpa2', hidden: false },
            ],
            ca_certs: [
              { alias: 'JarvisRoot', cert_pem: '-----BEGIN CERTIFICATE-----\nABC\n-----END CERTIFICATE-----', enabled: true },
            ],
          },
        }),
      ),
      http.put('/api/screen-time/:username/rules', async ({ request }) => {
        savedRules = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ status: 'SUCCESS', rules: savedRules });
      }),
    );

    renderWithProviders(<DevicePolicyCard username="kid" name="Emma" />);

    expect(await screen.findByText(/System Update Policy/i)).toBeInTheDocument();
    expect(screen.getByText('Home-WiFi')).toBeInTheDocument();
    expect(screen.getByText('JarvisRoot')).toBeInTheDocument();

    // Toggle VPN lockdown
    const vpnBtn = screen.getByTestId('toggle-vpn-lockdown');
    fireEvent.click(vpnBtn);

    // Change update policy to automatic
    const selectPolicy = screen.getByTestId('os-update-policy-select');
    fireEvent.change(selectPolicy, { target: { value: 'automatic' } });

    // Save policies
    const saveBtn = screen.getByTestId('save-device-policies');
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(savedRules).not.toBeNull();
      expect(savedRules?.vpn_lockdown).toBe(true);
      const oup = savedRules?.os_update_policy as Record<string, unknown>;
      expect(oup?.policy).toBe('automatic');
    });
  });

  it('allows adding and removing Wi-Fi networks and CA certs', async () => {
    let savedRules: Record<string, unknown> | null = null;

    server.use(
      http.get('/api/screen-time/:username/status', () =>
        HttpResponse.json({
          username: 'kid',
          day: '2026-10-27',
          rules: {},
        }),
      ),
      http.put('/api/screen-time/:username/rules', async ({ request }) => {
        savedRules = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ status: 'SUCCESS', rules: savedRules });
      }),
    );

    renderWithProviders(<DevicePolicyCard username="kid" name="Emma" />);

    await screen.findByText(/System Update Policy/i);

    // Add a Wi-Fi network
    const ssidInput = screen.getByTestId('input-wifi-ssid');
    const passInput = screen.getByTestId('input-wifi-password');
    const addWifiBtn = screen.getByTestId('btn-add-wifi');

    fireEvent.change(ssidInput, { target: { value: 'GuestNetwork' } });
    fireEvent.change(passInput, { target: { value: 'guest123' } });
    fireEvent.click(addWifiBtn);

    expect(screen.getByText('GuestNetwork')).toBeInTheDocument();

    // Add a CA cert
    const aliasInput = screen.getByTestId('input-cert-alias');
    const pemInput = screen.getByTestId('input-cert-pem');
    const addCertBtn = screen.getByTestId('btn-add-cert');

    fireEvent.change(aliasInput, { target: { value: 'TestCA' } });
    fireEvent.change(pemInput, { target: { value: '-----BEGIN CERTIFICATE-----\nXYZ\n-----END CERTIFICATE-----' } });
    fireEvent.click(addCertBtn);

    expect(screen.getByText('TestCA')).toBeInTheDocument();

    // Save policies
    const saveBtn = screen.getByTestId('save-device-policies');
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(savedRules).not.toBeNull();
      const wifis = savedRules?.wifi_networks as Array<{ ssid: string }>;
      expect(wifis).toHaveLength(1);
      expect(wifis[0].ssid).toBe('GuestNetwork');

      const certs = savedRules?.ca_certs as Array<{ alias: string }>;
      expect(certs).toHaveLength(1);
      expect(certs[0].alias).toBe('TestCA');
    });
  });
});
