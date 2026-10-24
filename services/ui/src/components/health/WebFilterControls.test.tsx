import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import WebFilterControls from './WebFilterControls';
import { api } from '../../services/api';
import type { ScreenRules } from '../../types/api';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal() as Record<string, unknown>;
  return { ...actual, api: { ...(actual.api as Record<string, unknown>), setScreenRules: vi.fn() } };
});

const wrap = (rules: ScreenRules) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <WebFilterControls username="kid" name="Kid" rules={rules} />
    </QueryClientProvider>,
  );

describe('WebFilterControls (a parent)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.setScreenRules).mockResolvedValue({ rules: {} });
  });

  it('turns the filter on with family-safe sites and safe search, keeping the other rules', async () => {
    wrap({ daily_limit_min: 90, blocked_apps: ['com.roblox.client'], paused: true });
    expect(screen.getByTestId('web-filter-adult')).toBeDisabled();
    fireEvent.click(screen.getByTestId('web-filter-enabled'));
    await waitFor(() => expect(api.setScreenRules).toHaveBeenCalled());
    const sent = vi.mocked(api.setScreenRules).mock.calls[0][1];
    expect(sent).toMatchObject({
      daily_limit_min: 90,
      blocked_apps: ['com.roblox.client'],
      web_filter: { enabled: true, adult: true, safe_search: true, block: [], allow: [] },
    });
    expect(sent).not.toHaveProperty('paused');
  });

  it('saves block and allow lists from pasted text and links', async () => {
    wrap({ web_filter: { enabled: true, adult: true, safe_search: false } });
    fireEvent.change(screen.getByPlaceholderText('tiktok.com'), { target: { value: 'TikTok.com\nhttps://www.reddit.com/r/x' } });
    fireEvent.change(screen.getByPlaceholderText('khanacademy.org'), { target: { value: 'khanacademy.org, ' } });
    fireEvent.click(screen.getByTestId('web-filter-save'));
    await waitFor(() => expect(api.setScreenRules).toHaveBeenCalled());
    expect(vi.mocked(api.setScreenRules).mock.calls[0][1].web_filter).toEqual({
      enabled: true, adult: true, safe_search: false, block: ['tiktok.com', 'www.reddit.com'], allow: ['khanacademy.org'],
    });
  });
});
