import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from './setup';
import { renderWithProviders } from './render';
import MusicRecommendationCard from '../components/health/MusicRecommendationCard';

describe('MusicRecommendationCard', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/recommendations/music', () => HttpResponse.json({
        status: 'SUCCESS',
        recommendation: {
          lane: 'hip hop',
          artist: 'Lecrae',
          style: 'hip hop, rap, conscious',
          reason: 'Same flow and weight, pointed at hope and purpose instead of emptiness.',
          matched_on: 'Drake',
          kind: 'music',
        },
      }))
    );
  });

  it('shows the suggested artist and the reason it was suggested', async () => {
    renderWithProviders(<MusicRecommendationCard />);
    await waitFor(() => expect(screen.getByTestId('music-recommendation-card')).toBeInTheDocument());
    expect(screen.getByText(/Lecrae/)).toBeInTheDocument();
    expect(screen.getByText(/Same flow and weight/)).toBeInTheDocument();
    expect(screen.getByText(/Drake/)).toBeInTheDocument();
  });

  it('stays quiet when there is no recommendation', async () => {
    server.use(
      http.get('/api/recommendations/music', () => HttpResponse.json({ status: 'SUCCESS', recommendation: null }))
    );
    const { container } = renderWithProviders(<MusicRecommendationCard />);
    await waitFor(() => expect(container.querySelector('[data-testid="music-recommendation-card"]')).not.toBeInTheDocument());
  });
});