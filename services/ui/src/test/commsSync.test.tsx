import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { render } from '@testing-library/react';

import CommsSync from '../components/layout/CommsSync';

const commsMock = vi.hoisted(() => ({
  hasAccess: vi.fn(),
  requestAccess: vi.fn(),
  getNewCalls: vi.fn(),
  getNewSms: vi.fn(),
  syncNow: undefined as undefined | ReturnType<typeof vi.fn>,
}));

vi.mock('../plugins/comms', () => ({ default: commsMock }));

describe('CommsSync', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    localStorage.setItem('jarvis_api_key', 'test-key');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true }));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('lets an APK with the native collector send them itself', async () => {
    // A fresh module: which path it takes is remembered per app start.
    vi.resetModules();
    commsMock.syncNow = vi.fn().mockResolvedValue({ ok: true, calls: 2, sms: 1 });
    commsMock.hasAccess.mockResolvedValue({ calls: true, sms: true });
    const { default: Fresh } = await import('../components/layout/CommsSync');

    render(<Fresh />);

    await vi.waitFor(() => expect(commsMock.syncNow).toHaveBeenCalled());
    expect(commsMock.getNewCalls).not.toHaveBeenCalled();
    expect(commsMock.getNewSms).not.toHaveBeenCalled();
    expect(fetch).not.toHaveBeenCalled();
    commsMock.syncNow = undefined;
    vi.resetModules();
  });

  it('reports a new call once', async () => {
    commsMock.hasAccess.mockResolvedValue({ calls: true, sms: false });
    commsMock.getNewCalls.mockResolvedValue({
      calls: [
        { id: 1, number: '5551234', name: 'Mom', direction: 'in', at: 1700000000000, duration: 42 },
      ],
    });

    render(<CommsSync />);

    await vi.waitFor(() => expect(fetch).toHaveBeenCalled());
    const [url, init] = vi.mocked(fetch).mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/api/activity');
    const body = JSON.parse(String(init.body));
    expect(body.feature).toBe('calls');
    expect(body.event).toBe('call');
    expect(body.item).toBe('Mom');
    expect(body.action).toBe('in');
    expect(body.duration_s).toBe(42);
    expect(body.detail.source).toBe('phone');
  });

  it('reports a new SMS with its body', async () => {
    commsMock.hasAccess.mockResolvedValue({ calls: false, sms: true });
    commsMock.getNewSms.mockResolvedValue({
      messages: [
        { id: 2, number: '5559999', name: '', body: 'hello there', direction: 'in', at: 1700001000000 },
      ],
    });

    render(<CommsSync />);

    await vi.waitFor(() => expect(fetch).toHaveBeenCalled());
    const [url, init] = vi.mocked(fetch).mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/api/activity');
    const body = JSON.parse(String(init.body));
    expect(body.feature).toBe('sms');
    expect(body.event).toBe('message');
    expect(body.item).toBe('5559999');
    expect(body.detail.body).toBe('hello there');
    expect(body.detail.source).toBe('phone');
  });

  it('stays quiet without permission', async () => {
    commsMock.hasAccess.mockResolvedValue({ calls: false, sms: false });

    render(<CommsSync />);

    await vi.waitFor(() => expect(commsMock.hasAccess).toHaveBeenCalled());
    expect(fetch).not.toHaveBeenCalled();
  });
});
