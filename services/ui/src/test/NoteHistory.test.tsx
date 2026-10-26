import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import toast from 'react-hot-toast';
import Notes from '../pages/Notes';
import { renderWithProviders } from './render';
import { notesReadCalls, server } from './setup';
import { confirmDialog } from '../components/ui/confirmDialog';

// Destructive actions draw the app's own dialog now, not the browser's.
vi.mock('../components/ui/confirmDialog', () => ({ confirmDialog: vi.fn(async () => true) }));
const confirmMock = vi.mocked(confirmDialog);

type VersionsCall = { path?: string; action?: string; version?: string };

function serveVersions(versions: unknown[], calls: VersionsCall[]) {
  server.use(
    http.post('/api/communication/notes/versions', async ({ request }) => {
      const body = (await request.json()) as VersionsCall;
      calls.push(body);
      if (body.action === 'read') {
        return HttpResponse.json({ status: 'SUCCESS', message: '# Trip ideas\nCategory: Notes\n\nJust Sedona.', service: 'note_version_read' });
      }
      if (body.action === 'restore') {
        return HttpResponse.json({ status: 'SUCCESS', message: 'Earlier version restored.', service: 'note_version_restore' });
      }
      return HttpResponse.json({ status: 'SUCCESS', message: 'ok', service: 'note_versions', detail: { versions } });
    }),
  );
}

describe('Note version history', () => {
  beforeEach(() => {
    localStorage.clear();
    toast.remove();
    confirmMock.mockResolvedValue(true);
  });
  afterEach(() => vi.unstubAllGlobals());

  it('lists versions, shows one and restores it, then reopens the stored note', async () => {
    const user = userEvent.setup();
    const calls: VersionsCall[] = [];
    serveVersions(
      [
        { id: '1700003600', modified: '2023-11-14T23:13:20+00:00', size: 2048, label: 'Before trip', author: 'u' },
        { id: '1700000000', modified: '2023-11-14T22:13:20+00:00', size: 12, label: '', author: '' },
      ],
      calls,
    );
    renderWithProviders(<Notes />);

    await user.click(await screen.findByText('Trip ideas'));
    await screen.findByLabelText('Note content');
    await user.click(screen.getByRole('button', { name: 'History' }));

    const history = await screen.findByTestId('note-history');
    expect(await within(history).findByText('Before trip')).toBeInTheDocument();
    expect(calls[0]).toMatchObject({ path: 'Notes/Trip ideas.md', action: 'list' });

    const buttons = within(history).getAllByRole('button', { pressed: false });
    await user.click(buttons[1]);
    expect(await within(history).findByTestId('note-version-text')).toHaveTextContent('Just Sedona.');

    notesReadCalls.length = 0;
    await user.click(within(history).getByRole('button', { name: /restore this version/i }));
    await waitFor(() => expect(calls.at(-1)).toMatchObject({ action: 'restore', version: '1700000000' }));
    // The editor reopens on the stored (restored) note.
    await waitFor(() => expect(notesReadCalls.length).toBe(1));
    expect(screen.queryByTestId('note-history')).toBeNull();
  });

  it('says plainly when there are no earlier versions', async () => {
    const user = userEvent.setup();
    serveVersions([], []);
    renderWithProviders(<Notes />);

    await user.click(await screen.findByText('Trip ideas'));
    await screen.findByLabelText('Note content');
    await user.click(screen.getByRole('button', { name: 'History' }));

    expect(await screen.findByText(/No earlier versions yet/)).toBeInTheDocument();
  });

  it('shows the reason when the versions cannot be listed', async () => {
    const user = userEvent.setup();
    server.use(
      http.post('/api/communication/notes/versions', () =>
        HttpResponse.json({ status: 'FAILURE', message: 'Nextcloud would not list this note’s versions (HTTP 403).' }),
      ),
    );
    renderWithProviders(<Notes />);

    await user.click(await screen.findByText('Trip ideas'));
    await screen.findByLabelText('Note content');
    await user.click(screen.getByRole('button', { name: 'History' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('HTTP 403');
  });

  it('offers no history for a note that was never saved', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Notes />);
    await user.click(await screen.findByTestId('quick-capture'));
    expect(screen.queryByRole('button', { name: 'History' })).toBeNull();
  });
});
