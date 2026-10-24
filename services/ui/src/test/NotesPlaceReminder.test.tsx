import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import toast from 'react-hot-toast';
import Notes from '../pages/Notes';
import { renderWithProviders } from './render';
import { notesMetaWrites, server } from './setup';

type PlaceCall = { zone?: string; on?: string; title?: string; url?: string };

const ZONES = {
  type: 'FeatureCollection',
  features: [
    { type: 'Feature', geometry: { type: 'Point', coordinates: [0, 0] }, properties: { entity_id: 'zone.work', friendly_name: 'Work' } },
    { type: 'Feature', geometry: { type: 'Point', coordinates: [0, 0] }, properties: { entity_id: 'zone.home', friendly_name: 'Home' } },
  ],
};

/** A fake geo: zones, and the caller's waiting place reminders. */
function serveGeo(adds: PlaceCall[], removed: string[], waiting: Array<Record<string, unknown>> = []) {
  server.use(
    http.get('/api/geo/zones', () => HttpResponse.json(ZONES)),
    http.get('/api/geo/place-reminders', () => HttpResponse.json({ reminders: waiting })),
    http.post('/api/geo/place-reminders', async ({ request }) => {
      const body = (await request.json()) as PlaceCall;
      adds.push(body);
      const reminder = {
        id: `p${adds.length}`.padEnd(32, '0'),
        zone: body.zone,
        zone_name: body.zone === 'zone.home' ? 'Home' : 'Work',
        on: body.on,
        title: body.title,
        url: body.url,
        created_at: 1,
      };
      waiting.push(reminder);
      return HttpResponse.json({ reminder });
    }),
    http.delete('/api/geo/place-reminders/:id', ({ params }) => {
      removed.push(String(params.id));
      return HttpResponse.json({ removed: params.id });
    }),
  );
}

const lastPlace = (path: string) =>
  (notesMetaWrites.at(-1)?.meta as { notes?: Record<string, { placeReminder?: Record<string, string> }> })?.notes?.[path]
    ?.placeReminder;

async function openReminderPanel(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByText('Trip ideas'));
  await screen.findByLabelText('Note content');
  await user.click(screen.getByRole('button', { name: 'Remind me' }));
  return screen.findByTestId('note-place-reminder');
}

