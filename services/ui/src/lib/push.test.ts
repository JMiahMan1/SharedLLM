import { afterEach, describe, expect, it, vi } from 'vitest';
import { sharePushKey } from './push';

/** The page hands the signed-in person's key to the push worker, so a notice's buttons act as them. */

function browserWithWorker(active: { postMessage: (m: unknown) => void } | null) {
  vi.stubGlobal('Notification', function Notification() {});
  vi.stubGlobal('PushManager', function PushManager() {});
  Object.defineProperty(navigator, 'serviceWorker', {
    configurable: true,
    value: { getRegistration: vi.fn(async (scope: string) => (scope === '/push-sw.js' && active ? { active } : undefined)) },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  // jsdom has no service worker: leave it as it was.
  delete (navigator as unknown as Record<string, unknown>).serviceWorker;
});

describe('sharePushKey', () => {
  it('posts the key to the push worker', async () => {
    const postMessage = vi.fn();
    browserWithWorker({ postMessage });
    expect(await sharePushKey('person-key')).toBe(true);
    expect(postMessage).toHaveBeenCalledWith({ type: 'jarvis-push-key', key: 'person-key' });
    await sharePushKey(null);
    expect(postMessage).toHaveBeenLastCalledWith({ type: 'jarvis-push-key', key: null });
  });

  it('does nothing where push was never enabled', async () => {
    browserWithWorker(null);
    expect(await sharePushKey('person-key')).toBe(false);
  });
});
