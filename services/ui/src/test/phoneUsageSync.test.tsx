import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { render } from '@testing-library/react';

import PhoneUsageSync from '../components/layout/PhoneUsageSync';

const phoneUsageMock = vi.hoisted(() => ({
  hasAccess: vi.fn(),
  openSettings: vi.fn(),
  getToday: vi.fn(),
  getNight: vi.fn(),
}));

vi.mock('../plugins/phoneUsage', () => ({ default: phoneUsageMock }));

describe('PhoneUsageSync', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    localStorage.setItem('jarvis_api_key', 'test-key');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true }));
    phoneUsageMock.getNight.mockResolvedValue({ since: 0, until: 0, intervals: [] });
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
    // Names this device, so a phone and a tablet add up on the server.
    expect(body.device_key).toMatch(/^[0-9a-f]{16,}$/);
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

  it('sends last night\'s screen-on stretches with this device\'s key', async () => {
    localStorage.setItem('jarvis_device_uid', 'device-key-0123456789');
    phoneUsageMock.hasAccess.mockResolvedValue({ granted: true });
    phoneUsageMock.getToday.mockResolvedValue({ millis: 60_000, firstUse: 1, lastUse: 2, sessions: 1 });
    phoneUsageMock.getNight.mockResolvedValue({ since: 0, until: 0, intervals: [[10, 20]] });

    render(<PhoneUsageSync />);

    await vi.waitFor(() =>
      expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).endsWith('/api/usage/night'))).toBe(true),
    );
    const [, init] = vi.mocked(fetch).mock.calls.find(([url]) => String(url).endsWith('/api/usage/night')) as [string, RequestInit];
    const body = JSON.parse(String(init.body));
    expect(body.device_key).toBe('device-key-0123456789');
    expect(body.intervals).toEqual([[10, 20]]);
    expect(body.night).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    const { since, until } = phoneUsageMock.getNight.mock.calls[0][0];
    expect(until - since).toBeLessThanOrEqual(18 * 3600 * 1000);
  });

  it('still posts the day when the APK has no night reader', async () => {
    phoneUsageMock.hasAccess.mockResolvedValue({ granted: true });
    phoneUsageMock.getToday.mockResolvedValue({ millis: 60_000, firstUse: 1, lastUse: 2, sessions: 1 });
    phoneUsageMock.getNight.mockRejectedValue(new Error('not implemented'));

    render(<PhoneUsageSync />);

    await vi.waitFor(() => expect(phoneUsageMock.getNight).toHaveBeenCalled());
    expect(String(vi.mocked(fetch).mock.calls[0][0])).toMatch(/\/api\/usage$/);
  });
});
