import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { WeatherRemindersPanel } from '../components/admin/WeatherRemindersPanel';
import type { UserProfile } from '../types/api';
import { renderWithProviders } from './render';
import { server } from './setup';

vi.mock('../components/ui/confirmDialog', () => ({ confirmDialog: vi.fn(async () => true) }));
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }));
vi.mock('../components/ui/EntitySearchDropdown', () => ({
  default: ({ value, onChange, testId }: { value: string; onChange: (v: string) => void; testId?: string }) => (
    <input data-testid={testId} value={value} onChange={(e) => onChange(e.target.value)} />
  ),
}));

const users: UserProfile[] = [
  { id: 1, username: 'default', is_admin: true, is_system_default: true } as UserProfile,
  { id: 2, username: 'jeremiah' } as UserProfile,
  { id: 3, username: 'Gracie' } as UserProfile,
];

let stored: Record<string, string> = {};
let saves: Array<{ key: string; value: string }> = [];
let tests: unknown[] = [];

beforeEach(() => {
  stored = { weather_reminders: '[]', weather_units: 'imperial', weather_location: '' };
  saves = [];
  tests = [];
  server.use(
    http.get('/api/settings', () => HttpResponse.json(Object.entries(stored).map(([key, value]) => ({ key, value })))),
    http.patch('/api/settings/:key', async ({ params, request }) => {
      const body = (await request.json()) as { value: string };
      stored[params.key as string] = body.value;
      saves.push({ key: params.key as string, value: body.value });
      return HttpResponse.json({ key: params.key, value: body.value });
    }),
    http.get('/api/geo/saved-places', () => HttpResponse.json({
      places: [{ id: 'p1', entity_id: 'place.p1', name: 'Barn', lat: 36.1234, lon: -86.7, radius: 50, scope: 'family', owner: 'jeremiah' }],
    })),
    http.post('/api/weather/reminders/test', async ({ request }) => {
      tests.push(await request.json());
      return HttpResponse.json({ sent: true, would_trigger: false, body: 'Nothing right now' });
    }),
  );
});

const render = () => renderWithProviders(<WeatherRemindersPanel users={users} />);

