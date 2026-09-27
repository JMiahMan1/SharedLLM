import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import ArcadeGames from './ArcadeGames';
import { api } from '../../services/api';
import type { ArcadeGamesResponse } from '../../types/api';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return {
    ...actual,
    api: {
      ...(actual.api as Record<string, unknown>),
      getArcadeGames: vi.fn(),
    },
  };
});

const mockedGetArcadeGames = vi.mocked(api.getArcadeGames);

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
  play_base: 'http://arcade.local:5001',
  featured_source: 'admin',
  featured: [
    {
      slug: 'space-invaders',
      title: 'Space Invaders',
      category: 'arcade',
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
      plays: 12,
      top_score: 900,
      rating: { count: 3, average: 4.67 },
      featured: true,
    },
    { slug: 'breakout', title: 'Breakout', category: 'classic', plays: 0, rating: { count: 0, average: 0 } },
  ],
  count: 2,
};

describe('ArcadeGames', () => {
  beforeEach(() => vi.clearAllMocks());

  it('renders featured picks, ratings and play links', async () => {
    mockedGetArcadeGames.mockResolvedValue(shelf);
    renderShelf();

    await waitFor(() => expect(screen.getByTestId('arcade-shelf')).toBeTruthy());
    expect(screen.getByText(/Picks from the family admins/)).toBeTruthy();
    expect(screen.getAllByTestId('arcade-rating-space-invaders')[0].textContent).toContain('4.7 (3)');
    expect(screen.getByTestId('arcade-rating-breakout').textContent).toContain('No votes');
    expect(screen.getByText(/No plays yet/)).toBeTruthy();

    const links = screen.getAllByRole('link', { name: /play/i });
    expect(links.length).toBeGreaterThan(0);
    expect(links[0].getAttribute('href')).toBe('http://arcade.local:5001/play/space-invaders');
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
});