describe('Note place reminders', () => {
  beforeEach(() => {
    localStorage.clear();
    toast.remove();
  });

  it('sets a "when I get home" reminder on a family place, shown on the card', async () => {
    const user = userEvent.setup();
    const adds: PlaceCall[] = [];
    serveGeo(adds, []);
    renderWithProviders(<Notes />);

    const panel = await openReminderPanel(user);
    const place = await within(panel).findByLabelText('Reminder place');
    // Sorted by name, so Home comes first.
    expect(within(place).getAllByRole('option').map((o) => o.textContent)).toEqual(['Home', 'Work']);
    await user.click(within(panel).getByRole('button', { name: /set place reminder/i }));

    await waitFor(() => expect(adds).toHaveLength(1));
    expect(adds[0]).toEqual({
      zone: 'zone.home',
      on: 'arrive',
      title: '📝 Trip ideas',
      url: '/notes?note=Notes%2FTrip%20ideas.md',
    });
    await waitFor(() =>
      expect(lastPlace('Notes/Trip ideas.md')).toEqual({ id: 'p1'.padEnd(32, '0'), zone: 'zone.home', zoneName: 'Home', on: 'arrive' }),
    );

    await user.click(screen.getByRole('button', { name: 'Close' }));
    expect(await screen.findByTestId('note-place-reminder-chip')).toHaveTextContent('Arriving at Home');
  });

  it('changes to leaving another place, then removes it', async () => {
    const user = userEvent.setup();
    const adds: PlaceCall[] = [];
    const removed: string[] = [];
    serveGeo(adds, removed);
    renderWithProviders(<Notes />);

    const panel = await openReminderPanel(user);
    await user.click(await within(panel).findByRole('button', { name: /set place reminder/i }));
    await waitFor(() => expect(lastPlace('Notes/Trip ideas.md')?.zone).toBe('zone.home'));

    await user.selectOptions(within(panel).getByLabelText('Reminder place'), 'zone.work');
    await user.selectOptions(within(panel).getByLabelText('Remind me when I'), 'leave');
    await user.click(within(panel).getByRole('button', { name: /change place reminder/i }));
    await waitFor(() => expect(lastPlace('Notes/Trip ideas.md')).toMatchObject({ zone: 'zone.work', on: 'leave' }));
    // The new one is set before the old one is cancelled.
    expect(adds.map((a) => a.zone)).toEqual(['zone.home', 'zone.work']);
    expect(removed).toEqual(['p1'.padEnd(32, '0')]);

    await user.click(within(panel).getByRole('button', { name: /remove place reminder/i }));
    await waitFor(() => expect(lastPlace('Notes/Trip ideas.md')).toBeUndefined());
    expect(removed).toEqual(['p1'.padEnd(32, '0'), 'p2'.padEnd(32, '0')]);
  });

  it('drops the card chip once geo says the reminder went off', async () => {
    const user = userEvent.setup();
    const adds: PlaceCall[] = [];
    const waiting: Array<Record<string, unknown>> = [];
    serveGeo(adds, [], waiting);
    renderWithProviders(<Notes />);

    const panel = await openReminderPanel(user);
    await user.click(await within(panel).findByRole('button', { name: /set place reminder/i }));
    await waitFor(() => expect(lastPlace('Notes/Trip ideas.md')).toBeDefined());
    await user.click(screen.getByRole('button', { name: 'Close' }));
    expect(await screen.findByTestId('note-place-reminder-chip')).toBeInTheDocument();

    // Arriving home fired it: geo no longer lists it.
    waiting.length = 0;
    await user.click(screen.getByRole('button', { name: 'Refresh notes' }));
    await waitFor(() => expect(screen.queryByTestId('note-place-reminder-chip')).toBeNull());
    await user.click(await screen.findByText('Trip ideas'));
    await user.click(await screen.findByRole('button', { name: 'Remind me' }));
    expect(await screen.findByText(/place reminder went off/i)).toBeInTheDocument();
  });

  it('says so when the family has no places, and when they cannot be loaded', async () => {
    const user = userEvent.setup();
    server.use(http.get('/api/geo/zones', () => HttpResponse.json({ type: 'FeatureCollection', features: [] })));
    renderWithProviders(<Notes />);
    const panel = await openReminderPanel(user);
    expect(await within(panel).findByText(/no places yet/i)).toBeInTheDocument();
    expect(within(panel).queryByRole('button', { name: /set place reminder/i })).toBeNull();
  });

  it('shows why places could not be loaded', async () => {
    const user = userEvent.setup();
    server.use(http.get('/api/geo/zones', () => HttpResponse.json({ detail: 'Geo zones unavailable' }, { status: 502 })));
    renderWithProviders(<Notes />);
    const panel = await openReminderPanel(user);
    expect(await within(panel).findByRole('alert')).toBeInTheDocument();
  });

  it('shows geo’s refusal and leaves the index alone', async () => {
    const user = userEvent.setup();
    server.use(
      http.get('/api/geo/zones', () => HttpResponse.json(ZONES)),
      http.post('/api/geo/place-reminders', () =>
        HttpResponse.json({ detail: 'That place is not one of the family’s places' }, { status: 422 }),
      ),
    );
    renderWithProviders(<Notes />);
    const panel = await openReminderPanel(user);
    await user.click(await within(panel).findByRole('button', { name: /set place reminder/i }));
    expect(await within(panel).findByRole('alert')).toHaveTextContent('not one of the family');
    expect(notesMetaWrites).toEqual([]);
  });
});
