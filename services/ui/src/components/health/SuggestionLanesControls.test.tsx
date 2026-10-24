import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';
import SuggestionLanesControls from './SuggestionLanesControls';
import { api } from '../../services/api';
import type { RecommendationLanes } from '../../types/api';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal() as Record<string, unknown>;
  return {
    ...actual,
    api: {
      ...(actual.api as Record<string, unknown>),
      getRecommendationLanes: vi.fn(),
      setRecommendationLanes: vi.fn(),
    },
  };
});

const lanes = (overrides: Partial<RecommendationLanes['lanes']> = {}, canManage = true): RecommendationLanes => ({
  username: 'kid',
  lanes: { music: true, video: true, podcast: true, book: true, game: true, activity: true, ...overrides },
  can_manage: canManage,
});

const wrap = (ui: ReactElement) =>
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{ui}</QueryClientProvider>);

describe('SuggestionLanesControls (a parent)', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows each lane and blocks one with a tap', async () => {
    vi.mocked(api.getRecommendationLanes).mockResolvedValue(lanes({ book: false }));
    vi.mocked(api.setRecommendationLanes).mockResolvedValue(lanes({ book: false, game: false }));
    wrap(<SuggestionLanesControls username="kid" name="Kid" />);

    const game = await screen.findByTestId('suggestion-lane-game');
    expect(game).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByTestId('suggestion-lane-book')).toHaveAttribute('aria-checked', 'false');

    fireEvent.click(game);
    await waitFor(() => expect(api.setRecommendationLanes).toHaveBeenCalledWith('kid', { game: false }));
    await waitFor(() => expect(screen.getByTestId('suggestion-lane-game')).toHaveAttribute('aria-checked', 'false'));
  });

  it('blesses a blocked lane again', async () => {
    vi.mocked(api.getRecommendationLanes).mockResolvedValue(lanes({ video: false }));
    vi.mocked(api.setRecommendationLanes).mockResolvedValue(lanes());
    wrap(<SuggestionLanesControls username="kid" name="Kid" />);

    fireEvent.click(await screen.findByTestId('suggestion-lane-video'));
    await waitFor(() => expect(api.setRecommendationLanes).toHaveBeenCalledWith('kid', { video: true }));
  });

  it('renders nothing for someone who may not manage the member', async () => {
    vi.mocked(api.getRecommendationLanes).mockResolvedValue(lanes({}, false));
    const { container } = wrap(<SuggestionLanesControls username="kid" name="Kid" />);
    await waitFor(() => expect(api.getRecommendationLanes).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing when the server refuses', async () => {
    vi.mocked(api.getRecommendationLanes).mockRejectedValue(new Error('403'));
    const { container } = wrap(<SuggestionLanesControls username="kid" name="Kid" />);
    await waitFor(() => expect(api.getRecommendationLanes).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
