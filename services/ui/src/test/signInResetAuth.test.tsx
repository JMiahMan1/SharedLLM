/**
 * AuthContext and the login page after the server refuses the stored key.
 *
 * At start (initAuth) and on a later profile read (refreshProfile), a 401
 * clears the key and the cached user through storageRemove (which also clears
 * the native TokenBridge copy), records why, and the login page says so. A
 * 503 is retried and never signs anyone out. The notice clears once the
 * person signs in again.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { AxiosError, AxiosHeaders } from 'axios';
import { AuthProvider, useAuth } from '../context/AuthContext';
import Login from '../pages/Login';
import { api } from '../services/api';
import * as storage from '../lib/storage';
import {
  SESSION_ENDED_MESSAGE,
  SIGN_IN_RESET_MESSAGE,
  readSignOutReason,
  recordSignOutReason,
} from '../lib/signOutNotice';

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
const storageRemove = vi.mocked(storage.storageRemove);

function httpError(status: number, data: unknown): AxiosError {
  const headers = new AxiosHeaders({ Authorization: 'Bearer fake-key-123' });
  const config = { headers } as never;
  return new AxiosError('failed', 'ERR_BAD_REQUEST', config, undefined, {
    status, statusText: '', headers: {}, config, data,
  } as never);
}

function Probe() {
  const { user, initError, refreshProfile } = useAuth();
  return (
    <div>
      <span data-testid="who">{user?.username ?? 'nobody'}</span>
      <span data-testid="init-error">{initError ?? ''}</span>
      <button type="button" onClick={() => void refreshProfile()}>refresh</button>
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

describe('AuthContext after a refused key', () => {
  beforeEach(() => {
    storageGet.mockImplementation(async (key: string) =>
      key === 'jarvis_api_key' ? 'fake-key-123' : key === 'jarvis_user' ? JSON.stringify({ username: 'parent', role: 'user' }) : null);
    storageRemove.mockClear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it('a reset sign-in at start clears the key, records the reason and says so', async () => {
    vi.spyOn(api, 'getMe').mockRejectedValue(httpError(401, { detail: 'Your sign-in was reset, please sign in again.', code: 'sign_in_reset' }));
    renderAuth(<Probe />);
    await waitFor(() => expect(screen.getByTestId('init-error')).toHaveTextContent(SIGN_IN_RESET_MESSAGE));
    expect(screen.getByTestId('who')).toHaveTextContent('nobody');
    expect(storageRemove).toHaveBeenCalledWith('jarvis_api_key');
    expect(storageRemove).toHaveBeenCalledWith('jarvis_user');
    expect(readSignOutReason()).toBe('sign_in_reset');
  });

  it('a refused key at start reads as a session that ended', async () => {
    vi.spyOn(api, 'getMe').mockRejectedValue(httpError(401, { detail: 'Invalid API Key', code: 'api_key_invalid' }));
    renderAuth(<Probe />);
    await waitFor(() => expect(screen.getByTestId('init-error')).toHaveTextContent(SESSION_ENDED_MESSAGE));
    expect(storageRemove).toHaveBeenCalledWith('jarvis_api_key');
    expect(readSignOutReason()).toBe('session_ended');
  });

  it('a 401 without a refusal code at start is retried, never a sign-out', async () => {
    const getMe = vi.spyOn(api, 'getMe')
      .mockRejectedValueOnce(httpError(401, { detail: 'Upstream refused the login' }))
      .mockResolvedValue({ username: 'parent', role: 'user', is_admin: false } as never);
    renderAuth(<Probe />);
    await waitFor(() => expect(getMe.mock.calls.length).toBeGreaterThanOrEqual(2), { timeout: 4000 });
    await waitFor(() => expect(screen.getByTestId('who')).toHaveTextContent('parent'));
    expect(storageRemove).not.toHaveBeenCalledWith('jarvis_api_key');
    expect(readSignOutReason()).toBeNull();
  });

  it('a 503 at start is retried, never a sign-out', async () => {
    const getMe = vi.spyOn(api, 'getMe')
      .mockRejectedValueOnce(httpError(503, { detail: 'Try again' }))
      .mockResolvedValue({ username: 'parent', role: 'user', is_admin: false } as never);
    renderAuth(<Probe />);
    await waitFor(() => expect(getMe.mock.calls.length).toBeGreaterThanOrEqual(2), { timeout: 4000 });
    await waitFor(() => expect(screen.getByTestId('who')).toHaveTextContent('parent'));
    expect(storageRemove).not.toHaveBeenCalledWith('jarvis_api_key');
    expect(readSignOutReason()).toBeNull();
  });

  it('a reset sign-in on a later profile read signs out with the reason', async () => {
    const getMe = vi.spyOn(api, 'getMe').mockResolvedValue({ username: 'parent', role: 'user', is_admin: false } as never);
    renderAuth(<Probe />);
    await waitFor(() => expect(screen.getByTestId('who')).toHaveTextContent('parent'));
    await waitFor(() => expect(getMe).toHaveBeenCalledTimes(2));
    getMe.mockRejectedValue(httpError(401, { detail: 'Your sign-in was reset, please sign in again.', code: 'sign_in_reset' }));
    await userEvent.setup().click(screen.getByRole('button', { name: 'refresh' }));
    await waitFor(() => expect(storageRemove).toHaveBeenCalledWith('jarvis_api_key'));
    expect(storageRemove).toHaveBeenCalledWith('jarvis_user');
    expect(readSignOutReason()).toBe('sign_in_reset');
  });
});

describe('login page notice', () => {
  beforeEach(() => {
    storageGet.mockResolvedValue(null);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it('shows the reset message after a reset sign-in', async () => {
    recordSignOutReason('sign_in_reset');
    renderAuth(<Login />);
    expect(await screen.findByText(SIGN_IN_RESET_MESSAGE)).toBeInTheDocument();
  });

  it('shows the session message after any other refused key', async () => {
    recordSignOutReason('session_ended');
    renderAuth(<Login />);
    expect(await screen.findByText(SESSION_ENDED_MESSAGE)).toBeInTheDocument();
    expect(screen.queryByText(SIGN_IN_RESET_MESSAGE)).not.toBeInTheDocument();
  });

  it('shows nothing when nobody was signed out', async () => {
    renderAuth(<Login />);
    await screen.findByRole('button', { name: 'Sign In' });
    expect(screen.queryByTestId('login-notice')).not.toBeInTheDocument();
  });

  it('clears the notice after a successful sign-in', async () => {
    recordSignOutReason('sign_in_reset');
    vi.spyOn(api, 'login').mockResolvedValue({ api_key: 'fake-key-456', username: 'parent', is_admin: false });
    vi.spyOn(api, 'getMe').mockResolvedValue({ username: 'parent', role: 'user', is_admin: false } as never);
    renderAuth(<Login />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText('Username'), 'parent');
    await user.type(screen.getByLabelText('Password'), 'fake-password');
    await user.click(screen.getByRole('button', { name: 'Sign In' }));
    await waitFor(() => expect(readSignOutReason()).toBeNull());
  });
});
