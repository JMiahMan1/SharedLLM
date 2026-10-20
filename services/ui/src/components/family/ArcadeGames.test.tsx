import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import ArcadeGames from './ArcadeGames';
import { api } from '../../services/api';
import type { ArcadeGamesResponse, ArcadeHighscoresResponse, ArcadeScoreboardResponse } from '../../types/api';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return {
    ...actual,
    api: {
      ...(actual.api as Record<string, unknown>),
      getArcadeGames: vi.fn(),
      rateArcadeGame: vi.fn(),
      getArcadeScoreboard: vi.fn(),
      getArcadeHighscores: vi.fn(),
      submitArcadeScore: vi.fn(),
    },
  };
});

const mockedGetArcadeGames = vi.mocked(api.getArcadeGames);
const mockedRateArcadeGame = vi.mocked(api.rateArcadeGame);
const mockedGetArcadeScoreboard = vi.mocked(api.getArcadeScoreboard);
const mockedGetArcadeHighscores = vi.mocked(api.getArcadeHighscores);
const mockedSubmitArcadeScore = vi.mocked(api.submitArcadeScore);

function renderShelf() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ArcadeGames />
    </QueryClientProvider>,
  );
}

const shelf: ArcadeGamesResponse = {
  success: true,
  arcade_available: true,
  play_base: 'https://games.sumemail.com',
  featured_source: 'admin',
  featured: [
    {
      slug: 'space-invaders',
      title: 'Space Invaders',
      category: 'arcade',
      kind: 'playable',
      plays: 12,
      top_score: 900,
      rating: { count: 3, average: 4.67 },
      featured: true,
    },
  ],
  games: [
    {
      slug: 'space-invaders',
      title: 'Space Invaders',
      category: 'arcade',
      kind: 'playable',
      plays: 12,
      top_score: 900,
      rating: { count: 3, average: 4.67 },
      featured: true,
    },
    { slug: 'breakout', title: 'Breakout', category: 'classic', kind: 'code', plays: 0, rating: { count: 0, average: 0 } },
  ],
  count: 2,
};

const scoreboard: ArcadeScoreboardResponse = {
  success: true,
  arcade_available: true,
  players: [
    {
      username: 'jeremiah',
      display_name: 'Jeremiah',
      initials: 'JER',
      player_url: 'https://games.sumemail.com/player/JER',
      you: true,
      best_score: 62960,
      total_games: 20,
      games_scored: 9,
      submits: 15,
      podiums: 9,
      crowns: 9,
    },
    {
      username: 'michele',
      display_name: 'Michele',
      initials: 'MIC',
      player_url: 'https://games.sumemail.com/player/MIC',
      you: false,
      best_score: 0,
      total_games: 20,
      games_scored: 0,
      submits: 0,
      podiums: 0,
      crowns: 0,
    },
  ],
};

const highscores: ArcadeHighscoresResponse = {
  success: true,
  arcade_available: true,
  players: [
    {
      player_id: 'jeremiah',
      initials: 'JER',
      best_score: 62960,
      total_games: 20,
      games_scored: 9,
      submits: 15,
      podiums: 9,
      crowns: 9,
      you: true,
    },
  ],
  games: [
    {
      slug: 'space-invaders',
      title: 'Space Invaders',
      scores: [
        { initials: 'JER', score: 62960, at: '2026-10-20T12:00:00Z', player_id: 'jeremiah' },
        { initials: 'MIC', score: 1200, at: '2026-10-20T12:05:00Z', player_id: 'michele' },
      ],
    },
  ],
};

