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
