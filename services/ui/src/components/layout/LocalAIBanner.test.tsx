import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '../../test/setup';
import LocalAIBanner from './LocalAIBanner';

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><LocalAIBanner /></QueryClientProvider>);
}

describe('LocalAIBanner', () => {
  it('shows a status line while the local AI is offline', async () => {
    server.use(http.get('/api/status/local-ai', () => HttpResponse.json({
      local_ai: { state: 'down', offline: true, since: 1, message: 'x', still_working: [] }, backends: {}, any_offline: true,
    })));
    mount();
    const banner = await screen.findByTestId('local-ai-banner');
    expect(banner).toHaveAttribute('role', 'status');
    expect(banner).toHaveTextContent("Jarvis's thinking brain is offline right now");
  });

  it('says nothing while it is up', async () => {
    mount();
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByTestId('local-ai-banner')).toBeNull();
  });
});