describe('WeatherRemindersPanel', () => {
  it('adds a hay reminder for one person at a saved place, then tests it', async () => {
    render();
    await screen.findByText('No weather reminders yet.');
    expect(screen.queryByLabelText('Remind @default')).toBeNull();

    fireEvent.change(screen.getByLabelText('Weather reminder title'), { target: { value: 'Cover the hay' } });
    fireEvent.change(screen.getByLabelText('Weather reminder threshold'), { target: { value: '50' } });
    await screen.findByRole('option', { name: 'Barn' });
    fireEvent.change(screen.getByLabelText('Weather reminder place'), { target: { value: '36.1234,-86.7000' } });
    fireEvent.click(screen.getByLabelText('Remind @jeremiah'));
    fireEvent.click(screen.getByRole('button', { name: 'Add reminder' }));

    await waitFor(() => expect(saves).toHaveLength(1));
    expect(saves[0].key).toBe('weather_reminders');
    const [rule] = JSON.parse(saves[0].value);
    expect(rule).toMatchObject({
      kind: 'rain', title: 'Cover the hay', people: ['jeremiah'], threshold: 50,
      location: { name: 'Barn', latitude: 36.1234, longitude: -86.7 },
    });
    expect(await screen.findByText(/Rain · chance 50% · next 12 h · Barn · @jeremiah/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Send Cover the hay to my phone now' }));
    await waitFor(() => expect(tests).toHaveLength(1));
    expect((tests[0] as { rule: { id: string } }).rule.id).toBe(rule.id);
  });

  it('saves a custom forecast location and units, and rejects a bad one', async () => {
    render();
    await screen.findByText('No weather reminders yet.');
    fireEvent.change(screen.getByLabelText('Forecast latitude'), { target: { value: '120' } });
    fireEvent.change(screen.getByLabelText('Forecast longitude'), { target: { value: '-86.8' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save location' }));
    expect(saves).toEqual([]);

    fireEvent.change(screen.getByLabelText('Forecast location name'), { target: { value: 'Farm' } });
    fireEvent.change(screen.getByLabelText('Forecast latitude'), { target: { value: '36' } });
    fireEvent.change(screen.getByLabelText('Weather units'), { target: { value: 'metric' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save location' }));
    await waitFor(() => expect(saves).toHaveLength(2));
    expect(saves[0]).toEqual({ key: 'weather_location', value: JSON.stringify({ name: 'Farm', latitude: 36, longitude: -86.8 }) });
    expect(saves[1]).toEqual({ key: 'weather_units', value: 'metric' });
  });

  it('converts saved thresholds when the units change, so 32 °F stays a freeze', async () => {
    stored.weather_reminders = JSON.stringify([
      { id: 'pipes', kind: 'freeze', title: 'Drip the taps', people: [], threshold: 32, units: 'imperial' },
      { id: 'hay', kind: 'rain', title: 'Cover the hay', people: [], threshold: 70 },
    ]);
    render();
    await screen.findByText(/at or below 32°F/);
    fireEvent.change(screen.getByLabelText('Weather units'), { target: { value: 'metric' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save location' }));
    await waitFor(() => expect(saves.map((s) => s.key)).toEqual(['weather_location', 'weather_reminders', 'weather_units']));
    const [pipes, hay] = JSON.parse(saves[1].value);
    expect(pipes).toMatchObject({ threshold: 0, units: 'metric' });
    expect(hay).toMatchObject({ threshold: 70, units: 'metric' });
    expect(await screen.findByText(/at or below 0°C/)).toBeInTheDocument();
  });

  it('keeps a rule\'s place when it is not a saved place any more', async () => {
    stored.weather_reminders = JSON.stringify([{ id: 'lake', kind: 'wind', title: 'Tie the boat', people: [],
      location: { name: 'Lake', latitude: 35.5, longitude: -86.1 } }]);
    render();
    await screen.findByText(/Tie the boat/);
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    expect(screen.getByLabelText('Weather reminder place')).toHaveValue('35.5000,-86.1000');
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() => expect(saves).toHaveLength(1));
    expect(JSON.parse(saves[0].value)[0].location).toEqual({ name: 'Lake', latitude: 35.5, longitude: -86.1 });
  });

  it('edits and removes an existing reminder', async () => {
    stored.weather_reminders = JSON.stringify([{ id: 'pipes', kind: 'freeze', title: 'Drip the taps', people: [] }]);
    render();
    expect(await screen.findByText(/Freeze · at or below 32°F · next 24 h · Home · everyone/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    expect(screen.getByLabelText('Weather reminder title')).toHaveValue('Drip the taps');
    fireEvent.click(screen.getByRole('button', { name: 'Remove Drip the taps' }));
    await waitFor(() => expect(saves).toHaveLength(1));
    expect(JSON.parse(saves[0].value)).toEqual([]);
  });

  it('closes the barn door without a notice', async () => {
    render();
    await screen.findByText('No weather reminders yet.');
    fireEvent.change(screen.getByLabelText('Weather reminder title'), { target: { value: 'Close the barn' } });
    fireEvent.click(screen.getByLabelText('Weather reminder sends a notice'));
    fireEvent.click(screen.getByRole('button', { name: 'Add reminder' }));
    expect(saves).toEqual([]);  // a reminder that does nothing is refused
    fireEvent.change(screen.getByLabelText('Home Assistant action'), { target: { value: 'cover.close_cover' } });
    fireEvent.change(screen.getByTestId('weather-action-entity'), { target: { value: 'cover.barn_door' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add reminder' }));
    await waitFor(() => expect(saves).toHaveLength(1));
    const [rule] = JSON.parse(saves[0].value);
    expect(rule).toMatchObject({ title: 'Close the barn', notify: false,
      actions: [{ type: 'ha_service', service: 'cover.close_cover', entity_id: 'cover.barn_door' }] });
    expect(await screen.findByText(/no notice · runs cover.close_cover on cover.barn_door/)).toBeInTheDocument();
  });
});