describe('ArcadeGames', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedRateArcadeGame.mockResolvedValue({ success: true, rating: { count: 4, average: 4.5 } });
    mockedGetArcadeScoreboard.mockResolvedValue(scoreboard);
    mockedGetArcadeHighscores.mockResolvedValue(highscores);
    mockedSubmitArcadeScore.mockResolvedValue({ success: true, score: 1234, rank: 1 });
  });

  it('renders featured picks, ratings and play links', async () => {
    mockedGetArcadeGames.mockResolvedValue(shelf);
    renderShelf();

    await waitFor(() => expect(screen.getByTestId('arcade-shelf')).toBeTruthy());
    expect(screen.getByText(/Picks from the family admins/)).toBeTruthy();
    expect(screen.getAllByTestId('arcade-rating-space-invaders')[0].textContent).toContain('4.7 (3)');
    expect(screen.getByTestId('arcade-rating-breakout').textContent).toContain('No votes');
    expect(screen.getByText(/No plays yet/)).toBeTruthy();
    expect(screen.getByText('Desktop')).toBeTruthy();

    const links = screen.getAllByRole('link', { name: /play/i });
    expect(links.length).toBeGreaterThan(0);
    expect(links[0].getAttribute('href')).toBe('https://games.sumemail.com/play/space-invaders');
  });

  it('plays a game inside the family hub', async () => {
    mockedGetArcadeGames.mockResolvedValue(shelf);
    renderShelf();

    await waitFor(() => expect(screen.getByTestId('arcade-shelf')).toBeTruthy());
    fireEvent.click(screen.getAllByRole('link', { name: /play/i })[0]);

    const frame = await screen.findByTestId('arcade-player-frame');
    expect(frame.getAttribute('src')).toBe('https://games.sumemail.com/play/space-invaders?embed=1&player=JER');

    fireEvent.click(screen.getByLabelText('Close dialog'));
    await waitFor(() => expect(screen.queryByTestId('arcade-player-frame')).toBeNull());
  });

  it('shows the family scoreboard and saves an auto-captured score as the signed-in member', async () => {
    mockedGetArcadeGames.mockResolvedValue(shelf);
    renderShelf();

    await waitFor(() => expect(screen.getByTestId('arcade-scoreboard')).toBeTruthy());
    expect(screen.getByText('Family high scores')).toBeTruthy();
    expect(screen.getByText('62,960')).toBeTruthy();
    expect(screen.getByText('You')).toBeTruthy();

    fireEvent.click(screen.getAllByRole('link', { name: /play/i })[0]);
    await screen.findByTestId('arcade-player-frame');
    window.dispatchEvent(
      new MessageEvent('message', {
        origin: 'https://games.sumemail.com',
        data: {
          source: 'arcade-score',
          slug: 'space-invaders',
          score: 4321,
          capture_id: 'capture-1',
        },
      }),
    );

    await waitFor(() =>
      expect(mockedSubmitArcadeScore).toHaveBeenCalledWith('space-invaders', 4321, 'capture-1'),
    );
    await waitFor(() => expect(screen.getByText(/Saved to the family board/)).toBeTruthy());
  });

  it('shows the per-game high score table and lets the player sort the shelf', async () => {
    mockedGetArcadeGames.mockResolvedValue(shelf);
    renderShelf();

    await waitFor(() => expect(screen.getByTestId('arcade-highscores')).toBeTruthy());
    expect(screen.getByText('High scores by game')).toBeTruthy();
    expect(screen.getByTestId('arcade-highscore-space-invaders').textContent).toContain('62,960');

    fireEvent.click(screen.getByRole('button', { name: 'Most played' }));
    await waitFor(() => expect(mockedGetArcadeGames).toHaveBeenCalledWith('plays'));
  });

  it('does not accept a score that was not captured by the game', async () => {
    mockedGetArcadeGames.mockResolvedValue(shelf);
    renderShelf();
    await waitFor(() => expect(screen.getByTestId('arcade-shelf')).toBeTruthy());
    fireEvent.click(screen.getAllByRole('link', { name: /play/i })[0]);
    await screen.findByTestId('arcade-player-frame');

    window.dispatchEvent(
      new MessageEvent('message', {
        origin: 'https://games.sumemail.com',
        data: { source: 'arcade-score', slug: 'space-invaders', score: 4321 },
      }),
    );

    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(mockedSubmitArcadeScore).not.toHaveBeenCalled();
  });

  it('relabels the shelf when admins have not curated picks', async () => {
    mockedGetArcadeGames.mockResolvedValue({
      ...shelf,
      featured_source: 'rating',
      featured: [{ ...shelf.games[1], featured: true }],
    });
    renderShelf();

    await waitFor(() => expect(screen.getByTestId('arcade-shelf')).toBeTruthy());
    expect(screen.getByText(/Top rated by family votes/)).toBeTruthy();
  });

  it('shows an honest offline state when the arcade is unreachable', async () => {
    mockedGetArcadeGames.mockResolvedValue({
      success: false,
      arcade_available: false,
      error: 'Arcade unreachable: timeout',
      play_base: '',
      featured_source: 'none',
      featured: [],
      games: [],
      count: 0,
    });
    renderShelf();

    await waitFor(() => expect(screen.getByTestId('arcade-offline')).toBeTruthy());
    expect(screen.getByText(/Arcade unreachable: timeout/)).toBeTruthy();
  });

  it('invites a first publish when no games exist', async () => {
    mockedGetArcadeGames.mockResolvedValue({
      success: true,
      arcade_available: true,
      play_base: 'http://arcade.local:5001',
      featured_source: 'none',
      featured: [],
      games: [],
      count: 0,
    });
    renderShelf();

    await waitFor(() => expect(screen.getByTestId('arcade-empty')).toBeTruthy());
  });

  it('rates a game as the signed-in family member', async () => {
    mockedGetArcadeGames.mockResolvedValue(shelf);
    renderShelf();

    await waitFor(() => expect(screen.getByTestId('arcade-shelf')).toBeTruthy());
    const stars = screen.getAllByRole('button', { name: 'Rate Space Invaders 4 stars' });
    fireEvent.click(stars[0]);

    await waitFor(() => expect(mockedRateArcadeGame).toHaveBeenCalledWith('space-invaders', 4));
    await waitFor(() => expect(screen.getAllByText('Thanks!').length).toBeGreaterThan(0));
  });
});
