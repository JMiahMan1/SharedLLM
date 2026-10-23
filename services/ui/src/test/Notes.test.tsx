import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Notes from '../pages/Notes';
import { EMPTY_NOTES_INDEX, readNotesSnapshot, writeNotesSnapshot } from '../lib/notesMeta';
import { renderWithProviders } from './render';
import {
  notesCheckOffCalls,
  notesDuplicateCalls,
  notesEmptyTrashCalls,
  notesFixtures,
  notesMetaWrites,
  notesMoveCalls,
  notesReadCalls,
  notesWriteCalls,
  server,
} from './setup';
import { http, HttpResponse } from 'msw';

const lastIndex = (path: string) => {
  const meta = notesMetaWrites.at(-1)?.meta as
    | { notes?: Record<string, { previousPath?: string }> }
    | undefined;
  return meta?.notes?.[path];
};

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
    expect(content.textContent).toBe('Sedona in the fall, then the coast.');
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

  it('pins a note into the shared notes index', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Notes />);

    await screen.findByText('Trip ideas');
    await user.click(screen.getAllByRole('button', { name: /pin note/i })[1]);

    await waitFor(() => {
      const pinned = notesMetaWrites.some(
        (call) => (call.meta as { notes?: Record<string, { pinned?: boolean }> })?.notes?.['Notes/Trip ideas.md']?.pinned,
      );
      expect(pinned).toBe(true);
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

  it('loads every preview with the list instead of one read per note', async () => {
    renderWithProviders(<Notes />);

    expect(await screen.findByText('Pick up groceries')).toBeInTheDocument();
    expect(screen.getByText('Sedona in the fall, then the coast.')).toBeInTheDocument();
    // 24 parallel reads used to fill the server's Nextcloud connections.
    expect(notesReadCalls).toEqual([]);
  });

  it('opens a note from a fresh read, not the preview', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Notes />);

    await user.click(await screen.findByText('Trip ideas'));

    await screen.findByLabelText('Note content');
    expect(notesReadCalls).toEqual([expect.objectContaining({ title: 'Trip ideas' })]);
  });

  it('opens the exact note named in the URL from the dashboard widget', async () => {
    renderWithProviders(<Notes />, { route: '/notes?note=Notes%2FTrip%20ideas.md' });

    const content = await screen.findByLabelText('Note content');
    expect(content.textContent).toBe('Sedona in the fall, then the coast.');
    expect(notesReadCalls).toEqual([expect.objectContaining({ path: 'Notes/Trip ideas.md' })]);
  });

  it('starts a new note from the Android widget quick add link', async () => {
    renderWithProviders(<Notes />, { route: '/notes?new=1' });

    const title = await screen.findByLabelText('Note title');
    expect((title as HTMLInputElement).value).toBe('');
    expect(screen.getByLabelText('Note content').textContent).toBe('');
    expect(notesReadCalls).toEqual([]);
  });

  it('does not open an empty editor when the note cannot be read', async () => {
    const user = userEvent.setup();
    server.use(
      http.post('/api/communication/notes/read', () =>
        HttpResponse.json({ status: 'FAILURE', message: 'Note error: TimeoutError' })
      )
    );
    renderWithProviders(<Notes />);

    await user.click(await screen.findByText('Trip ideas'));

    // Saving an empty editor would have overwritten the note.
    await waitFor(() => expect(screen.queryByText(/opening note/i)).not.toBeInTheDocument());
    expect(screen.queryByLabelText('Note content')).not.toBeInTheDocument();
    expect(notesWriteCalls).toEqual([]);
  });

  it('keeps typing inside the editor without re-reading the list', async () => {
    const user = userEvent.setup();
    let lists = 0;
    server.use(
      http.post('/api/communication/notes/list', async ({ request }) => {
        lists += 1;
        const body = (await request.json()) as { previews?: boolean };
        expect(body.previews).toBe(true);
        return HttpResponse.json({ status: 'SUCCESS', message: 'ok', detail: { notes: [], directories: ['Notes'] } });
      })
    );
    renderWithProviders(<Notes />);

    await user.click(await screen.findByTestId('quick-capture'));
    await user.type(screen.getByLabelText('Note content'), 'a few words');

    expect(screen.getByLabelText('Note content').textContent).toBe('a few words');
    expect(lists).toBe(1);
  });

  it('archives a note by moving it into Notes/Archive and keeps its origin', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Notes />);

    await screen.findByText('Trip ideas');
    await user.click(screen.getByRole('button', { name: 'Archive Trip ideas' }));

    await waitFor(() => expect(notesMoveCalls.length).toBe(1));
    expect(notesMoveCalls[0]).toMatchObject({
      path: 'Notes/Trip ideas.md',
      destination: 'Notes/Archive/Trip ideas.md',
    });
    await waitFor(() => {
      expect(lastIndex('Notes/Archive/Trip ideas.md')?.previousPath).toBe('Notes/Trip ideas.md');
    });
  });

  it('archives several selected notes in one pass', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Notes />);

    await screen.findByText('Trip ideas');
    await user.click(screen.getByTestId('select-notes'));
    await user.click(screen.getByText('Trip ideas'));
    await user.click(screen.getByText('Shared Checklist'));

    expect(screen.getByTestId('bulk-bar')).toHaveTextContent('2 selected');
    await user.click(within(screen.getByTestId('bulk-bar')).getByRole('button', { name: 'Archive' }));

    await waitFor(() => expect(notesMoveCalls.length).toBe(2));
    expect(notesMoveCalls.map((call) => call.destination).sort()).toEqual([
      'Notes/Archive/Shared Checklist.md',
      'Notes/Archive/Trip ideas.md',
    ]);
    await waitFor(() => {
      expect(lastIndex('Notes/Archive/Shared Checklist.md')?.previousPath).toBe('Notes/Shared Checklist.md');
      expect(lastIndex('Notes/Archive/Trip ideas.md')?.previousPath).toBe('Notes/Trip ideas.md');
    });
  });

  it('moves a note to Notes/Trash and restores it to where it lived', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Notes />);

    await screen.findByText('Trip ideas');
    await user.click(screen.getByRole('button', { name: 'Move Trip ideas to trash' }));

    await waitFor(() => expect(notesMoveCalls.length).toBe(1));
    expect(notesMoveCalls[0].destination).toBe('Notes/Trash/Trip ideas.md');

    await user.click(await screen.findByRole('button', { name: /^Trash/ }));
    await user.click(await screen.findByRole('button', { name: 'Restore Trip ideas' }));

    await waitFor(() => expect(notesMoveCalls.length).toBe(2));
    expect(notesMoveCalls[1].destination).toBe('Notes/Trip ideas.md');
  });

  it('moves the card to trash before the server answers', async () => {
    const user = userEvent.setup();
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.post('/api/communication/notes/move', async ({ request }) => {
        const body = (await request.json()) as { path?: string };
        await gate;
        // Model the server actually moving the file before it answers.
        const target = notesFixtures.find((n) => n.path === body.path);
        if (target) target.path = 'Notes/Trash/Trip ideas.md';
        return HttpResponse.json({ status: 'SUCCESS', message: 'Moved', service: 'note_move' });
      }),
    );
    renderWithProviders(<Notes />);

    await screen.findByText('Trip ideas');
    await user.click(screen.getByRole('button', { name: 'Move Trip ideas to trash' }));

    // The card leaves the shelf at once, while the move is still in flight.
    await waitFor(() => expect(screen.queryByText('Trip ideas')).toBeNull());

    release();
    await waitFor(() => expect(screen.queryByText('Trip ideas')).toBeNull());
    await user.click(await screen.findByRole('button', { name: /^Trash/ }));
    expect(await screen.findByRole('button', { name: 'Restore Trip ideas' })).toBeTruthy();
  });

  it('puts the card back when the move is refused', async () => {
    const user = userEvent.setup();
    server.use(
      http.post('/api/communication/notes/move', () =>
        HttpResponse.json({ status: 'FAILURE', message: 'Source is gone' }),
      ),
    );
    renderWithProviders(<Notes />);

    await screen.findByText('Trip ideas');
    await user.click(screen.getByRole('button', { name: 'Move Trip ideas to trash' }));

    // It leaves optimistically, then the refusal brings it home.
    expect(await screen.findByText('Trip ideas')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Restore Trip ideas' })).toBeNull();
  });

  it('duplicates a note through the copy route', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Notes />);

    await screen.findByText('Trip ideas');
    await user.click(screen.getByRole('button', { name: 'Duplicate Trip ideas' }));

    await waitFor(() => expect(notesDuplicateCalls.length).toBe(1));
    expect(notesDuplicateCalls[0]).toMatchObject({ path: 'Notes/Trip ideas.md' });
  });

  it('empties the trash after a confirmation', async () => {
    const user = userEvent.setup();
    vi.stubGlobal('confirm', vi.fn(() => true));
    renderWithProviders(<Notes />);

    await screen.findByText('Trip ideas');
    await user.click(screen.getByRole('button', { name: 'Move Trip ideas to trash' }));
    await waitFor(() => expect(notesMoveCalls.length).toBe(1));

    await user.click(await screen.findByRole('button', { name: /^Trash/ }));
    await user.click(await screen.findByRole('button', { name: 'Empty trash' }));

    await waitFor(() => expect(notesEmptyTrashCalls.length).toBe(1));
    vi.unstubAllGlobals();
  });

  it('shows inline #labels and filters the shelf by one', async () => {
    const user = userEvent.setup();
    notesFixtures.push({ title: 'Weekend plans', path: 'Notes/Weekend plans.md', size: 40, modified: '2026-05-11T12:00:00.000Z' });
    renderWithProviders(<Notes />);

    await screen.findByText('Trip ideas');
    // The card carries its label; the shelf filter carries the same one.
    const cardChip = await screen.findByRole('button', { name: '#outdoors' });
    await user.click(cardChip);

    await waitFor(() => expect(screen.queryByText('Trip ideas')).not.toBeInTheDocument());
    expect(screen.getByText('Weekend plans')).toBeInTheDocument();
  });

  it('understands a tag: search', async () => {
    const user = userEvent.setup();
    notesFixtures.push({ title: 'Weekend plans', path: 'Notes/Weekend plans.md', size: 40, modified: '2026-05-11T12:00:00.000Z' });
    renderWithProviders(<Notes />);

    await screen.findByText('Trip ideas');
    await user.type(screen.getByLabelText('Search notes'), 'tag:outdoors');

    await waitFor(() => expect(screen.queryByText('Trip ideas')).not.toBeInTheDocument());
    expect(screen.getByText('Weekend plans')).toBeInTheDocument();
  });

  it('adds a #label from the editor', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Notes />);

    await screen.findByText('Trip ideas');
    await user.click(screen.getByTestId('quick-capture'));
    await user.type(await screen.findByLabelText('Note label'), 'friends');
    await user.click(screen.getByRole('button', { name: 'Add label' }));

    expect(screen.getByLabelText('Note content').textContent).toContain('#friends');
  });

  it('paints the shelf from the last visit before the list answers', () => {
    writeNotesSnapshot(
      'me',
      [
        {
          title: 'Snapshot note',
          path: 'Notes/Snapshot note.md',
          size: 40,
          modified: '2026-05-11T12:00:00.000Z',
          content: '# Snapshot note\nCategory: Notes\n\nCached line',
        },
      ],
      EMPTY_NOTES_INDEX,
    );

    renderWithProviders(<Notes />);

    // The seeded list renders on the first paint, so nothing shows a spinner.
    expect(screen.getByText('Snapshot note')).toBeInTheDocument();
    expect(screen.queryByText('Loading your notes…')).not.toBeInTheDocument();
  });

  it('keeps the list in a snapshot for the next visit', async () => {
    renderWithProviders(<Notes />);

    await screen.findByText('Trip ideas');
    const snapshot = readNotesSnapshot('me');
    expect(snapshot?.notes).toHaveLength(notesFixtures.length);
    expect(typeof snapshot?.savedAt).toBe('number');
  });

  it('opens a note from the list text without a fresh read wait', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Notes />);

    await screen.findByText('Trip ideas');
    notesReadCalls.length = 0;
    await user.click(screen.getByText('Trip ideas'));

    // The stored text shows at once, then the read reconciles it.
    expect(await screen.findByText(/Sedona in the fall/, { selector: '[role="textbox"] *' })).toBeInTheDocument();
    await waitFor(() => expect(notesReadCalls.length).toBe(1));
  });
});
