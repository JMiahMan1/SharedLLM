import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { render } from '@testing-library/react';
import type { ReactElement } from 'react';
import { AuthProvider } from '../context/AuthContext';
import { LocationProvider } from '../context/LocationContext';

interface RenderOptions {
  queryClient?: QueryClient;
  /** The URL the page opens at, e.g. "/family?tab=create". */
  route?: string;
}

export const renderWithProviders = (ui: ReactElement, options: RenderOptions = {}) => {
  const { queryClient: providedQueryClient, route } = options;
  const queryClient = providedQueryClient || new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });

  const result = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={route ? [route] : undefined}>
        <AuthProvider>
          <LocationProvider>{ui}</LocationProvider>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );

  return {
    ...result,
    queryClient,
  };
};
