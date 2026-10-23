import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { AuthProvider } from '../context/AuthContext';
import { CallProvider } from '../context/CallContext';
import { server } from './setup';

function renderCallProvider() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <AuthProvider>
          <CallProvider>
            <div data-testid="call-child" />
          </CallProvider>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('incoming call ringing', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('rings on a schedule while a call waits in the banner', async () => {
    const vibrate = vi.fn();
    Object.defineProperty(navigator, 'vibrate', { value: vibrate, configurable: true });
    const setInterval = vi.spyOn(window, 'setInterval');
    server.use(
      http.get('/api/communication/calls', () =>
        HttpResponse.json({
          calls: [
            {
              token: 'room-1',
              name: 'Family',
              started_at: 1_700_000_000,
              participants: [{ id: 'p1', username: 'mom', name: 'Mom', muted: false, camera: false }],
            },
          ],
        }),
      ),
    );

    renderCallProvider();

    await waitFor(() => expect(vibrate).toHaveBeenCalled());
    expect(setInterval).toHaveBeenCalledWith(expect.any(Function), 2500);
  });

  it('stops alerting after the banner is dismissed', async () => {
    const user = userEvent.setup();
    const vibrate = vi.fn();
    Object.defineProperty(navigator, 'vibrate', { value: vibrate, configurable: true });
    const clearInterval = vi.spyOn(window, 'clearInterval');
    server.use(
      http.get('/api/communication/calls', () =>
        HttpResponse.json({
          calls: [
            {
              token: 'room-1',
              name: 'Family',
              started_at: 1_700_000_000,
              participants: [{ id: 'p1', username: 'mom', name: 'Mom', muted: false, camera: false }],
            },
          ],
        }),
      ),
    );

    renderCallProvider();
    await waitFor(() => expect(vibrate).toHaveBeenCalled());

    await user.click(await screen.findByRole('button', { name: 'Dismiss drop-in alert' }));

    await waitFor(() => expect(screen.queryByTestId('call-banner')).not.toBeInTheDocument());
    expect(clearInterval).toHaveBeenCalled();
  });
});
