import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Notes from '../pages/Notes';
import { renderWithProviders } from './render';
import { notesCheckOffCalls, notesWriteCalls, server } from './setup';
import { http, HttpResponse } from 'msw';

describe('Notes (Keep-style)', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('lists notes with content previews and checklists', async () => {
    renderWithProviders(<Notes />);

    expect(await screen.findByText('Shared Checklist')).toBeInTheDocument();
    expect(await screen.findByText('Trip ideas')).toBeInTheDocument();

    // checklist item parsed from the note body renders as a real checkbox
    expect(await screen.findByText('Pick up groceries')).toBeInTheDocument();
  });

  it('saves with a full write, never append', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Notes />);

    await user.click(await screen.findByTestId('quick-capture'));
    await user.type(screen.getByLabelText('Note title'), 'Grocery run');
    await user.type(screen.getByLabelText('Note content'), 'milk and eggs');
    await user.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => {
      expect(notesWriteCalls.length).toBe(1);
    });
    expect(notesWriteCalls[0].title).toBe('Grocery run');
    expect(notesWriteCalls[0].content).toContain('milk and eggs');
  });

  it('opens a note without its stored header and saves it in place', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Notes />);

    await user.click(await screen.findByText('Trip ideas'));
    const content = await screen.findByLabelText('Note content');
    // The server writes "# title / Category:" itself; showing it here put a
    // second copy on top of the note with every save.
    expect((content as HTMLTextAreaElement).value).toBe('Sedona in the fall, then the coast.');
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(notesWriteCalls.length).toBe(1));
    expect(notesWriteCalls[0]).toMatchObject({ title: 'Trip ideas', path: 'Notes/Trip ideas.md', category: 'Notes' });
  });

  it('renaming a note writes it under the new name and removes the old file', async () => {
    const user = userEvent.setup();
    const deletes: Array<{ path?: string }> = [];
    server.use(
      http.post('/api/communication/notes/write', async ({ request }) => {
        notesWriteCalls.push((await request.json()) as never);
        return HttpResponse.json({ status: 'SUCCESS', message: 'saved', detail: { path: 'Notes/Fall trip.md' } });
      }),
      http.post('/api/communication/notes/delete', async ({ request }) => {
        deletes.push((await request.json()) as { path?: string });
        return HttpResponse.json({ status: 'SUCCESS', message: 'deleted' });
      }),
    );
    renderWithProviders(<Notes />);

    await user.click(await screen.findByText('Trip ideas'));
    const title = await screen.findByLabelText('Note title');
    await user.clear(title);
    await user.type(title, 'Fall trip');
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(deletes).toEqual([expect.objectContaining({ path: 'Notes/Trip ideas.md' })]));
    expect(notesWriteCalls[0]).toMatchObject({ title: 'Fall trip' });
    expect((notesWriteCalls[0] as { path?: string }).path).toBeUndefined();
  });

  it('toggles checklist items on the server', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Notes />);

    const checkbox = await screen.findByRole('button', { name: /check pick up groceries/i });
    await user.click(checkbox);

    await waitFor(() => {
      expect(notesCheckOffCalls.length).toBe(1);
    });
    expect(notesCheckOffCalls[0].item).toBe('Pick up groceries');
  });

  it('pins a note and persists the preference locally', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Notes />);

    await screen.findByText('Trip ideas');
    await user.click(screen.getAllByRole('button', { name: /pin note/i })[1]);

    await waitFor(() => {
      const stored = JSON.parse(localStorage.getItem('jarvis_notes_meta_v1') || '{}');
      expect(Object.keys(stored).length).toBe(1);
      expect(Object.values(stored)[0]).toMatchObject({ pinned: true });
    });
  });

  it('filters notes by search text', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Notes />);

    await screen.findByText('Trip ideas');
    await user.type(screen.getByLabelText('Search notes'), 'groceries');

    await waitFor(() => {
      expect(screen.queryByText('Trip ideas')).not.toBeInTheDocument();
    });
    expect(screen.getByText('Shared Checklist')).toBeInTheDocument();
  });

  it('surfaces a failure instead of pretending the list loaded', async () => {
    server.use(
      http.post('/api/communication/notes/list', () =>
        HttpResponse.json({ status: 'FAILURE', message: 'Nextcloud credentials missing.' })
      )
    );
    renderWithProviders(<Notes />);

    await waitFor(() => {
      expect(screen.getByText(/nextcloud credentials missing/i)).toBeInTheDocument();
    });
    // A failed load must not masquerade as "you have no notes"
    expect(screen.queryByText(/no notes yet/i)).not.toBeInTheDocument();
  });
});
