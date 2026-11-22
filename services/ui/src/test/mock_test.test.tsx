import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useQuery } from '@tanstack/react-query';

vi.mock('../services/api', () => ({
  api: {
    getBibleStats: vi.fn().mockResolvedValue({ read_streak_current: 5 }),
  },
}));

import { api } from '../services/api';

function TestComponent() {
  const { data } = useQuery({
    queryKey: ['bible-stats'],
    queryFn: () => api.getBibleStats(30),
  });
  return <div>{data?.read_streak_current ?? 0}</div>;
}

describe('mock test', () => {
  it('should use mock data', async () => {
    const { unmount } = render(
      <QueryClientProvider client={new QueryClient()}>
        <TestComponent />
      </QueryClientProvider>
    );
    
    // Wait for the query to resolve
    await waitFor(() => {
      expect(screen.getByText('5')).toBeInTheDocument();
    });
    
    unmount();
  });
});