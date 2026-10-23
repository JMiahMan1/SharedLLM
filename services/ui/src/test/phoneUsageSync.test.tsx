import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { render } from '@testing-library/react';

import PhoneUsageSync from '../components/layout/PhoneUsageSync';

const phoneUsageMock = vi.hoisted(() => ({
  hasAccess: vi.fn(),
  openSettings: vi.fn(),
  getToday: vi.fn(),
}));

vi.mock('../plugins/phoneUsage', () => ({ default: phoneUsageMock }));

describe('PhoneUsageSync', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    localStorage.setItem('jarvis_api_key', 'test-key');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true }));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('posts the day once the usage permission is granted', async () => {
    phoneUsageMock.hasAccess.mockResolvedValue({ granted: true });
    phoneUsageMock.getToday.mockResolvedValue({
      millis: 3_600_000,
      firstUse: 1_700_000_000_000,
      lastUse: 1_700_004_000_000,
      sessions: 3,
    });

    render(<PhoneUsageSync />);

    await vi.waitFor(() => expect(fetch).toHaveBeenCalled());
    const [url, init] = vi.mocked(fetch).mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/api/usage');
    const body = JSON.parse(String(init.body));
    expect(body.millis).toBe(3_600_000);
    expect(body.sessions).toBe(3);
    expect(body.source).toBe('phone');
    expect(body.day).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });

  it('stays quiet without the usage permission', async () => {
    phoneUsageMock.hasAccess.mockResolvedValue({ granted: false });
    phoneUsageMock.getToday.mockResolvedValue({
      millis: 0,
      firstUse: null,
      lastUse: null,
      sessions: 0,
    });

    render(<PhoneUsageSync />);

    await vi.waitFor(() => expect(phoneUsageMock.hasAccess).toHaveBeenCalled());
    expect(fetch).not.toHaveBeenCalled();
  });
});
