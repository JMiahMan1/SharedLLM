import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { MusicAssistantUsersCard } from '../components/admin/MusicAssistantUsersCard';
import { renderWithProviders } from '../test/render';
import { server } from '../test/setup';

const ACCOUNTS = {
  status: 'SUCCESS',
  server: 'http://music.example.test:8095',
  users: [
    { username: 'Robin', role: 'user', enabled: true },
    { username: 'skye', role: 'admin', enabled: true },
  ],
};

const listAccounts = () => http.get('*/api/media/music-assistant/users', () => HttpResponse.json(ACCOUNTS));

describe('Music Assistant accounts', () => {
  it('lists the accounts Music Assistant has, and links to its own interface', async () => {
    server.use(listAccounts());
    renderWithProviders(<MusicAssistantUsersCard />);

    expect(await screen.findByText('Robin')).toBeInTheDocument();
    expect(screen.getByText('skye')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /open music assistant/i })).toHaveAttribute(
      'href',
      ACCOUNTS.server,
    );
  });

  it('will not send a password shorter than Music Assistant accepts', async () => {
    server.use(listAccounts());
    renderWithProviders(<MusicAssistantUsersCard />);
    const user = userEvent.setup();

    const field = await screen.findByLabelText('Music Assistant password for Robin');
    await user.type(field, 'short');
    expect(screen.getByTestId('ma-password-robin')).toBeDisabled();

    await user.type(field, '-enough');
    expect(screen.getByTestId('ma-password-robin')).toBeEnabled();
  });

  it('sets the password for that person and clears the field afterwards', async () => {
    let sent: unknown = null;
    server.use(
      listAccounts(),
      http.post('*/api/media/music-assistant/users/password', async ({ request }) => {
        sent = await request.json();
        return HttpResponse.json({ status: 'SUCCESS', username: 'Robin', changed: true });
      }),
    );
    renderWithProviders(<MusicAssistantUsersCard />);
    const user = userEvent.setup();

    const field = await screen.findByLabelText('Music Assistant password for Robin');
    await user.type(field, 'long-enough');
    await user.click(screen.getByTestId('ma-password-robin'));

    await waitFor(() => expect(sent).toEqual({ username: 'Robin', password: 'long-enough' }));
    await waitFor(() => expect(field).toHaveValue(''));
  });

  it('says so when Music Assistant cannot be read', async () => {
    server.use(
      http.get('*/api/media/music-assistant/users', () =>
        HttpResponse.json({ status: 'FAILURE', message: 'HomeHub unreachable' }, { status: 502 }),
      ),
    );
    renderWithProviders(<MusicAssistantUsersCard />);

    expect(await screen.findByText(/could not load the music assistant accounts/i)).toBeInTheDocument();
  });
});
