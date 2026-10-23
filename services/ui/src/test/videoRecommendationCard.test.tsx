import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from './setup';
import { renderWithProviders } from './render';
import VideoRecommendationCard from '../components/health/VideoRecommendationCard';

describe('VideoRecommendationCard', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/recommendations/video', () => HttpResponse.json({
        status: 'SUCCESS',
        recommendation: {
          theme: 'gaming',
          creator: 'DanTDM',
          style: 'gaming, family-friendly, adventure',
          reason: 'Same building, exploring and fun, kept light and friendly for everyone.',
          matched_on: 'PewDiePie',
          kind: 'video',
          redeemed: true,
        },
      }))
    );
  });

  it('shows the suggested creator and the reason it was suggested', async () => {
    renderWithProviders(<VideoRecommendationCard />);
    await waitFor(() => expect(screen.getByTestId('video-recommendation-card')).toBeInTheDocument());
    expect(screen.getByText(/DanTDM/)).toBeInTheDocument();
    expect(screen.getByText(/Same building/)).toBeInTheDocument();
    expect(screen.getByText(/PewDiePie/)).toBeInTheDocument();
  });

  it('stays quiet when there is no recommendation', async () => {
    server.use(
      http.get('/api/recommendations/video', () => HttpResponse.json({ status: 'SUCCESS', recommendation: null }))
    );
    const { container } = renderWithProviders(<VideoRecommendationCard />);
    await waitFor(() => expect(container.querySelector('[data-testid="video-recommendation-card"]')).not.toBeInTheDocument());
  });
});