import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from './setup';
import { renderWithProviders } from './render';
import PodcastRecommendationCard from '../components/health/PodcastRecommendationCard';

describe('PodcastRecommendationCard', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/recommendations/podcast', () => HttpResponse.json({
        status: 'SUCCESS',
        recommendation: {
          theme: 'culture conversation',
          show: 'That Sounds Fun',
          host: 'Annie F. Downs',
          style: 'conversations, faith, encouragement',
          reason: 'Warm and joyful conversations with authors, musicians, and inspiring guests.',
          matched_on: 'Joe Rogan',
          kind: 'podcast',
          redeemed: true,
        },
      }))
    );
  });

  it('shows the suggested podcast and the reason it was suggested', async () => {
    renderWithProviders(<PodcastRecommendationCard />);
    await waitFor(() => expect(screen.getByTestId('podcast-recommendation-card')).toBeInTheDocument());
    expect(screen.getByText(/That Sounds Fun/)).toBeInTheDocument();
    expect(screen.getByText(/Annie F. Downs/)).toBeInTheDocument();
    expect(screen.getByText(/Warm and joyful conversations/)).toBeInTheDocument();
    expect(screen.getByText(/Joe Rogan/)).toBeInTheDocument();
  });

  it('stays quiet when there is no recommendation', async () => {
    server.use(
      http.get('/api/recommendations/podcast', () => HttpResponse.json({ status: 'SUCCESS', recommendation: null }))
    );
    const { container } = renderWithProviders(<PodcastRecommendationCard />);
    await waitFor(() => expect(container.querySelector('[data-testid="podcast-recommendation-card"]')).not.toBeInTheDocument());
  });
});
