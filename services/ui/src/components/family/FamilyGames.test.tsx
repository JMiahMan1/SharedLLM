import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import FamilyGames from './FamilyGames';
import { api } from '../../services/api';
import type { FamilyGameCard, FamilyGamesResponse } from '../../types/api';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return {
    ...actual,
    api: {
      ...(actual.api as Record<string, unknown>),
      getFamilyGames: vi.fn(),
      setFamilyGameState: vi.fn(),
      rateArcadeGame: vi.fn(),
      getArcadeScoreboard: vi.fn(),
      getArcadeHighscores: vi.fn(),
      submitArcadeScore: vi.fn(),
    },
  };
});

const m = {
  getFamilyGames: vi.mocked(api.getFamilyGames),
  setState: vi.mocked(api.setFamilyGameState),
  rate: vi.mocked(api.rateArcadeGame),
  scoreboard: vi.mocked(api.getArcadeScoreboard),
  highscores: vi.mocked(api.getArcadeHighscores),
  submit: vi.mocked(api.submitArcadeScore),
};

function card(slug: string, overrides: Partial<FamilyGameCard> = {}): FamilyGameCard {
  return {
    slug,
    title: slug.replace('family-', '').replace(/-/g, ' '),
    collection: 'family',
    family_state: 'published',
    source: 'built-in',
    emoji: '☁️',
    tagline: 'Hop to the stars',
    art: { from: '#123456', to: '#654321' },
    rating: { count: 4, average: 4.5 },
    plays: 3,
    on_shelf: true,
    shelf_reason: '',
    ...overrides,
  };
}

const base: FamilyGamesResponse = {
  success: true,
  arcade_available: true,
  play_base: 'https://games.sumemail.com',
  is_admin: false,
  min_rating: 4,
  min_votes: 2,
  games: [card('family-cloud-hop')],
  review: [],
  unpublished: [],
};

function renderIt() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <FamilyGames />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  m.scoreboard.mockResolvedValue({
    success: true,
    arcade_available: true,
    players: [
      {
        username: 'kate',
        display_name: 'Kate',
        initials: 'KAT',
        player_url: 'https://games.sumemail.com/player/KAT',
        you: true,
        best_score: 0,
        total_games: 1,
        games_scored: 0,
        submits: 0,
        podiums: 0,
        crowns: 0,
      },
    ],
  });
  m.highscores.mockResolvedValue({ success: true, arcade_available: true, players: [], games: [] });
});

describe('FamilyGames', () => {
  it('shows players the shelf with no admin controls', async () => {
    m.getFamilyGames.mockResolvedValue(base);
    renderIt();
    expect(await screen.findByTestId('family-game-family-cloud-hop')).toBeInTheDocument();
    expect(screen.queryByTestId('family-admin')).not.toBeInTheDocument();
    expect(screen.getByTestId('family-rating-family-cloud-hop')).toHaveTextContent('4.5 · 4 votes');
    expect(m.scoreboard).toHaveBeenCalledWith('family');
    expect(m.highscores).toHaveBeenCalledWith('family');
  });

  it('tells players when nothing has made the shelf yet', async () => {
    m.getFamilyGames.mockResolvedValue({ ...base, games: [] });
    renderIt();
    expect(await screen.findByTestId('family-games-empty')).toHaveTextContent('being tested');
  });

  it('renders an honest offline card', async () => {
    m.getFamilyGames.mockResolvedValue({ ...base, arcade_available: false, error: 'Arcade unreachable: boom', games: [] });
    renderIt();
    expect(await screen.findByTestId('family-games-offline')).toHaveTextContent('Arcade unreachable: boom');
  });

  it('lets an admin test, see why a game is held back, and publish it', async () => {
    m.getFamilyGames.mockResolvedValue({
      ...base,
      is_admin: true,
      admin_token_configured: true,
      review: [card('family-petal-swap', { family_state: 'testing', on_shelf: false, shelf_reason: 'In testing' })],
      unpublished: [card('family-orbit-guard', { family_state: 'unpublished', on_shelf: false, shelf_reason: 'Unpublished' })],
    });
    m.setState.mockResolvedValue({ success: true, state: 'published' });
    renderIt();

    const row = await screen.findByTestId('family-admin-family-petal-swap');
    expect(within(row).getByTestId('family-reason-family-petal-swap')).toHaveTextContent('In testing');
    expect(within(row).getByRole('button', { name: /Test play/ })).toBeInTheDocument();
    fireEvent.click(within(row).getByRole('button', { name: /Publish/ }));
    await waitFor(() => expect(m.setState).toHaveBeenCalledWith('family-petal-swap', 'published'));

    const retired = screen.getByTestId('family-admin-family-orbit-guard');
    expect(within(retired).queryByRole('button', { name: /Test play/ })).not.toBeInTheDocument();
    fireEvent.click(within(retired).getByRole('button', { name: /Back to testing/ }));
    await waitFor(() => expect(m.setState).toHaveBeenCalledWith('family-orbit-guard', 'testing'));
  });

  it('warns the admin and disables publishing when the arcade token is missing', async () => {
    m.getFamilyGames.mockResolvedValue({
      ...base,
      is_admin: true,
      admin_token_configured: false,
      review: [card('family-petal-swap', { family_state: 'testing', on_shelf: false, shelf_reason: 'In testing' })],
    });
    renderIt();
    expect(await screen.findByTestId('family-token-missing')).toHaveTextContent('ARCADE_ADMIN_TOKEN');
    const row = screen.getByTestId('family-admin-family-petal-swap');
    expect(within(row).getByRole('button', { name: /Publish/ })).toBeDisabled();
  });

  it('votes with large tap targets', async () => {
    m.getFamilyGames.mockResolvedValue(base);
    m.rate.mockResolvedValue({ success: true, rating: { count: 5, average: 4.6 } });
    renderIt();
    const vote = await screen.findByTestId('family-vote-family-cloud-hop');
    const five = within(vote).getByRole('button', { name: /5 stars/ });
    expect(five.className).toContain('h-11');
    fireEvent.click(five);
    await waitFor(() => expect(m.rate).toHaveBeenCalledWith('family-cloud-hop', 5));
  });

  it('saves a finished run from the embedded game once, under the signed-in member', async () => {
    m.getFamilyGames.mockResolvedValue(base);
    m.submit.mockResolvedValue({ success: true, rank: 1 });
    renderIt();
    fireEvent.click(await screen.findByRole('button', { name: 'Play cloud hop' }));
    const frame = (await screen.findByTestId('family-player-frame')) as HTMLIFrameElement;
    expect(frame.src).toBe('https://games.sumemail.com/play/family-cloud-hop?embed=1&player=KAT');

    const post = (data: unknown, origin = 'https://games.sumemail.com') =>
      window.dispatchEvent(new MessageEvent('message', { data, origin }));
    const run = { source: 'arcade-score', slug: 'family-cloud-hop', score: 420, capture_id: 'family-cloud-hop:r1' };
    post(run, 'https://evil.example');
    post(run);
    post(run);
    await waitFor(() => expect(m.submit).toHaveBeenCalledTimes(1));
    expect(m.submit).toHaveBeenCalledWith('family-cloud-hop', 420, 'family-cloud-hop:r1');
  });
});
