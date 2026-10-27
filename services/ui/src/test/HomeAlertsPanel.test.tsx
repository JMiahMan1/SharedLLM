import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { HomeAlertsPanel } from '../components/admin/HomeAlertsPanel';
import type { UserProfile } from '../types/api';
import { renderWithProviders } from './render';
import { server } from './setup';


// Destructive actions draw the app's own dialog now, not the browser's.
vi.mock('../components/ui/confirmDialog', () => ({ confirmDialog: vi.fn(async () => true) }));

vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn() },
}));

vi.mock('../components/ui/EntitySearchDropdown', () => ({
  default: ({ value, onChange, testId }: { value: string; onChange: (v: string) => void; testId?: string }) => (
    <input data-testid={testId} value={value} onChange={(e) => onChange(e.target.value)} />
  ),
}));

const users: UserProfile[] = [
  { id: 1, username: 'default', is_admin: true, is_system_default: true } as UserProfile,
  { id: 2, username: 'Michele' } as UserProfile,
  { id: 3, username: 'jeremiah' } as UserProfile,
];

let stored = '[]';
let saves: unknown[] = [];

beforeEach(() => {
  stored = '[]';
  saves = [];
  server.use(
    http.get('/api/settings', () => HttpResponse.json([{ key: 'home_alert_entities', value: stored }])),
    http.patch('/api/settings/:key', async ({ params, request }) => {
      const body = (await request.json()) as { value: string };
      expect(params.key).toBe('home_alert_entities');
      stored = body.value;
      saves.push(JSON.parse(body.value));
      return HttpResponse.json({ key: params.key, value: body.value });
    }),
  );
});

const render = () => renderWithProviders(<HomeAlertsPanel users={users} />);

describe('HomeAlertsPanel', () => {
  it('says plainly that nothing is mapped yet', async () => {
    render();
    expect(await screen.findByText('No entities send home alerts yet.')).toBeInTheDocument();
  });

  it('maps a doorbell to everyone when nobody is picked', async () => {
    render();
    await screen.findByText('No entities send home alerts yet.');
    fireEvent.change(screen.getByTestId('home-alert-entity-search'), { target: { value: 'binary_sensor.front_doorbell' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save alert' }));

    await waitFor(() => expect(saves).toHaveLength(1));
    expect(saves[0]).toEqual([{ entity_id: 'binary_sensor.front_doorbell', event: 'doorbell', people: [] }]);
    expect(await screen.findByText(/Doorbell pressed · everyone/)).toBeInTheDocument();
  });

  it('sends a garage alert to the people picked, with its minutes, and never offers the system account', async () => {
    render();
    await screen.findByText('No entities send home alerts yet.');
    expect(screen.queryByLabelText('Notify @default')).toBeNull();

    fireEvent.change(screen.getByTestId('home-alert-entity-search'), { target: { value: 'cover.garage' } });
    fireEvent.change(screen.getByLabelText('Home alert event'), { target: { value: 'garage_open' } });
    fireEvent.change(screen.getByLabelText('Alert minutes'), { target: { value: '20' } });
    expect(screen.queryByLabelText('Alert watts')).toBeNull();
    fireEvent.click(screen.getByLabelText('Notify @Michele'));
    fireEvent.click(screen.getByRole('button', { name: 'Save alert' }));

    await waitFor(() => expect(saves).toHaveLength(1));
    expect(saves[0]).toEqual([{ entity_id: 'cover.garage', event: 'garage_open', people: ['michele'], minutes: 20 }]);
  });

  it('edits and removes an existing alert', async () => {
    stored = JSON.stringify([
      { entity_id: 'sensor.washer_power', event: 'appliance_done', people: [], watts: 15 },
      { entity_id: 'binary_sensor.sink_leak', event: 'water_leak', people: ['jeremiah'] },
    ]);
    render();
    expect(await screen.findByText(/Washer or dryer finished · 15 W · everyone/)).toBeInTheDocument();

    fireEvent.click(screen.getAllByRole('button', { name: 'Edit' })[0]);
    expect(screen.getByTestId('home-alert-entity-search')).toHaveValue('sensor.washer_power');
    expect(screen.getByLabelText('Alert watts')).toHaveValue(15);

    fireEvent.click(screen.getByRole('button', { name: /Remove binary_sensor.sink_leak/ }));
    await waitFor(() => expect(saves).toHaveLength(1));
    expect(saves[0]).toEqual([{ entity_id: 'sensor.washer_power', event: 'appliance_done', people: [], watts: 15 }]);
  });
});
