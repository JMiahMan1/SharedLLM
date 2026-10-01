import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from './setup';
import { renderWithProviders } from './render';
import PresenceWidget from '../components/widgets/PresenceWidget';
import type { UserWidgetSettings } from '../types/widget';

const navigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => navigate };
});

const settings = { widget_key: 'presence', visibility: 'visible', order_index: 0 } as UserWidgetSettings;

/** `updated_at` is epoch SECONDS (see geo's location writer), so build from now. */
const at = (secondsAgo: number) => Math.floor(Date.now() / 1000) - secondsAgo;

function useHandlers(locations: Record<string, unknown>, trips: unknown[] = []) {
  server.use(
    http.get('/api/users/location/all', () => HttpResponse.json(locations)),
    http.get('/api/geo/trips', () => HttpResponse.json({ trips, total_trips: trips.length })),
  );
}

const props = { settingsButton: null, userSettings: settings, onTogglePin: () => {} };

describe('PresenceWidget', () => {
  beforeEach(() => {
    navigate.mockClear();
    useHandlers({});
  });

  afterEach(() => server.resetHandlers());

  it('renders the widget shell', async () => {
    renderWithProviders(<PresenceWidget {...props} />);
    expect(await screen.findByTestId('presence-widget')).toBeInTheDocument();
  });

  it('counts only genuinely live members', async () => {
    // One fresh (30s), one recent (5min), one stale (2h).
    useHandlers({
      jeremiah: { latitude: 33.16, longitude: -111.56, updated_at: at(30) },
      michele: { latitude: 33.17, longitude: -111.57, updated_at: at(300) },
      default: { latitude: 33.18, longitude: -111.58, updated_at: at(7200) },
    });
    renderWithProviders(<PresenceWidget {...props} />);

    await screen.findByTestId('presence-row-jeremiah');
    expect(screen.getByText('1')).toBeInTheDocument();
    expect(screen.getByText(/person live/)).toBeInTheDocument();
  });

  it('says how many are not reporting, so a stopped phone is visible', async () => {
    useHandlers({
      jeremiah: { latitude: 33.16, longitude: -111.56, updated_at: at(30) },
      michele: { latitude: 33.17, longitude: -111.57, updated_at: at(7200) },
    });
    renderWithProviders(<PresenceWidget {...props} />);

    await screen.findByTestId('presence-row-michele');
    expect(screen.getByText(/1 not reporting/)).toBeInTheDocument();
  });

  it('labels each member with its freshness and age', async () => {
    useHandlers({ jeremiah: { latitude: 33.16, longitude: -111.56, updated_at: at(30) } });
    renderWithProviders(<PresenceWidget {...props} />);

    const row = await screen.findByTestId('presence-row-jeremiah');
    expect(within(row).getByText(/Live/)).toBeInTheDocument();
  });

  it('drops a null-island fix rather than plotting the Atlantic', async () => {
    useHandlers({ broken: { latitude: 0, longitude: 0, updated_at: at(30) } });
    renderWithProviders(<PresenceWidget {...props} />);

    expect(await screen.findByText(/No one is sharing location/i)).toBeInTheDocument();
    expect(screen.queryByTestId('presence-row-broken')).not.toBeInTheDocument();
  });

  it('summarises the latest trip with its owner', async () => {
    useHandlers(
      {},
      [
        {
          id: 't1',
          user_id: 'jeremiah',
          user_name: 'Jeremiah',
          start_time: at(3600),
          end_time: at(1800),
          distance_miles: 12.4,
          activity_type: 'driving',
          status: 'completed',
          start_location: { name: 'Home' },
          end_location: { name: 'Office' },
          mpg: 25,
          cost_per_gallon: 3.65,
          fuel_used_gal: 0,
          trip_cost_usd: 0,
          top_speed_mph: 0,
          duration_seconds: 1800,
        },
      ],
    );
    renderWithProviders(<PresenceWidget {...props} />);

    expect(await screen.findByText('Home → Office')).toBeInTheDocument();
    // formatMiles drops the decimal at >= 10 miles, so this is "12 mi".
    expect(screen.getByText(/^12 mi$/)).toBeInTheDocument();
    expect(screen.getByText(/Jeremiah/)).toBeInTheDocument();
  });

  it('opens Wander on click and from the keyboard', async () => {
    const user = userEvent.setup();
    renderWithProviders(<PresenceWidget {...props} />);

    await user.click(await screen.findByTestId('presence-widget'));
    expect(navigate).toHaveBeenCalledWith('/wander');

    navigate.mockClear();
    const card = screen.getByTestId('presence-widget');
    card.focus();
    expect(card).toHaveFocus();
    await user.keyboard('{Enter}');
    expect(navigate).toHaveBeenCalledWith('/wander');

    navigate.mockClear();
    await user.keyboard(' ');
    expect(navigate).toHaveBeenCalledWith('/wander');
  });
});