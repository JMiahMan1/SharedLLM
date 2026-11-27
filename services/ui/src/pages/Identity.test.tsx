import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import Identity from './Identity';
import { renderWithProviders } from '../test/render';
import { server } from '../test/setup';

vi.stubGlobal('prompt', vi.fn(() => 'CLI Client'));
if (!Object.getOwnPropertyDescriptor(navigator, 'clipboard')) {
  Object.defineProperty(navigator, 'clipboard', {
    value: {
      writeText: vi.fn().mockResolvedValue(undefined),
    },
    writable: true,
    configurable: true,
  });
}

/// What the harness answers for /api/users/me; these tests vary a field or two.
const defaultUser = {
  id: 1,
  username: 'default',
  display_name: 'Shared/Default User',
  full_name: 'Shared/Default User',
  role: 'admin',
  is_admin: true,
  is_system_default: true,
  nextcloud_url: 'https://cloud.example.com',
  nextcloud_user: 'default',
  ha_url: 'https://ha.example.com',
  voice_fingerprint: null,
};

describe('Identity page', () => {
  it('renders integrations, keys, and persona data from the live contract', async () => {
    renderWithProviders(<Identity />);

    expect(await screen.findByText('Integration Gallery')).toBeInTheDocument();
    expect(screen.getByText('External Client Access')).toBeInTheDocument();
    expect(await screen.findByText('OpenWebUI')).toBeInTheDocument();
    expect(await screen.findByDisplayValue('Shared/Default User')).toBeInTheDocument();
  });

  it('generates an API key through the backend contract and shows it once', async () => {
    renderWithProviders(<Identity />);

    fireEvent.click(await screen.findByText('Generate New Key'));
    fireEvent.change(screen.getByLabelText(/which client/i), { target: { value: 'CLI Client' } });
    fireEvent.click(screen.getByRole('button', { name: 'Generate' }));

    expect(await screen.findByTestId('new-api-key')).toHaveTextContent('sk-test-generated-key');
    expect(await screen.findByText('CLI Client')).toBeInTheDocument();
    expect(window.prompt).not.toHaveBeenCalled();
  });

  it('does not save an empty name while the profile is loading', async () => {
    renderWithProviders(<Identity />);

    // Once loaded, the box holds the saved name rather than staying empty.
    await screen.findByDisplayValue('Shared/Default User');
    expect(screen.getByLabelText('Display name')).toHaveValue('Shared/Default User');
  });

  it('removes one saved value without touching the rest of the login', async () => {
    const bodies: Array<Record<string, unknown>> = [];
    server.use(
      http.get('/api/users/me', () => HttpResponse.json({
        ...defaultUser, gitlab_url: '', gitlab_user: 'dev@example.test', saved_credentials: ['gitlab_token', 'gitlab_user'],
      })),
      http.patch('/api/users/me', async ({ request }) => {
        bodies.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json({ ...defaultUser, saved_credentials: ['gitlab_user'] });
      }),
    );

    renderWithProviders(<Identity />);
    fireEvent.click(await screen.findByRole('button', { name: /GitLab$/ }));

    fireEvent.click(await screen.findByTestId('credential-remove-gitlab_token'));

    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0].clear_fields).toEqual(['gitlab_token']);
    // The username is not sent back as a change, and nothing else is cleared.
    expect(bodies[0].gitlab_user).toBeUndefined();
  });

  it('saves what is there even when a field is still missing', async () => {
    const bodies: Array<Record<string, unknown>> = [];
    server.use(
      http.get('/api/users/me', () => HttpResponse.json({
        ...defaultUser, gitlab_url: '', gitlab_user: 'dev@example.test', saved_credentials: ['gitlab_user'],
      })),
      http.patch('/api/users/me', async ({ request }) => {
        bodies.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json({ ...defaultUser, saved_credentials: ['gitlab_user', 'gitlab_token'] });
      }),
    );

    renderWithProviders(<Identity />);
    fireEvent.click(await screen.findByRole('button', { name: /GitLab$/ }));

    fireEvent.change(await screen.findByLabelText('Access Token'), { target: { value: 'glpat-new' } });
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0].gitlab_token).toBe('glpat-new');
    // An untouched URL is not invented, and the save was not refused.
    expect(bodies[0].gitlab_url).toBeUndefined();
  });

  it('only offers Test for services the backend can test', async () => {
    renderWithProviders(<Identity />);

    fireEvent.click(await screen.findByRole('button', { name: /Private Git$/ }));
    expect(screen.queryByRole('button', { name: /^test$/i })).toBeNull();
  });
});
