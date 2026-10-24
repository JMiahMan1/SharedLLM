import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import SpotifyLinkPanel from './SpotifyLinkPanel';
import { api } from '../../services/api';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal() as Record<string, unknown>;
  return {
    ...actual,
    api: { ...(actual.api as Record<string, unknown>), getSpotifyStatus: vi.fn(), startSpotifyLink: vi.fn(), unlinkSpotify: vi.fn() },
  };
});

const wrap = (path = '/identity') =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[path]}>
        <SpotifyLinkPanel />
      </MemoryRouter>
    </QueryClientProvider>,
  );

describe('SpotifyLinkPanel', () => {
  beforeEach(() => vi.clearAllMocks());

  it('says plainly when the server has no Spotify app, and offers nothing', async () => {
    vi.mocked(api.getSpotifyStatus).mockResolvedValue({ configured: false, missing: ['spotify_client_id', 'spotify_redirect_uri'], linked: false });
    wrap();
    expect(await screen.findByTestId('spotify-not-configured')).toHaveTextContent('spotify_client_id, spotify_redirect_uri');
    expect(screen.queryByRole('button', { name: 'Link Spotify' })).toBeNull();
  });

  it('sends the browser to Spotify to link', async () => {
    vi.mocked(api.getSpotifyStatus).mockResolvedValue({ configured: true, missing: [], linked: false });
    vi.mocked(api.startSpotifyLink).mockResolvedValue({ url: 'https://accounts.spotify.com/authorize?x=1' });
    const assign = vi.fn();
    vi.stubGlobal('location', { ...window.location, assign });
    wrap();
    fireEvent.click(await screen.findByRole('button', { name: 'Link Spotify' }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith('https://accounts.spotify.com/authorize?x=1'));
    vi.unstubAllGlobals();
  });

  it('shows the link, its last problem, and unlinks', async () => {
    vi.mocked(api.getSpotifyStatus).mockResolvedValue({
      configured: true, missing: [], linked: true, account_name: 'Emma', last_error: 'Spotify answered 503', last_sync_at: null,
    });
    vi.mocked(api.unlinkSpotify).mockResolvedValue({ linked: false });
    wrap('/identity?spotify=linked');
    expect(await screen.findByTestId('spotify-linked')).toHaveTextContent('Linked as Emma');
    expect(screen.getByText('Spotify is linked.')).toBeInTheDocument();
    expect(screen.getByText(/Last sync failed: Spotify answered 503/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Unlink Spotify' }));
    await waitFor(() => expect(api.unlinkSpotify).toHaveBeenCalled());
  });
});
