/**
 * A slow phone must not be told it failed.
 *
 * The start-up read of the phone's own storage used to be given three seconds.
 * A cold start on the emulator (and, by the reports, on a real tablet) needed
 * more than ten, so every launch showed a red "Initialization timed out.
 * Please restart the app." banner over a login form while the stored session
 * restored underneath it. The banner is now only raised when the read really
 * has not finished.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider, STORAGE_TIMEOUT_MS, useAuth } from '../context/AuthContext';
import { api } from '../services/api';
import * as storage from '../lib/storage';

vi.mock('../lib/storage', () => ({
  storageInit: vi.fn().mockResolvedValue(undefined),
  storageGet: vi.fn(),
  storageSet: vi.fn().mockResolvedValue(undefined),
  storageRemove: vi.fn().mockResolvedValue(undefined),
  mirrorNativeCredentials: vi.fn().mockResolvedValue(undefined),
}));
vi.mock('../lib/deviceRegistration', () => ({ registerThisDevice: vi.fn().mockResolvedValue({ registered: false }) }));
vi.mock('../lib/locationTracking', () => ({ mirrorIdentity: vi.fn().mockResolvedValue(undefined) }));
vi.mock('../lib/push', () => ({ sharePushKey: vi.fn().mockResolvedValue(true) }));

const storageGet = vi.mocked(storage.storageGet);

function Probe() {
  const { user, initError } = useAuth();
  return (
    <div>
      <span data-testid="who">{user?.username ?? 'nobody'}</span>
      <span data-testid="init-error">{initError ?? ''}</span>
    </div>
  );
}

function renderAuth(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <AuthProvider>{ui}</AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('AuthContext start-up timeout', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.spyOn(api, 'getMe').mockResolvedValue({ username: 'slowpoke' } as never);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it('the timeout allows a slow phone to finish', () => {
    // Five seconds was already past the old three-second limit.
    expect(STORAGE_TIMEOUT_MS).toBeGreaterThanOrEqual(10000);
  });

  it('a storage read that takes seconds raises no banner and still signs in', async () => {
    // Each read takes four seconds: past the old three-second limit, twice.
    storageGet.mockImplementation(async (key: string) => {
      await new Promise((resolve) => setTimeout(resolve, 4000));
      return key === 'jarvis_api_key' ? 'slow-key-123' : JSON.stringify({ username: 'slowpoke' });
    });

    renderAuth(<Probe />);

    // Past the old three-second limit, before the key has arrived.
    await act(async () => { await vi.advanceTimersByTimeAsync(3500); });
    expect(screen.getByTestId('init-error')).toHaveTextContent('');

    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    expect(screen.getByTestId('init-error')).toHaveTextContent('');
    expect(screen.getByTestId('who')).toHaveTextContent('slowpoke');
  });

  it('a read that never finishes still ends the spinner, without claiming the key is bad', async () => {
    storageGet.mockImplementation(() => new Promise<string | null>(() => {}));

    renderAuth(<Probe />);
    await act(async () => { await vi.advanceTimersByTimeAsync(STORAGE_TIMEOUT_MS + 1000); });

    expect(screen.getByTestId('init-error')).toHaveTextContent(/still starting up/i);
    expect(screen.getByTestId('init-error')).not.toHaveTextContent(/timed out/i);
  });
});
