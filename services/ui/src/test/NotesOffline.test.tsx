import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { act, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import toast from 'react-hot-toast';
import Notes from '../pages/Notes';
import { readNoteDrafts, writeNoteDraft } from '../lib/noteDrafts';
import { isOfflineError } from '../lib/noteOutbox';
import { renderWithProviders } from './render';
import { notesVersions, notesWriteCalls } from './setup';

type WriteCall = { title?: string; content?: string; path?: string; if_match?: string };
const writes = () => notesWriteCalls as WriteCall[];

let online = true;
function goOnline() {
  online = true;
  act(() => {
    window.dispatchEvent(new Event('online'));
  });
}

const tripDraft = (extra: Record<string, unknown> = {}) => ({
  path: 'Notes/Trip ideas.md',
  title: 'Trip ideas',
  content: 'Sedona in the fall. Pack layers.',
  folder: '',
  color: 'default' as const,
  pinned: false,
  original: { title: 'Trip ideas', category: 'Notes' },
  etag: '"v1"',
  base: 'Sedona in the fall, then the coast.',
  queued: true,
  ...extra,
});

describe('Notes offline edits', () => {
  beforeEach(() => {
    localStorage.clear();
    toast.remove();
    online = true;
    Object.defineProperty(window.navigator, 'onLine', { configurable: true, get: () => online });
  });
  afterEach(() => {
    online = true;
  });

  it('queues an edit made offline and saves it with its version once back online', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Notes />);
    await user.click(await screen.findByText('Trip ideas'));
    const content = await screen.findByLabelText('Note content');
    await waitFor(() => expect(content.textContent).toBe('Sedona in the fall, then the coast.'));

    online = false;
    await user.click(content);
    await user.type(content, ' Offline line.');
    await waitFor(() => expect(screen.getByTestId('note-save-status')).toHaveTextContent(/offline/i), { timeout: 4000 });
    expect(writes()).toEqual([]);
    expect(readNoteDrafts('me')['Notes/Trip ideas.md']).toMatchObject({ queued: true, etag: '"v1"' });

    goOnline();
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toMatchObject({ path: 'Notes/Trip ideas.md', if_match: '"v1"' });
    expect(writes()[0].content).toContain('Offline line.');
    await waitFor(() => expect(readNoteDrafts('me')).toEqual({}));
  });

  it('keeps an offline edit when the sheet closes, and the shelf saves it when the connection returns', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Notes />);
    await user.click(await screen.findByText('Trip ideas'));
    const content = await screen.findByLabelText('Note content');
    await waitFor(() => expect(content.textContent).toBe('Sedona in the fall, then the coast.'));

    online = false;
    await user.click(content);
    await user.type(content, ' Closed offline.');
    await user.click(screen.getByRole('button', { name: 'Close' }));
    expect(await screen.findByTestId('note-draft-queued')).toBeInTheDocument();
    expect(writes()).toEqual([]);

    goOnline();
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toMatchObject({ path: 'Notes/Trip ideas.md', if_match: '"v1"' });
    expect(writes()[0].content).toContain('Closed offline.');
    await waitFor(() => expect(screen.queryByTestId('note-drafts')).toBeNull());
  });

  it('turns a replay over a note changed elsewhere into a choice, not an overwrite', async () => {
    const user = userEvent.setup();
    writeNoteDraft('me', 'Notes/Trip ideas.md', tripDraft());
    notesVersions['Notes/Trip ideas.md'] = 2; // edited on another device meanwhile
    renderWithProviders(<Notes />);

    // Replayed on load with the old version: refused, nothing overwritten.
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0].if_match).toBe('"v1"');
    await waitFor(() => expect(readNoteDrafts('me')['Notes/Trip ideas.md']).toMatchObject({ conflict: true, queued: false }));
    expect(await screen.findByText(/changed somewhere else meanwhile/i)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Restore' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/changed somewhere else/);
    expect(screen.getByRole('button', { name: 'Keep both' })).toBeInTheDocument();
    expect(writes()).toHaveLength(1);
  });

  it('treats a replay whose first attempt already landed as saved', async () => {
    // The stored note already holds this text: the answer was lost, not the save.
    writeNoteDraft('me', 'Notes/Trip ideas.md', tripDraft({ content: 'Sedona in the fall, then the coast.', etag: '"v0"' }));
    renderWithProviders(<Notes />);
    await waitFor(() => expect(writes()).toHaveLength(1));
    await waitFor(() => expect(readNoteDrafts('me')).toEqual({}));
  });

  it('leaves queued edits alone while still offline', async () => {
    online = false;
    writeNoteDraft('me', 'Notes/Trip ideas.md', tripDraft());
    renderWithProviders(<Notes />);
    expect(await screen.findByTestId('note-draft-queued')).toBeInTheDocument();
    expect(writes()).toEqual([]);
  });

  it('tells a lost connection from a refusal', () => {
    expect(isOfflineError({ code: 'ERR_NETWORK' })).toBe(true);
    expect(isOfflineError({ code: 'ERR_NETWORK', response: { status: 502 } })).toBe(false);
    expect(isOfflineError({ code: 'ECONNABORTED' })).toBe(false);
    expect(isOfflineError(new Error('Save failed'))).toBe(false);
    online = false;
    expect(isOfflineError(new Error('anything'))).toBe(true);
  });
});
