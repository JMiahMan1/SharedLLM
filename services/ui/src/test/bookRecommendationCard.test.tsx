import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from './setup';
import { renderWithProviders } from './render';
import BookRecommendationCard from '../components/health/BookRecommendationCard';

describe('BookRecommendationCard', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/recommendations/book', () => HttpResponse.json({
        status: 'SUCCESS',
        recommendation: {
          theme: 'epic fantasy',
          title: 'The Wingfeather Saga',
          author: 'Andrew Peterson',
          style: 'epic fantasy, family, heroic courage',
          reason: 'An extraordinary tale of family sacrifice, redemption, and courage against darkness.',
          matched_on: 'J.R.R. Tolkien',
          kind: 'book',
          redeemed: true,
        },
      }))
    );
  });

  it('shows the suggested book and the reason it was suggested', async () => {
    renderWithProviders(<BookRecommendationCard />);
    await waitFor(() => expect(screen.getByTestId('book-recommendation-card')).toBeInTheDocument());
    expect(screen.getByText(/The Wingfeather Saga/)).toBeInTheDocument();
    expect(screen.getByText(/Andrew Peterson/)).toBeInTheDocument();
    expect(screen.getByText(/An extraordinary tale/)).toBeInTheDocument();
    expect(screen.getByText(/J.R.R. Tolkien/)).toBeInTheDocument();
  });

  it('stays quiet when there is no recommendation', async () => {
    server.use(
      http.get('/api/recommendations/book', () => HttpResponse.json({ status: 'SUCCESS', recommendation: null }))
    );
    const { container } = renderWithProviders(<BookRecommendationCard />);
    await waitFor(() => expect(container.querySelector('[data-testid="book-recommendation-card"]')).not.toBeInTheDocument());
  });
});
