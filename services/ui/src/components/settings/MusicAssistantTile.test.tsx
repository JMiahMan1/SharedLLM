import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import MusicAssistantTile from './MusicAssistantTile';
import { api } from '../../services/api';
import type { UserProfile } from '../../services/api';
import type { MusicAssistantStatus } from '../../lib/musicAssistant';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal() as Record<string, unknown>;
  return {
    ...actual,
    api: { ...(actual.api as Record<string, unknown>), updateProfile: vi.fn(), getMusicAssistantStatus: vi.fn(), revealCredential: vi.fn() },
  };
});

const user = (extra: Partial<UserProfile> = {}): UserProfile =>
  ({ username: 'sam', full_name: 'Sam', role: 'user', is_admin: false, saved_credentials: ['mass_token'], ...extra }) as UserProfile;

const status = (extra: Partial<MusicAssistantStatus>): MusicAssistantStatus => ({
  server: 'household', login: 'own', url: 'http://music.example.test:8095', own_url: null,
  household_url: 'http://music.example.test:8095', household_error: null, can_use_household: false,
  note: 'Music Assistant keeps play history, resume points and playback speed for each person; favourites are shared.',
  ma_username: 'sam', state: 'ok', message: 'Connected as sam.', ...extra,
});

const wrap = (u: UserProfile = user()) =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <MusicAssistantTile userData={u} />
      </MemoryRouter>
    </QueryClientProvider>,
  );

describe('MusicAssistantTile', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows the household server and the person\'s own account, with the shared-favourites note', async () => {
    vi.mocked(api.getMusicAssistantStatus).mockResolvedValue(status({}));
    wrap();
    expect(await screen.findByText('Household server, your account')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Manage Music Assistant' }));
    const dialog = await screen.findByTestId('music-assistant-dialog');
    expect(within(dialog).getByTestId('music-assistant-status')).toHaveTextContent('Connected as sam.');
    expect(within(dialog).getByTestId('music-assistant-note')).toHaveTextContent('favourites are shared');
    expect(within(dialog).queryByRole('button', { name: 'Use the household Music Assistant' })).toBeNull();
  });

  it('shows an own server that does not answer as an error, and offers the household one explicitly', async () => {
    vi.mocked(api.getMusicAssistantStatus).mockResolvedValue(status({
      server: 'own', own_url: 'http://mine.example.test:8095', url: 'http://mine.example.test:8095', can_use_household: true,
      state: 'unreachable', message: 'Your own Music Assistant at http://mine.example.test:8095 did not answer (No answer).',
    }));
    vi.mocked(api.updateProfile).mockResolvedValue(user());
    wrap();
    const line = await screen.findByText('Your server is not answering');
    expect(line).toHaveClass('text-rose-300');
    fireEvent.click(screen.getByRole('button', { name: 'Manage Music Assistant' }));
    const dialog = await screen.findByTestId('music-assistant-dialog');
    expect(within(dialog).getByRole('alert')).toHaveTextContent('did not answer');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Use the household Music Assistant' }));
    await waitFor(() => expect(api.updateProfile).toHaveBeenCalledWith({ clear_fields: ['mass_url'] }));
  });

  it('offers a new login when the household server refuses the token', async () => {
    vi.mocked(api.getMusicAssistantStatus).mockResolvedValue(status({ state: 'token_refused', message: 'Music Assistant refused the access token.' }));
    vi.mocked(api.updateProfile).mockResolvedValue(user());
    wrap();
    expect(await screen.findByText('Token refused')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Manage Music Assistant' }));
    const dialog = await screen.findByTestId('music-assistant-dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Get a new login' }));
    await waitFor(() => expect(api.updateProfile).toHaveBeenCalledWith({ clear_fields: ['mass_token'] }));
  });

  it('saves an own server URL with its token, and refuses an address without a scheme', async () => {
    vi.mocked(api.getMusicAssistantStatus).mockResolvedValue(status({}));
    vi.mocked(api.updateProfile).mockResolvedValue(user());
    wrap();
    fireEvent.click(await screen.findByRole('button', { name: 'Manage Music Assistant' }));
    const dialog = await screen.findByTestId('music-assistant-dialog');
    const urlBox = within(dialog).getByLabelText('Your own server URL (optional)');
    fireEvent.change(urlBox, { target: { value: 'mine.example.test' } });
    expect(within(dialog).getByText('Start the address with http:// or https://')).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Save' })).toBeDisabled();
    fireEvent.change(urlBox, { target: { value: 'http://mine.example.test:8095' } });
    fireEvent.change(within(dialog).getByLabelText('Music Assistant access token'), { target: { value: ' made-up-token ' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(api.updateProfile).toHaveBeenCalledWith({ mass_url: 'http://mine.example.test:8095', mass_token: 'made-up-token' }));
  });

  it('says when nothing is set up, in identity\'s words', async () => {
    vi.mocked(api.getMusicAssistantStatus).mockResolvedValue(status({
      server: 'absent', login: 'absent', url: null, household_url: null, state: 'not_set_up',
      household_error: 'No Music Assistant is set up: add the Music Assistant integration in HomeHub or your own in Accounts & Integrations.',
      message: 'No Music Assistant is set up: add the Music Assistant integration in HomeHub or your own in Accounts & Integrations.',
    }));
    wrap(user({ saved_credentials: [] }));
    expect(await screen.findByText('Not set up')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Manage Music Assistant' }));
    const dialog = await screen.findByTestId('music-assistant-dialog');
    expect(within(dialog).getByRole('alert')).toHaveTextContent('add the Music Assistant integration in HomeHub');
  });
});
