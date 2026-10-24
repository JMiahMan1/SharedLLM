import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import RecommendationsWidget from './RecommendationsWidget';
import { api } from '../../services/api';
import type { UserWidgetSettings } from '../../types/widget';

vi.mock('../../hooks/useHaptics', () => ({
  useHaptics: () => ({ trigger: vi.fn(), isEnabled: () => true, setEnabled: vi.fn() }),
}));

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal() as Record<string, unknown>;
  return {
    ...actual,
    api: {
      ...(actual.api as Record<string, unknown>),
      getMusicRecommendation: vi.fn(),
      getVideoRecommendation: vi.fn(),
      getPodcastRecommendation: vi.fn(),
      getBookRecommendation: vi.fn(),
      getGameRecommendation: vi.fn(),
      getActivityRecommendation: vi.fn(),
    },
  };
});

const settings: UserWidgetSettings = {
  widget_key: 'recommendations',
  visibility: 'visible',
  order_index: 0,
  size: 'medium',
  is_pinned: false,
  sort_mode: null,
  pinned_devices: [],
  config: {},
  updated_at: 0,
};

const renderWidget = () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <RecommendationsWidget settingsButton={<span />} userSettings={settings} onTogglePin={() => {}} />
    </QueryClientProvider>,
  );
};

const none = { status: 'SUCCESS', recommendation: null };

describe('RecommendationsWidget', () => {
  beforeEach(() => {
    vi.mocked(api.getMusicRecommendation).mockResolvedValue(none);
    vi.mocked(api.getVideoRecommendation).mockResolvedValue(none);
    vi.mocked(api.getPodcastRecommendation).mockResolvedValue(none);
    vi.mocked(api.getBookRecommendation).mockResolvedValue(none);
    vi.mocked(api.getGameRecommendation).mockResolvedValue(none);
    vi.mocked(api.getActivityRecommendation).mockResolvedValue(none);
  });

  it('says plainly when there is nothing to suggest yet', async () => {
    renderWidget();
    expect(await screen.findByTestId('recommendations-empty')).toHaveTextContent('No suggestions yet');
  });

  it('shows only the suggestions the server returned', async () => {
    vi.mocked(api.getMusicRecommendation).mockResolvedValue({
      status: 'SUCCESS',
      recommendation: { lane: 'hip hop', artist: 'Lecrae', style: 'hip hop with a message', reason: 'Same energy.', matched_on: 'Rap Caviar', kind: 'music' },
    });
    vi.mocked(api.getBookRecommendation).mockResolvedValue({
      status: 'SUCCESS',
      recommendation: { theme: 'grimdark horror', title: null, author: null, style: '', reason: 'A lighter read.', matched_on: 'horror', kind: 'book', redeemed: false, redirect_to: 'the Psalms' },
    });
    renderWidget();

    expect(await screen.findByTestId('recommendation-music')).toHaveTextContent('Lecrae');
    expect(screen.getByTestId('recommendation-music')).toHaveTextContent("Because you've been listening to Rap Caviar");
    expect(screen.getByTestId('recommendation-book')).toHaveTextContent('the Psalms');
    expect(screen.queryByTestId('recommendation-video')).toBeNull();
    expect(screen.queryByTestId('recommendation-podcast')).toBeNull();
  });

  it('keeps the rest when one kind fails, and says so', async () => {
    vi.mocked(api.getMusicRecommendation).mockResolvedValue({
      status: 'SUCCESS',
      recommendation: { lane: 'pop', artist: 'for KING & COUNTRY', style: 'anthemic pop', reason: 'Big choruses.', matched_on: 'pop', kind: 'music' },
    });
    vi.mocked(api.getVideoRecommendation).mockRejectedValue(new Error('down'));
    renderWidget();

    expect(await screen.findByTestId('recommendation-music', {}, { timeout: 4000 })).toBeInTheDocument();
    expect(await screen.findByTestId('recommendations-partial-error', {}, { timeout: 4000 })).toBeInTheDocument();
  });

  it('offers a retry when nothing could load', async () => {
    for (const fn of [api.getMusicRecommendation, api.getVideoRecommendation, api.getPodcastRecommendation, api.getBookRecommendation, api.getGameRecommendation, api.getActivityRecommendation]) {
      vi.mocked(fn).mockRejectedValue(new Error('down'));
    }
    renderWidget();
    expect(await screen.findByTestId('recommendations-error', {}, { timeout: 4000 })).toHaveTextContent('Try again');
  });

  it('shows a game swap and an activity alongside the rest', async () => {
    vi.mocked(api.getGameRecommendation).mockResolvedValue({
      status: 'SUCCESS',
      recommendation: { theme: 'shooter', game: 'Rocket League', style: 'fast team matches', reason: 'Same team play.', matched_on: 'Fortnite', kind: 'game', redeemed: true },
    });
    vi.mocked(api.getActivityRecommendation).mockResolvedValue({
      status: 'SUCCESS',
      recommendation: { theme: 'late nights', activity: 'A phone-free wind-down', style: 'a Psalm before sleep', reason: 'Rest is a gift.', matched_on: 'late nights on the phone this week', kind: 'activity' },
    });
    renderWidget();

    expect(await screen.findByTestId('recommendation-game')).toHaveTextContent("Rocket League");
    expect(screen.getByTestId('recommendation-game')).toHaveTextContent("Because you've been playing Fortnite");
    expect(screen.getByTestId('recommendation-activity')).toHaveTextContent('A phone-free wind-down');
  });
});
