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
  it('follows a profile the avatar picker writes to the ["me"] cache, even with a profile read still in flight', async () => {
    // The first read (sign-in) answers at once; the second (a token change
    // reads the profile again) is held open, so the picker's write lands while
    // it is in flight: the race this guards.
    let answerLate: (profile: unknown) => void = () => undefined;
    const late = new Promise((resolve) => { answerLate = resolve; });
    const getMe = vi.spyOn(api, 'getMe')
      .mockResolvedValueOnce({ username: 'dad', avatar: 'preset:noah' } as never)
      .mockReturnValueOnce(late as never)
      .mockResolvedValue({ username: 'dad', avatar: 'upload:123' } as never);
    const client = new QueryClient();
    render(
      <QueryClientProvider client={client}>
        <AuthProvider><Avatar /></AuthProvider>
      </QueryClientProvider>,
    );
    // Sign-in runs several async steps first; a busy full run took over the default 1 s.
    await waitFor(() => expect(screen.getByTestId('avatar')).toHaveTextContent('preset:noah'), { timeout: 5000 });
    await waitFor(() => expect(getMe).toHaveBeenCalledTimes(2));

    act(() => { client.setQueryData(['me'], { username: 'dad', avatar: 'upload:123' }); });
    await waitFor(() => expect(screen.getByTestId('avatar')).toHaveTextContent('upload:123'));

    // The read that started before the picker's write answers with the old picture: it must not win.
    await act(async () => {
      answerLate({ username: 'dad', avatar: 'preset:noah' });
      await late;
    });
    expect(screen.getByTestId('avatar')).toHaveTextContent('upload:123');
    expect(client.getQueryData<{ avatar: string }>(['me'])?.avatar).toBe('upload:123');

    // Someone else's profile is never taken as the signed-in person's.
    act(() => { client.setQueryData(['me'], { username: 'mom', avatar: 'preset:ruth' }); });
    expect(screen.getByTestId('avatar')).toHaveTextContent('upload:123');
  });
});
