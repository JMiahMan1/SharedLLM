import { describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AuthProvider, useAuth } from '../context/AuthContext';
import { api } from '../services/api';

vi.mock('../lib/storage', () => ({
  storageInit: vi.fn().mockResolvedValue(undefined),
  storageGet: vi.fn().mockResolvedValue('test-key'),
  storageSet: vi.fn().mockResolvedValue(undefined),
  storageRemove: vi.fn().mockResolvedValue(undefined),
  mirrorNativeCredentials: vi.fn().mockResolvedValue(undefined),
}));
vi.mock('../lib/deviceRegistration', () => ({ registerThisDevice: vi.fn().mockResolvedValue({ registered: false }) }));
vi.mock('../lib/locationTracking', () => ({ mirrorIdentity: vi.fn().mockResolvedValue(undefined) }));

function Avatar() {
  const { user } = useAuth();
  return <span data-testid="avatar">{user?.avatar ?? 'none'}</span>;
}

describe('AuthContext', () => {
  it('follows a profile the avatar picker writes to the ["me"] cache', async () => {
    vi.spyOn(api, 'getMe').mockResolvedValue({ username: 'dad', avatar: 'preset:noah' } as never);
    const client = new QueryClient();
    render(
      <QueryClientProvider client={client}>
        <AuthProvider><Avatar /></AuthProvider>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByTestId('avatar')).toHaveTextContent('preset:noah'));

    act(() => { client.setQueryData(['me'], { username: 'dad', avatar: 'upload:123' }); });
    await waitFor(() => expect(screen.getByTestId('avatar')).toHaveTextContent('upload:123'));

    // Someone else's profile is never taken as the signed-in person's.
    act(() => { client.setQueryData(['me'], { username: 'mom', avatar: 'preset:ruth' }); });
    expect(screen.getByTestId('avatar')).toHaveTextContent('upload:123');
  });
});
