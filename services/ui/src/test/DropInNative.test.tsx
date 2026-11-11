import { describe, expect, it, vi } from 'vitest';
import { render, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../context/AuthContext';
import { CallProvider } from '../context/CallContext';

const native = vi.hoisted(() => ({
  onJoin: null as null | ((room: string, name: string) => void),
  onHangUp: null as null | (() => void),
  ended: vi.fn(),
}));

vi.mock('../lib/dropIn', () => ({
  listenForDropIn: (onJoin: (room: string, name: string) => void, onHangUp: () => void) => {
    native.onJoin = onJoin;
    native.onHangUp = onHangUp;
    return () => {};
  },
  reportDropInEnded: native.ended,
}));

describe('native drop-in (Android answered a ring in the background)', () => {
  it('listens for the native join and ends the native call when nothing could join', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <AuthProvider>
            <CallProvider>
              <div />
            </CallProvider>
          </AuthProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => expect(native.onJoin).toBeTypeOf('function'));
    expect(native.onHangUp).toBeTypeOf('function');

    // Not signed in here, so the call cannot start: the phone's native call
    // (and its microphone) must be ended rather than left up.
    native.onJoin?.('room-1', 'Mom');
    await waitFor(() => expect(native.ended).toHaveBeenCalled());
  });
});
