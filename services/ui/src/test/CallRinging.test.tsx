import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { AuthProvider } from '../context/AuthContext';
import { CallProvider } from '../context/CallContext';
import { server } from './setup';
import { DEVICE_DROP_IN_KEY } from '../lib/callSettings';

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

const MOM = { id: 'p1', username: 'mom', name: 'Mom', muted: false, camera: false };

function serveCalls(ringing: boolean, opts: { dropIn?: boolean; deviceDropIn?: boolean } = {}) {
  server.use(
    http.get('/api/communication/calls', () =>
      HttpResponse.json({
        calls: [{
          token: 'room-1', name: 'Family', started_at: 1_700_000_000, participants: [MOM], ringing, caller: 'Mom',
          drop_in: opts.dropIn ?? false,
        }],
        ...(opts.deviceDropIn === undefined ? {} : { device_drop_in: opts.deviceDropIn }),
      }),
    ),
  );
}

describe('incoming call ringing', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.removeItem(DEVICE_DROP_IN_KEY);
  });

  it('rings a full-screen incoming call while it is ringing you', async () => {
    const vibrate = vi.fn();
    Object.defineProperty(navigator, 'vibrate', { value: vibrate, configurable: true });
    const setInterval = vi.spyOn(window, 'setInterval');
    serveCalls(true);

    renderCallProvider();

    const incoming = await screen.findByTestId('incoming-call');
    expect(incoming).toHaveTextContent('Mom');
    expect(incoming).toHaveTextContent('is calling Family');
    expect(screen.getByRole('button', { name: 'Answer' })).toBeInTheDocument();
    await waitFor(() => expect(vibrate).toHaveBeenCalled());
    expect(setInterval).toHaveBeenCalledWith(expect.any(Function), 2500);
    expect(screen.queryByTestId('call-banner')).not.toBeInTheDocument();
  });

  it('declining stops the ringing and tells the caller', async () => {
    const user = userEvent.setup();
    Object.defineProperty(navigator, 'vibrate', { value: vi.fn(), configurable: true });
    const clearInterval = vi.spyOn(window, 'clearInterval');
    const declined: string[] = [];
    serveCalls(true);
    server.use(
      http.post('/api/communication/calls/:room/decline', ({ params }) => {
        declined.push(String(params.room));
        return HttpResponse.json({ declined: true });
      }),
    );

    renderCallProvider();
    await user.click(await screen.findByRole('button', { name: 'Decline' }));

    await waitFor(() => expect(screen.queryByTestId('incoming-call')).not.toBeInTheDocument());
    expect(clearInterval).toHaveBeenCalled();
    await waitFor(() => expect(declined).toEqual(['room-1']));
  });

  it('answers by itself when you let the caller drop in and this device has drop-in on', async () => {
    Object.defineProperty(navigator, 'vibrate', { value: vi.fn(), configurable: true });
    const getUserMedia = vi
      .spyOn(navigator.mediaDevices, 'getUserMedia')
      .mockRejectedValue(new Error('no microphone in tests'));
    serveCalls(true, { dropIn: true, deviceDropIn: true });

    renderCallProvider();

    await waitFor(() => expect(getUserMedia).toHaveBeenCalled());
    expect(screen.queryByTestId('incoming-call')).not.toBeInTheDocument();
  });

  it('rings when the caller is not on your drop-in list', async () => {
    Object.defineProperty(navigator, 'vibrate', { value: vi.fn(), configurable: true });
    const getUserMedia = vi.spyOn(navigator.mediaDevices, 'getUserMedia');
    serveCalls(true, { dropIn: false, deviceDropIn: true });

    renderCallProvider();

    expect(await screen.findByTestId('incoming-call')).toHaveTextContent('Mom');
    expect(getUserMedia).not.toHaveBeenCalled();
  });

  it('rings when this device has drop-in switched off, and remembers that', async () => {
    Object.defineProperty(navigator, 'vibrate', { value: vi.fn(), configurable: true });
    const getUserMedia = vi.spyOn(navigator.mediaDevices, 'getUserMedia');
    serveCalls(true, { dropIn: true, deviceDropIn: false });

    renderCallProvider();

    expect(await screen.findByTestId('incoming-call')).toHaveTextContent('Mom');
    expect(getUserMedia).not.toHaveBeenCalled();
    await waitFor(() => expect(localStorage.getItem(DEVICE_DROP_IN_KEY)).toBe('0'));
  });

  it('a call in progress that is not ringing you shows a quiet Join banner', async () => {
    const user = userEvent.setup();
    Object.defineProperty(navigator, 'vibrate', { value: vi.fn(), configurable: true });
    const setInterval = vi.spyOn(window, 'setInterval');
    serveCalls(false);

    renderCallProvider();

    const banner = await screen.findByTestId('call-banner');
    expect(banner).toHaveTextContent('Mom on a call');
    expect(screen.queryByTestId('incoming-call')).not.toBeInTheDocument();
    expect(setInterval).not.toHaveBeenCalledWith(expect.any(Function), 2500);

    await user.click(screen.getByRole('button', { name: 'Dismiss call alert' }));
    await waitFor(() => expect(screen.queryByTestId('call-banner')).not.toBeInTheDocument());
  });
});
