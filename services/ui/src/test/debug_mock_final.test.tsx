import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useQuery } from '@tanstack/react-query';

vi.mock('../services/api', () => ({
  api: {
    getBibleStats: vi.fn().mockResolvedValue({ read_streak_current: 5 }),
    getBibleAchievements: vi.fn(),
    getBibleActivityFeed: vi.fn(),
  },
}));

import { api } from '../services/api';

function TestComponent() {
  const { data } = useQuery({
    queryKey: ['bible-stats'],
    queryFn: () => api.getBibleStats(30),
  });
  return <div data-testid="streak">{data?.read_streak_current ?? 0}</div>;
}

describe('debug bible progress mock', () => {
  it('should use mock data', async () => {
    const { unmount } = render(
      <QueryClientProvider client={new QueryClient()}>
        <TestComponent />
      </QueryClientProvider>
    );
    
    await waitFor(() => {
      expect(screen.getByTestId('streak')).toHaveTextContent('5');
    });
    
    unmount();
  });
});