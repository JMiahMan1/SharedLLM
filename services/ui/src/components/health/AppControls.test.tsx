import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import AppControls from './AppControls';
import { api } from '../../services/api';
import type { ScreenRules } from '../../types/api';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal() as Record<string, unknown>;
  return { ...actual, api: { ...(actual.api as Record<string, unknown>), getUsage: vi.fn(), setScreenRules: vi.fn() } };
});

const YT = 'com.google.android.youtube';
const ROBLOX = 'com.roblox.client';

const wrap = (rules: ScreenRules) =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AppControls username="kid" name="Kid" rules={rules} />
    </QueryClientProvider>,
  );

describe('AppControls (a parent)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getUsage).mockResolvedValue({
      status: 'SUCCESS',
      days: [
        { day: '2026-10-21', millis: 1, first_use: null, last_use: null, sessions: 1, apps: [{ package: YT, millis: 3_600_000 }, { package: 'com.jarvisos.app', millis: 60_000 }] },
        { day: '2026-10-20', millis: 1, first_use: null, last_use: null, sessions: 1, apps: [{ package: YT, millis: 600_000 }] },
      ],
    });
    vi.mocked(api.setScreenRules).mockResolvedValue({ rules: {} });
  });

  it('lists the apps the phone reported, not the always-allowed ones, plus apps with rules', async () => {
    wrap({ daily_limit_min: 120, always_allowed: ['com.jarvisos.app'], blocked_apps: [ROBLOX] });
    expect(await screen.findByTestId(`app-row-${YT}`)).toHaveTextContent('this week');
    expect(screen.getByTestId(`app-row-${ROBLOX}`)).toHaveTextContent('not used this week');
    expect(screen.queryByTestId('app-row-com.jarvisos.app')).toBeNull();
  });

  it('switches an app off and keeps the other rules', async () => {
    wrap({ daily_limit_min: 120, bedtime_start: '21:00', bedtime_end: '07:00', paused: true });
    fireEvent.click(await screen.findByRole('switch', { name: 'YouTube allowed' }));
    await waitFor(() => expect(api.setScreenRules).toHaveBeenCalled());
    const [user, sent] = vi.mocked(api.setScreenRules).mock.calls[0];
    expect(user).toBe('kid');
    expect(sent).toMatchObject({ daily_limit_min: 120, bedtime_start: '21:00', blocked_apps: [YT], app_limits: {} });
    expect(sent).not.toHaveProperty('paused');
  });

  it('sets and clears an app’s daily minutes', async () => {
    wrap({ app_limits: { [YT]: 30 } });
    const input = await screen.findByLabelText(/Daily minutes for/);
    expect(input).toHaveValue(30);
    fireEvent.change(input, { target: { value: '45' } });
    fireEvent.blur(input);
    await waitFor(() => expect(api.setScreenRules).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.setScreenRules).mock.calls[0][1].app_limits).toEqual({ [YT]: 45 });

    fireEvent.change(input, { target: { value: '' } });
    fireEvent.blur(input);
    await waitFor(() => expect(api.setScreenRules).toHaveBeenCalledTimes(2));
    expect(vi.mocked(api.setScreenRules).mock.calls[1][1].app_limits).toEqual({});
  });
});
