import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ isNative: vi.fn(() => false) }));

vi.mock('@capacitor/core', () => ({
  Capacitor: { isNativePlatform: mocks.isNative },
  registerPlugin: vi.fn(),
}));

import { gatewaySocketUrl } from './serverUrl';

describe('gatewaySocketUrl', () => {
  beforeEach(() => {
    mocks.isNative.mockReturnValue(false);
    localStorage.clear();
  });

  it('uses the page origin on the web', () => {
    const url = new URL(gatewaySocketUrl('/api/workspaces/demo/terminal', 'a b'));
    expect(url.protocol).toBe(window.location.protocol === 'https:' ? 'wss:' : 'ws:');
    expect(url.host).toBe(window.location.host);
    expect(url.pathname).toBe('/api/workspaces/demo/terminal');
    expect(url.searchParams.get('token')).toBe('a b');
  });

  it('uses the configured server inside the app, never the bundled page origin', () => {
    mocks.isNative.mockReturnValue(true);
    localStorage.setItem('jarvis_server_url', 'https://jarvis.example.test/app');
    expect(gatewaySocketUrl('/api/workspaces/demo/terminal', 'tok')).toBe(
      'wss://jarvis.example.test/api/workspaces/demo/terminal?token=tok',
    );
  });
});
