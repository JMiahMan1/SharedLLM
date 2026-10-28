import { describe, it, expect } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import GeofenceAlertsCard from '../components/guardian/GeofenceAlertsCard';
import { renderWithProviders } from './render';
import { server } from './setup';

const ZONES = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [-111.5646, 33.1667] },
      properties: {
        entity_id: 'zone.home',
        friendly_name: 'Home',
        radius: 100,
        latitude: 33.1667,
        longitude: -111.5646,
      },
    },
    {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [-111.80, 33.30] },
      properties: {
        entity_id: 'zone.school',
        friendly_name: 'School',
        radius: 150,
        latitude: 33.30,
        longitude: -111.80,
      },
    },
  ],
};

describe('GeofenceAlertsCard', () => {
  it('renders places and allows toggling arrival alerts and photo scan', async () => {
    let savedRules: Record<string, unknown> | null = null;

    server.use(
      http.get('/api/geo/zones', () => HttpResponse.json(ZONES)),
      http.get('/api/screen-time/:username/status', () =>
        HttpResponse.json({
          username: 'kid',
          day: '2026-10-27',
          rules: {
            photo_scan: true,
            geofence_alerts: {
              'zone.home': { arrive: true, depart: false },
            },
          },
        }),
      ),
      http.put('/api/screen-time/:username/rules', async ({ request }) => {
        savedRules = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ status: 'SUCCESS', rules: savedRules });
      }),
    );

    renderWithProviders(<GeofenceAlertsCard username="kid" name="Emma" />);

    expect(await screen.findByText(/Photo & Video Safety Scan/i)).toBeInTheDocument();
    expect(screen.getByText('School')).toBeInTheDocument();
    expect(screen.getByText('Home')).toBeInTheDocument();

    // Toggle arrival alert for School
    const schoolArriveBtn = screen.getByTestId('geofence-arrive-zone.school');
    fireEvent.click(schoolArriveBtn);

    await waitFor(() => {
      expect(savedRules).not.toBeNull();
      const ga = savedRules?.geofence_alerts as Record<string, { arrive?: boolean }> | undefined;
      expect(ga?.['zone.school']?.arrive).toBe(true);
    });

    // Toggle photo scan off
    const photoScanBtn = screen.getByTestId('toggle-photo-scan');
    fireEvent.click(photoScanBtn);

    await waitFor(() => {
      expect(savedRules?.photo_scan).toBe(false);
    });
  });
});
