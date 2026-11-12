import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import toast from 'react-hot-toast';
import HouseholdTimezone from './HouseholdTimezone';
import { filterTimeZones } from '../../lib/householdTimezone';
import { renderWithProviders } from '../../test/render';
import { server } from '../../test/setup';

vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }));

const ORDER = ['household setting', 'TZ in .env', 'home location'];
const NO_ZONE = 'No time zone: set the household time zone, TZ in .env, or derive it from the home location.';

// A stand-in for the gateway, applying the documented precedence.
let state = { setting: '', env_tz: '', from_location: false, location_set: true };
let puts: Array<Record<string, unknown>> = [];

function snapshot() {
  const base = { ...state, order: ORDER };
  if (state.setting) return { ...base, zone: state.setting, source: 'household setting', error: null };
  if (state.env_tz) return { ...base, zone: state.env_tz, source: 'TZ in .env', error: null };
  if (state.from_location && state.location_set) return { ...base, zone: 'America/Denver', source: 'home location', error: null };
  return { ...base, zone: null, source: null, error: NO_ZONE };
}

beforeEach(() => {
  state = { setting: '', env_tz: '', from_location: false, location_set: true };
  puts = [];
  vi.mocked(toast.error).mockClear();
  vi.mocked(toast.success).mockClear();
  server.use(
    http.get('/api/household-timezone', () => HttpResponse.json(snapshot())),
    http.put('/api/household-timezone', async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      puts.push(body);
      if (typeof body.timezone === 'string' && body.timezone && !body.timezone.includes('/')) {
        return HttpResponse.json({ detail: `Not an IANA time zone: ${body.timezone}` }, { status: 400 });
      }
      if (typeof body.timezone === 'string') state.setting = body.timezone;
      if (typeof body.from_location === 'boolean') state.from_location = body.from_location;
      return HttpResponse.json(snapshot());
    }),
  );
});

afterEach(() => {
  vi.restoreAllMocks();
});

const panel = async () => screen.findByTestId('household-timezone-effective');

describe('HouseholdTimezone', () => {
  it('shows the server error and the order when no source gives a zone', async () => {
    renderWithProviders(<HouseholdTimezone />);
    const effective = await panel();
    expect(within(effective).getByRole('alert')).toHaveTextContent(NO_ZONE);
    expect(effective).toHaveTextContent('TZ in the server .env: not set');
    expect(effective).toHaveTextContent('Checked in this order: household setting, then TZ in .env, then home location.');
  });

  it('finds a zone by search and saves it as the household setting', async () => {
    renderWithProviders(<HouseholdTimezone />);
    await panel();
    fireEvent.change(screen.getByLabelText('Search time zones'), { target: { value: 'berl' } });
    const select = screen.getByLabelText('Household time zone') as HTMLSelectElement;
    expect(within(select).getByRole('option', { name: 'Europe/Berlin' })).toBeInTheDocument();
    expect(within(select).queryByRole('option', { name: 'Asia/Tokyo' })).toBeNull();
    fireEvent.change(select, { target: { value: 'Europe/Berlin' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save time zone' }));
    await waitFor(() => expect(puts).toEqual([{ timezone: 'Europe/Berlin' }]));
    await waitFor(() => expect(screen.getByTestId('household-timezone-effective'))
      .toHaveTextContent('In effect: Europe/Berlin (from the household setting)'));
  });

  it("offers the browser's own zone", async () => {
    vi.spyOn(Intl.DateTimeFormat.prototype, 'resolvedOptions').mockReturnValue(
      { timeZone: 'Pacific/Honolulu' } as Intl.ResolvedDateTimeFormatOptions,
    );
    renderWithProviders(<HouseholdTimezone />);
    await panel();
    fireEvent.click(screen.getByRole('button', { name: "Use my browser's zone (Pacific/Honolulu)" }));
    expect((screen.getByLabelText('Household time zone') as HTMLSelectElement).value).toBe('Pacific/Honolulu');
    fireEvent.click(screen.getByRole('button', { name: 'Save time zone' }));
    await waitFor(() => expect(puts).toEqual([{ timezone: 'Pacific/Honolulu' }]));
  });

  it('clears the setting and falls back to TZ from .env, saying so', async () => {
    state = { ...state, setting: 'Europe/Berlin', env_tz: 'America/Chicago' };
    renderWithProviders(<HouseholdTimezone />);
    expect(await panel()).toHaveTextContent('In effect: Europe/Berlin (from the household setting)');
    fireEvent.click(screen.getByRole('button', { name: 'Clear (use TZ from .env)' }));
    await waitFor(() => expect(puts).toEqual([{ timezone: '' }]));
    await waitFor(() => expect(screen.getByTestId('household-timezone-effective'))
      .toHaveTextContent('In effect: America/Chicago (from TZ in the server .env)'));
    expect(screen.getByRole('button', { name: 'Clear (use TZ from .env)' })).toBeDisabled();
  });

  it('derives the zone from the home location when switched on', async () => {
    renderWithProviders(<HouseholdTimezone />);
    await panel();
    const toggle = screen.getByRole('switch', { name: /Derive from home location/ });
    expect(toggle).not.toBeChecked();
    fireEvent.click(toggle);
    await waitFor(() => expect(puts).toEqual([{ from_location: true }]));
    await waitFor(() => expect(screen.getByTestId('household-timezone-effective'))
      .toHaveTextContent('In effect: America/Denver (from the home location)'));
    expect(screen.getByRole('switch', { name: /Derive from home location/ })).toBeChecked();
  });

  it("shows the server's reason when it refuses a zone", async () => {
    vi.spyOn(Intl, 'supportedValuesOf').mockReturnValue([]);
    renderWithProviders(<HouseholdTimezone />);
    await panel();
    // Without a zone list the field is free text; the server checks it.
    const field = screen.getByLabelText('Household time zone') as HTMLInputElement;
    expect(field.tagName).toBe('INPUT');
    fireEvent.change(field, { target: { value: 'Mars' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save time zone' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Not an IANA time zone: Mars'));
  });
});

describe('filterTimeZones', () => {
  it('matches every word, ignoring case and underscores', () => {
    const zones = ['America/New_York', 'America/Denver', 'Europe/Berlin'];
    expect(filterTimeZones(zones, 'new york')).toEqual(['America/New_York']);
    expect(filterTimeZones(zones, 'AMERICA')).toEqual(['America/New_York', 'America/Denver']);
    expect(filterTimeZones(zones, '  ')).toEqual(zones);
  });
});
