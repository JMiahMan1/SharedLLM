import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import toast from 'react-hot-toast';
import Notes from '../pages/Notes';
import { renderWithProviders } from './render';
import { notesMetaWrites, server } from './setup';

type TimerCall = { title?: string; type?: string; time_str?: string; url?: string; id?: string };

function serveTimers(adds: TimerCall[], deletes: TimerCall[]) {
  server.use(
    http.post('/api/communication/timers', async ({ request }) => {
      const body = (await request.json()) as TimerCall;
      adds.push(body);
      return HttpResponse.json({
        status: 'SUCCESS',
        message: 'Set reminder.',
        service: 'timer_add',
        detail: { timer_id: `t${adds.length}`, expires_at: body.time_str },
      });
    }),
    http.delete('/api/communication/timers', async ({ request }) => {
      deletes.push((await request.json()) as TimerCall);
      return HttpResponse.json({ status: 'SUCCESS', message: 'Deleted 1 timer(s).', service: 'timer_delete' });
    }),
  );
}

const lastReminder = (path: string) =>
  (notesMetaWrites.at(-1)?.meta as { notes?: Record<string, { reminder?: { at: string; timerId: string } }> })?.notes?.[path]
    ?.reminder;

describe('Note reminders', () => {
  beforeEach(() => {
    localStorage.clear();
    toast.remove();
  });
  afterEach(() => vi.useRealTimers());

  it('sets a reminder on the timers that opens the note, and shows it on the card', async () => {
    const user = userEvent.setup();
    const adds: TimerCall[] = [];
    serveTimers(adds, []);
    renderWithProviders(<Notes />);

    await user.click(await screen.findByText('Trip ideas'));
    await screen.findByLabelText('Note content');
    await user.click(screen.getByRole('button', { name: 'Remind me' }));
    await user.click(within(screen.getByTestId('note-reminder')).getByRole('button', { name: 'Tomorrow 9:00' }));

    await waitFor(() => expect(adds).toHaveLength(1));
    expect(adds[0]).toMatchObject({
      title: '📝 Trip ideas',
      type: 'reminder',
      url: '/notes?note=Notes%2FTrip%20ideas.md',
    });
    const at = new Date(adds[0].time_str!);
    expect(at.getHours()).toBe(9);
    expect(at.getTime()).toBeGreaterThan(Date.now());
    await waitFor(() => expect(lastReminder('Notes/Trip ideas.md')).toEqual({ at: adds[0].time_str, timerId: 't1' }));

    await user.click(screen.getByRole('button', { name: 'Close' }));
    expect(await screen.findByTestId('note-reminder-chip')).toBeInTheDocument();
  });

  it('removes a reminder by cancelling its timer and clearing the index', async () => {
    const user = userEvent.setup();
    const adds: TimerCall[] = [];
    const deletes: TimerCall[] = [];
    serveTimers(adds, deletes);
    renderWithProviders(<Notes />);

    await user.click(await screen.findByText('Trip ideas'));
    await screen.findByLabelText('Note content');
    await user.click(screen.getByRole('button', { name: 'Remind me' }));
    const panel = screen.getByTestId('note-reminder');
    await user.click(within(panel).getByRole('button', { name: 'Tomorrow 9:00' }));
    await waitFor(() => expect(lastReminder('Notes/Trip ideas.md')?.timerId).toBe('t1'));

    await user.click(within(panel).getByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(deletes).toEqual([expect.objectContaining({ id: 't1', type: 'reminder' })]));
    await waitFor(() => expect(lastReminder('Notes/Trip ideas.md')).toBeUndefined());
  });

  it('refuses a time that has already passed', async () => {
    const user = userEvent.setup();
    const adds: TimerCall[] = [];
    serveTimers(adds, []);
    renderWithProviders(<Notes />);

    await user.click(await screen.findByText('Trip ideas'));
    await screen.findByLabelText('Note content');
    await user.click(screen.getByRole('button', { name: 'Remind me' }));
    const panel = screen.getByTestId('note-reminder');
    const field = within(panel).getByLabelText('Reminder time');
    await user.clear(field);
    await user.type(field, '2020-01-01T09:00');
    await user.click(within(panel).getByRole('button', { name: /set reminder/i }));

    expect(await within(panel).findByRole('alert')).toHaveTextContent('still ahead');
    expect(adds).toEqual([]);
  });

  it('shows the timers’ refusal and keeps the note without a reminder', async () => {
    const user = userEvent.setup();
    server.use(
      http.post('/api/communication/timers', () =>
        HttpResponse.json({ status: 'FAILURE', message: 'Could not determine timer duration/time.' }),
      ),
    );
    renderWithProviders(<Notes />);

    await user.click(await screen.findByText('Trip ideas'));
    await screen.findByLabelText('Note content');
    await user.click(screen.getByRole('button', { name: 'Remind me' }));
    const panel = screen.getByTestId('note-reminder');
    await user.click(within(panel).getByRole('button', { name: 'Tomorrow 9:00' }));

    expect(await within(panel).findByRole('alert')).toHaveTextContent('Could not determine');
    expect(notesMetaWrites).toEqual([]);
  });

  it('offers no reminder on a note that was never saved', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Notes />);
    await user.click(await screen.findByTestId('quick-capture'));
    expect(screen.queryByRole('button', { name: 'Remind me' })).toBeNull();
  });
});
