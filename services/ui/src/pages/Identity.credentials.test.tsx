import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import Identity from './Identity';
import { renderWithProviders } from '../test/render';
import { server } from '../test/setup';

const openTile = async (name: 'Home Assistant' | 'Music Assistant' | 'Audiobookshelf' | 'Nextcloud') => {
  const user = userEvent.setup();
  await user.click(await screen.findByRole('button', { name: new RegExp(`${name}$`) }));
  return user;
};

describe('Identity credentials', () => {
  it('offers a Music Assistant tile with a URL and a token field', async () => {
    renderWithProviders(<Identity />);

    await openTile('Music Assistant');

    expect(await screen.findByText('Server URL')).toBeInTheDocument();
    expect(screen.getByText('Access Token')).toBeInTheDocument();
  });

  it('offers an Audiobookshelf API key alongside username and password', async () => {
    renderWithProviders(<Identity />);

    await openTile('Audiobookshelf');

    expect(await screen.findByText('API Key')).toBeInTheDocument();
    expect(screen.getByText('Username')).toBeInTheDocument();
    expect(screen.getByText('Password')).toBeInTheDocument();
  });

  it('replaces the dead data-sharing toggle with real per-service grants', async () => {
    renderWithProviders(<Identity />);

    await openTile('Home Assistant');

    const card = await screen.findByTestId('shared-credentials');
    expect(card).toHaveTextContent(/own credentials always win/i);
    expect(screen.queryByText('Data Sharing Rule')).not.toBeInTheDocument();

    for (const label of ['Home Assistant', 'Music Assistant', 'Audiobookshelf', 'Nextcloud']) {
      expect(screen.getByRole('checkbox', { name: `Use shared ${label} credentials` })).toBeInTheDocument();
    }
  });

  it('lets an admin grant and revoke a shared service', async () => {
    renderWithProviders(<Identity />);
    const user = await openTile('Nextcloud');

    const card = await screen.findByTestId('shared-credentials');
    const maToggle = () =>
      screen.getByRole('checkbox', { name: 'Use shared Music Assistant credentials' });
    expect(maToggle()).not.toBeChecked();

    await user.click(maToggle());
    await waitFor(() => expect(maToggle()).toBeChecked());
    expect(card).toHaveTextContent(/Last changed by/);
    expect(card).toHaveTextContent('default');

    await user.click(maToggle());
    await waitFor(() => expect(maToggle()).not.toBeChecked());
  });

  it('shows a non-admin the read-only view and leaves the toggles disabled', async () => {
    server.use(
      http.get('/api/users/me', () =>
        HttpResponse.json({
          id: 7,
          username: 'casey',
          display_name: 'Casey',
          is_admin: false,
          is_system_default: false,
          ha_url: 'https://ha.example.com',
        }),
      ),
      http.get('/api/users/:username/credential-shares', ({ params }) =>
        HttpResponse.json({
          username: String(params.username),
          services: ['audiobookshelf'],
          shared_owner: 'default',
        }),
      ),
    );

    renderWithProviders(<Identity />);
    await openTile('Home Assistant');

    const card = await screen.findByTestId('shared-credentials');
    expect(card).toHaveTextContent(/An admin grants or revokes shared access/);
    expect(screen.getByRole('checkbox', { name: 'Use shared Audiobookshelf credentials' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'Use shared Nextcloud credentials' })).toBeDisabled();
  });
});
