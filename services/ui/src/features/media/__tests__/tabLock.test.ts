import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FakeBroadcastChannel } from '../../../test/fakes/FakeBroadcastChannel';
import { CHANNEL_NAME, HEARTBEAT_MS, LOCK_TTL_MS, TabLock } from '../engine/tabLock';

const tab = (id: string) => new TabLock({ channel: new FakeBroadcastChannel(CHANNEL_NAME), tabId: id });

beforeEach(() => {
  FakeBroadcastChannel.reset();
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
});

describe('TabLock (BUG-66)', () => {
  it('the tab that starts playing takes the audio from the one that had it', () => {
    const a = tab('a');
    const b = tab('b');
    const lostA = vi.fn();
    const lostB = vi.fn();
    a.onLost(lostA);
    b.onLost(lostB);
    a.claim();
    expect(a.current).toBe('this');
    expect(b.current).toBe('other');
    expect(lostB).not.toHaveBeenCalled(); // b was not playing
    b.claim();
    expect(lostA).toHaveBeenCalledTimes(1);
    expect(a.current).toBe('other');
    expect(b.current).toBe('this');
    // "Take over" is simply claiming again.
    a.claim();
    expect(lostB).toHaveBeenCalledTimes(1);
    expect(a.current).toBe('this');
  });

  it('keeps the claim alive with heartbeats, and lets it expire when they stop', () => {
    const a = tab('a');
    const b = tab('b');
    a.claim();
    vi.advanceTimersByTime(HEARTBEAT_MS * 5);
    expect(b.current).toBe('other');
    a.dispose(); // the tab closes without a word (release is sent, but test expiry too)
    const c = tab('c');
    const d = tab('d');
    c.claim();
    (c as unknown as { stopHeartbeat: () => void }).stopHeartbeat();
    vi.advanceTimersByTime(LOCK_TTL_MS + 1);
    expect(d.current).toBe('none');
  });

  it('releasing on pause frees the lock for everyone', () => {
    const a = tab('a');
    const b = tab('b');
    const changes: string[] = [];
    b.onChange((owner) => changes.push(owner));
    a.claim();
    a.release();
    a.release(); // no-op
    expect(b.current).toBe('none');
    expect(changes).toEqual(['other', 'none']);
  });

  it('ignores its own messages, junk, and works without a channel', () => {
    const a = tab('a');
    (a as unknown as { receive: (d: unknown) => void }).receive({ type: 'claim', tab: 'a' });
    (a as unknown as { receive: (d: unknown) => void }).receive(null);
    expect(a.current).toBe('none');
    const lonely = new TabLock({ channel: null });
    lonely.claim();
    expect(lonely.current).toBe('this');
    lonely.dispose();
  });

  it('a heartbeat from another tab while this tab owns the lock does not steal it', () => {
    const a = tab('a');
    const b = tab('b');
    b.claim();
    a.claim(); // b loses
    (a as unknown as { receive: (d: unknown) => void }).receive({ type: 'heartbeat', tab: 'b' });
    expect(a.current).toBe('this');
  });
});
