import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import toast from 'react-hot-toast';
import Notes from '../pages/Notes';
import { renderWithProviders } from './render';
import { server } from './setup';

type ShareCall = { action?: string; path?: string; share_with?: string; can_edit?: boolean; share_id?: string };
type Share = { id: string; with: string; name: string; can_edit: boolean };

/** A fake Nextcloud sharing API holding one note's shares. */
function serveSharing(calls: ShareCall[], people: Array<{ id: string; name: string }>, shares: Share[]) {
  server.use(
    http.post('/api/communication/notes/share', async ({ request }) => {
      const body = (await request.json()) as ShareCall;
      calls.push(body);
      if (body.action === 'people') return HttpResponse.json({ status: 'SUCCESS', message: 'ok', detail: { people } });
      if (body.action === 'add') {
        const who = people.find((p) => p.id === body.share_with)!;
        shares.push({ id: String(shares.length + 20), with: who.id, name: who.name, can_edit: Boolean(body.can_edit) });
        return HttpResponse.json({ status: 'SUCCESS', message: 'Shared.' });
      }
      if (body.action === 'remove') {
        shares.splice(shares.findIndex((s) => s.id === body.share_id), 1);
        return HttpResponse.json({ status: 'SUCCESS', message: 'No longer shared.' });
      }
      return HttpResponse.json({ status: 'SUCCESS', message: 'ok', detail: { shares: [...shares] } });
    }),
  );
}

async function openSharing(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByText('Trip ideas'));
  await screen.findByLabelText('Note content');
  await user.click(screen.getByRole('button', { name: 'Share' }));
  return screen.findByTestId('note-share');
}

describe('Note sharing', () => {
  beforeEach(() => {
    localStorage.clear();
    toast.remove();
  });

  it('shares a note with a household member and can stop sharing', async () => {
    const user = userEvent.setup();
    const calls: ShareCall[] = [];
    serveSharing(calls, [{ id: 'household', name: 'Household' }, { id: 'jarvis', name: 'Jarvis' }], []);
    renderWithProviders(<Notes />);

    const panel = await openSharing(user);
    expect(await within(panel).findByText('Only you so far.')).toBeInTheDocument();
    await user.selectOptions(await within(panel).findByLabelText('Share with'), 'household');
    await user.click(within(panel).getByLabelText('Can edit'));
    await user.click(within(panel).getByRole('button', { name: 'Share' }));

    await waitFor(() =>
      expect(calls.find((c) => c.action === 'add')).toMatchObject({
        path: 'Notes/Trip ideas.md',
        share_with: 'household',
        can_edit: false,
      }),
    );
    expect(await within(panel).findByText('Household')).toBeInTheDocument();
    expect(within(panel).getByText(/can view/)).toBeInTheDocument();
    // Someone already shared with is not offered again.
    expect(within(within(panel).getByLabelText('Share with')).queryByRole('option', { name: 'Household' })).toBeNull();

    await user.click(within(panel).getByRole('button', { name: 'Stop sharing with Household' }));
    await waitFor(() => expect(calls.find((c) => c.action === 'remove')).toMatchObject({ share_id: '20' }));
    expect(await within(panel).findByText('Only you so far.')).toBeInTheDocument();
  });

  it('says so when there is no one to share with', async () => {
    const user = userEvent.setup();
    serveSharing([], [], []);
    renderWithProviders(<Notes />);

    const panel = await openSharing(user);
    expect(await within(panel).findByText('No one else on this Nextcloud to share with.')).toBeInTheDocument();
  });

  it('shows Nextcloud’s reason when sharing cannot be listed', async () => {
    const user = userEvent.setup();
    server.use(
      http.post('/api/communication/notes/share', () =>
        HttpResponse.json({ status: 'FAILURE', message: 'Sharing is disabled for this user' }),
      ),
    );
    renderWithProviders(<Notes />);

    const panel = await openSharing(user);
    const alerts = await within(panel).findAllByRole('alert');
    expect(alerts[0]).toHaveTextContent('Sharing is disabled for this user');
  });
});
