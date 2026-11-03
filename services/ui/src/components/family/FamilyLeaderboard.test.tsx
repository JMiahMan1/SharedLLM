import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import FamilyLeaderboard from './FamilyLeaderboard';
import { api } from '../../services/api';
import type { ArcadeScoreboardPlayer, FamilyGameCard } from '../../types/api';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return {
    ...actual,
    api: { ...(actual.api as Record<string, unknown>), getArcadeScoreboard: vi.fn(), getArcadeHighscores: vi.fn() },
  };
});

function player(username: string, extra: Partial<ArcadeScoreboardPlayer> = {}): ArcadeScoreboardPlayer {
  const name = username[0].toUpperCase() + username.slice(1);
  return {
    username,
    display_name: name,
    initials: username.slice(0, 3).toUpperCase(),
    player_url: '',
    you: false,
    best_score: 100,
    total_games: 2,
    games_scored: 1,
    submits: 1,
    podiums: 0,
    crowns: 0,
    ...extra,
  };
}

const game = (slug: string, title: string): FamilyGameCard => ({
  slug,
  title,
  collection: 'family',
  family_state: 'published',
  source: 'built-in',
  emoji: '🌸',
  on_shelf: true,
  shelf_reason: '',
});

function renderIt(onPlay = vi.fn()) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <FamilyLeaderboard games={[game('family-petal-swap', 'Petal Swap')]} onPlay={onPlay} />
    </QueryClientProvider>,
  );
  return onPlay;
}

beforeEach(() => vi.clearAllMocks());

describe('FamilyLeaderboard', () => {
  it('ranks by games led, not by raw scores from different games', async () => {
    vi.mocked(api.getArcadeScoreboard).mockResolvedValue({
      success: true,
      arcade_available: true,
      players: [
        player('kate', { best_score: 99999, crowns: 0, podiums: 1 }),
        player('michele', { crowns: 2, podiums: 2 }),
        player('jeremiah', { crowns: 1, podiums: 2, you: true }),
        player('grandma', { games_scored: 0, submits: 0, best_score: 0 }),
      ],
    });
    vi.mocked(api.getArcadeHighscores).mockResolvedValue({ success: true, arcade_available: true, players: [], games: [] });
    renderIt();

    expect(await screen.findByTestId('family-podium-1')).toHaveTextContent('Michele');
    expect(screen.getByTestId('family-podium-2')).toHaveTextContent('Jeremiah');
    expect(screen.getByTestId('family-podium-2')).toHaveTextContent('You');
    expect(screen.getByTestId('family-podium-3')).toHaveTextContent('Kate');
    expect(screen.getByTestId('family-rank-grandma')).toHaveTextContent("Hasn't played yet");
  });

  it('shows each game champion by their Jarvis account and offers to beat it', async () => {
    vi.mocked(api.getArcadeScoreboard).mockResolvedValue({
      success: true,
      arcade_available: true,
      players: [player('michele', { crowns: 1 }), player('jeremiah', { you: true })],
    });
    vi.mocked(api.getArcadeHighscores).mockResolvedValue({
      success: true,
      arcade_available: true,
      players: [],
      games: [{ slug: 'family-petal-swap', title: 'Petal Swap', scores: [{ initials: 'MIC', player_id: 'michele', score: 4200 }] }],
    });
    const onPlay = renderIt();

    const card = await screen.findByTestId('family-champion-family-petal-swap');
    expect(card).toHaveTextContent('Michele');
    expect(card).toHaveTextContent('4,200');
    fireEvent.click(within(card).getByRole('button', { name: /Beat it/ }));
    expect(onPlay).toHaveBeenCalledWith(expect.objectContaining({ slug: 'family-petal-swap' }));
  });

  it('invites the family in before anyone has scored', async () => {
    vi.mocked(api.getArcadeScoreboard).mockResolvedValue({
      success: true,
      arcade_available: true,
      players: [player('kate', { games_scored: 0, submits: 0, best_score: 0 })],
    });
    vi.mocked(api.getArcadeHighscores).mockResolvedValue({ success: true, arcade_available: true, players: [], games: [] });
    renderIt();
    expect(await screen.findByTestId('family-board-empty')).toHaveTextContent('No champions yet');
  });
});
