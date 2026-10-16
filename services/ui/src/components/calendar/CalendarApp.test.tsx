import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import CalendarApp from './CalendarApp';
import { renderWithProviders } from '../../test/render';
import { server } from '../../test/setup';

const ymd = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

const at = (days: number, hour: number) => {
  const d = new Date();
  d.setDate(d.getDate() + days);
  d.setHours(hour, 0, 0, 0);
  return d.toISOString();
};

const integrations = [
  { type: 'nextcloud', enabled: true, writable: true, provides_calendar: true, available: true },
  { type: 'ical', enabled: true, writable: false, provides_calendar: true, available: true },
];

describe('CalendarApp', () => {
  let reads: URLSearchParams[];
  let updates: Array<Record<string, unknown>>;

  beforeEach(() => {
    reads = [];
    updates = [];
    server.use(
      http.get('/api/calendar/settings', () => HttpResponse.json({ status: 'SUCCESS', settings: {} })),
      http.get('/api/communication/calendar/events', ({ request }) => {
        reads.push(new URL(request.url).searchParams);
        return HttpResponse.json({
          status: 'SUCCESS',
          service: 'calendar_read',
          detail: { integrations, default: 'nextcloud' },
          events: [
            { id: 'uid-dentist', integration: 'nextcloud', summary: 'Dentist', start_time: at(1, 9), end_time: at(1, 10), calendar: 'Personal' },
            { integration: 'ical', summary: 'School holiday', start_time: at(2, 9), calendar: 'ical' },
          ],
        });
      }),
      http.put('/api/communication/calendar/events', async ({ request }) => {
        updates.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json({ status: 'SUCCESS', message: 'Updated the event.' });
      }),
    );
  });

  it('loads the days on screen, and the month grid when it is chosen', async () => {
    const user = userEvent.setup();
    renderWithProviders(<CalendarApp />);

    await waitFor(() => expect(reads.length).toBeGreaterThan(0));
    // Agenda: 60 days from today (the server default was only the next week).
    const today = new Date();
    const in60 = new Date(today);
    in60.setDate(today.getDate() + 60);
    expect(reads[0].get('start')).toBe(ymd(today));
    expect(reads[0].get('end')).toBe(ymd(in60));

    await user.click(screen.getByRole('button', { name: 'Month' }));
    await waitFor(() => expect(reads.length).toBeGreaterThan(1));
    const first = new Date(today.getFullYear(), today.getMonth(), 1);
    first.setDate(first.getDate() - first.getDay());
    expect(reads[reads.length - 1].get('start')).toBe(ymd(first));
  });

  it('edits one event by its id and leaves an untouched time alone', async () => {
    const user = userEvent.setup();
    renderWithProviders(<CalendarApp />);

    await user.click(await screen.findByRole('button', { name: /dentist/i }));
    const title = await screen.findByLabelText('Title');
    await user.clear(title);
    await user.type(title, 'Dentist (Leo)');
    await user.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => expect(updates).toHaveLength(1));
    expect(updates[0]).toMatchObject({ event_id: 'uid-dentist', integration: 'nextcloud', summary: 'Dentist (Leo)' });
    // Not a title search (that renamed every event sharing a word with it).
    expect(updates[0]).not.toHaveProperty('query');
    expect(updates[0]).not.toHaveProperty('start_time');
  });

  it('opens a read-only subscription event without edit or delete', async () => {
    const user = userEvent.setup();
    renderWithProviders(<CalendarApp />);

    await user.click(await screen.findByRole('button', { name: /school holiday/i }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/read-only subscription/i)).toBeInTheDocument();
    expect(within(dialog).queryByRole('button', { name: /delete/i })).not.toBeInTheDocument();
    expect(within(dialog).queryByRole('button', { name: /^save$/i })).not.toBeInTheDocument();
  });
  it('sets phone reminders with this device\'s time zone', async () => {
    const saved: Array<Record<string, unknown>> = [];
    server.use(
      http.put('/api/calendar/settings', async ({ request }) => {
        saved.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json({ status: 'SUCCESS', settings: saved[saved.length - 1] });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<CalendarApp />);

    await user.click(await screen.findByRole('button', { name: /sources/i }));
    const select = screen.getByLabelText('Event reminder') as HTMLSelectElement;
    expect(select.value).toBe('30');
    await user.selectOptions(select, '10');
    await waitFor(() => expect(saved).toHaveLength(1));
    expect(saved[0]).toEqual({ reminder_minutes: 10, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone });
  });
});
