import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import toast from 'react-hot-toast';
import Notes from '../pages/Notes';
import { readNoteDrafts, writeNoteDraft } from '../lib/noteDrafts';
import { renderWithProviders } from './render';
import { notesVersions, notesWriteCalls, server } from './setup';

type WriteCall = { title?: string; content?: string; path?: string; if_match?: string };
const writes = () => notesWriteCalls as WriteCall[];

describe('Notes autosave and drafts', () => {
  beforeEach(() => {
    localStorage.clear();
    toast.remove();
  });

  it('saves an open note by itself, in place and only over the version it opened', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Notes />);

    await user.click(await screen.findByText('Trip ideas'));
    const content = await screen.findByLabelText('Note content');
    // The stored read lands first and hands over the file's version.
    await waitFor(() => expect(content.textContent).toBe('Sedona in the fall, then the coast.'));
    await user.click(content);
    await user.type(content, ' Bring boots.');

    await waitFor(() => expect(writes().length).toBe(1), { timeout: 4000 });
    expect(writes()[0]).toMatchObject({ path: 'Notes/Trip ideas.md', if_match: '"v1"' });
    expect(writes()[0].content).toContain('Bring boots.');
    // Still open: autosave is not a close.
    expect(screen.getByLabelText('Note content')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('note-save-status')).toHaveTextContent('Saved'));
    expect(readNoteDrafts('me')).toEqual({});
  });

  it('refuses to overwrite a note changed elsewhere and keeps both on request', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Notes />);

    await user.click(await screen.findByText('Trip ideas'));
    const content = await screen.findByLabelText('Note content');
    await waitFor(() => expect(content.textContent).toBe('Sedona in the fall, then the coast.'));
    // Someone edits the same note on another device.
    notesVersions['Notes/Trip ideas.md'] = 2;
    await user.click(content);
    await user.type(content, ' Mine.');

    expect(await screen.findByRole('alert', {}, { timeout: 4000 })).toHaveTextContent(/changed somewhere else/);
    // The one attempt carried the old version and was refused.
    expect(writes()).toHaveLength(1);
    expect(writes()[0].if_match).toBe('"v1"');
    // Nothing is lost while the choice is open.
    expect(readNoteDrafts('me')['Notes/Trip ideas.md']?.content).toContain('Mine.');

    await user.click(screen.getByRole('button', { name: 'Keep both' }));
    await waitFor(() => expect(writes().length).toBe(2));
    expect(writes()[1]).toMatchObject({ title: 'Trip ideas (my copy)' });
    expect(writes()[1].path).toBeUndefined();
    expect(writes()[1].content).toContain('Mine.');
    await waitFor(() => expect(screen.queryByLabelText('Note content')).not.toBeInTheDocument());
    expect(readNoteDrafts('me')).toEqual({});
  });

  it('drops my edit for the stored version when I choose it', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Notes />);

    await user.click(await screen.findByText('Trip ideas'));
    const content = await screen.findByLabelText('Note content');
    await waitFor(() => expect(content.textContent).toBe('Sedona in the fall, then the coast.'));
    notesVersions['Notes/Trip ideas.md'] = 2;
    await user.type(content, ' Mine.');
    await screen.findByRole('alert', {}, { timeout: 4000 });

    await user.click(screen.getByRole('button', { name: 'Use the stored version' }));

    await waitFor(() =>
      expect(screen.getByLabelText('Note content').textContent).toBe('Sedona in the fall, then the coast.'),
    );
    expect(screen.queryByRole('alert')).toBeNull();
    expect(readNoteDrafts('me')).toEqual({});
  });

  it('compares the stored text before saving when there is no version to check', async () => {
    const user = userEvent.setup();
    let stored = '# Trip ideas\nCategory: Notes\n\nSedona in the fall, then the coast.';
    server.use(
      http.post('/api/communication/notes/read', () =>
        HttpResponse.json({ status: 'SUCCESS', message: stored, service: 'note_read', detail: { etag: null } }),
      ),
    );
    renderWithProviders(<Notes />);

    await user.click(await screen.findByText('Trip ideas'));
    const content = await screen.findByLabelText('Note content');
    stored = '# Trip ideas\nCategory: Notes\n\nChanged on the laptop.';
    await user.click(content);
    await user.type(content, ' Mine.');
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent(/changed somewhere else/);
    expect(writes()).toEqual([]);
  });

  it('saves pending changes when the sheet is closed', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Notes />);

    await user.click(await screen.findByTestId('quick-capture'));
    await user.type(screen.getByLabelText('Note title'), 'Grocery run');
    await user.type(screen.getByLabelText('Note content'), 'milk');
    // A new note never autosaves (one file per name, not per keystroke) …
    expect(screen.getByTestId('note-save-status')).toHaveTextContent('Kept on this device until you save');
    await user.click(screen.getByRole('button', { name: 'Close' }));

    // … but closing the sheet saves it, as in Keep.
    await waitFor(() => expect(writes().length).toBe(1));
    expect(writes()[0]).toMatchObject({ title: 'Grocery run' });
    await waitFor(() => expect(screen.queryByLabelText('Note content')).not.toBeInTheDocument());
    expect(readNoteDrafts('me')).toEqual({});
  });

  it('closes an untouched note without writing anything', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Notes />);

    await user.click(await screen.findByText('Trip ideas'));
    await screen.findByLabelText('Note content');
    await user.click(screen.getByRole('button', { name: 'Close' }));

    await waitFor(() => expect(screen.queryByLabelText('Note content')).not.toBeInTheDocument());
    expect(writes()).toEqual([]);
  });

  it('keeps a draft when the app closes mid-edit and offers it back next time', async () => {
    const user = userEvent.setup();
    const first = renderWithProviders(<Notes />);

    await user.click(await screen.findByTestId('quick-capture'));
    await user.type(screen.getByLabelText('Note title'), 'Packing list');
    await user.type(screen.getByLabelText('Note content'), 'socks');
    // The app goes away without a save (closed, or the webview was killed).
    first.unmount();
    expect(Object.values(readNoteDrafts('me')).map((d) => d.title)).toEqual(['Packing list']);

    renderWithProviders(<Notes />);
    const offer = await screen.findByTestId('note-drafts');
    expect(offer).toHaveTextContent('Unsaved new note Packing list');
    await user.click(screen.getByRole('button', { name: 'Restore' }));

    expect(screen.getByLabelText('Note content').textContent).toBe('socks');
    await user.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(writes().length).toBe(1));
    expect(writes()[0]).toMatchObject({ title: 'Packing list' });
    expect(writes()[0].content).toContain('socks');
    await waitFor(() => expect(readNoteDrafts('me')).toEqual({}));
    expect(screen.queryByTestId('note-drafts')).toBeNull();
  });

  it('opens a note with its kept changes and can discard them for the stored text', async () => {
    const user = userEvent.setup();
    writeNoteDraft('me', 'Notes/Trip ideas.md', {
      path: 'Notes/Trip ideas.md',
      title: 'Trip ideas',
      content: 'Sedona, then Moab.',
      folder: '',
      color: 'default',
      pinned: false,
      original: { title: 'Trip ideas', category: 'Notes' },
      etag: '"v1"',
      base: 'Sedona in the fall, then the coast.',
    });
    renderWithProviders(<Notes />);

    // The card (the drafts offer names the note too).
    await user.click(await screen.findByRole('heading', { name: 'Trip ideas' }));
    expect((await screen.findByLabelText('Note content')).textContent).toBe('Sedona, then Moab.');
    await user.click(screen.getByRole('button', { name: 'Discard them' }));

    await waitFor(() =>
      expect(screen.getByLabelText('Note content').textContent).toBe('Sedona in the fall, then the coast.'),
    );
    expect(readNoteDrafts('me')).toEqual({});
  });

  it('discards a kept draft from the offer', async () => {
    const user = userEvent.setup();
    writeNoteDraft('me', 'new:1', { title: 'Old idea', content: 'x', folder: '', color: 'default', pinned: false });
    renderWithProviders(<Notes />);

    await screen.findByTestId('note-drafts');
    await user.click(screen.getByRole('button', { name: 'Discard' }));

    expect(screen.queryByTestId('note-drafts')).toBeNull();
    expect(readNoteDrafts('me')).toEqual({});
  });
});
