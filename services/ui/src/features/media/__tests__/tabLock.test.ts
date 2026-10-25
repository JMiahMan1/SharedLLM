import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FakeBroadcastChannel } from '../../../test/fakes/FakeBroadcastChannel';
import { CHANNEL_NAME, HEARTBEAT_MS, LOCK_TTL_MS, TabLock, type LockOwner } from '../engine/tabLock';

const tab = (id: string) => new TabLock({ channel: new FakeBroadcastChannel(CHANNEL_NAME), tabId: id });

/** Watch the owner the way the app does (onChange), never the `current` getter. */
function track(lock: TabLock) {
  const seen: LockOwner[] = [];
  lock.onChange((owner) => seen.push(owner));
  return { seen, last: (): LockOwner => seen.at(-1) ?? 'none' };
}

/**
 * A BroadcastChannel that holds messages until `flush()`, so two tabs can
 * claim in the same tick the way a real (asynchronous) channel delivers.
 */
class DeferredChannel {
  static registry = new Map<string, Set<DeferredChannel>>();
  private readonly name: string;
  private queue: unknown[] = [];
  private listeners: Array<(event: { data: unknown }) => void> = [];
  constructor(name: string) {
    this.name = name;
    const peers = DeferredChannel.registry.get(name) ?? new Set<DeferredChannel>();
    peers.add(this);
    DeferredChannel.registry.set(name, peers);
  }
  postMessage(data: unknown): void {
    for (const peer of DeferredChannel.registry.get(this.name) ?? []) {
      if (peer !== this) peer.queue.push(data);
    }
  }
  close(): void {
    DeferredChannel.registry.get(this.name)?.delete(this);
  }
  addEventListener(_type: 'message', listener: (event: { data: unknown }) => void): void {
    this.listeners.push(listener);
  }
  flush(): void {
    const queued = this.queue;
    this.queue = [];
    for (const data of queued) for (const listener of [...this.listeners]) listener({ data });
  }
  static reset(): void {
    DeferredChannel.registry.clear();
  }
}

beforeEach(() => {
  FakeBroadcastChannel.reset();
  DeferredChannel.reset();
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
    const ta = track(a);
    const tb = track(b);
    a.claim();
    expect(ta.last()).toBe('this');
    expect(tb.last()).toBe('other');
    expect(lostB).not.toHaveBeenCalled(); // b was not playing
    vi.advanceTimersByTime(1);
    b.claim();
    expect(lostA).toHaveBeenCalledTimes(1);
    expect(ta.last()).toBe('other');
    expect(tb.last()).toBe('this');
    // "Take over" is simply claiming again.
    vi.advanceTimersByTime(1);
    a.claim();
    expect(lostB).toHaveBeenCalledTimes(1);
    expect(ta.last()).toBe('this');
  });

  it('keeps the claim alive with heartbeats, and drops a stale owner when they stop', () => {
    const a = tab('a');
    const b = tab('b');
    const tb = track(b);
    a.claim();
    expect(tb.last()).toBe('other');
    vi.advanceTimersByTime(HEARTBEAT_MS * 5);
    expect(tb.last()).toBe('other'); // the heartbeats kept it alive
    // a crashes without a word: its heartbeats stop and no release is sent.
    (a as unknown as { stopHeartbeat: () => void }).stopHeartbeat();
    vi.advanceTimersByTime(LOCK_TTL_MS + 1);
    expect(tb.last()).toBe('none'); // the non-owner dropped the stale owner
  });

  it('two tabs claiming in the same tick leave exactly one owner', () => {
    const chanA = new DeferredChannel('race');
    const chanB = new DeferredChannel('race');
    const a = new TabLock({ channel: chanA, tabId: 'a' });
    const b = new TabLock({ channel: chanB, tabId: 'b' });
    const ta = track(a);
    const tb = track(b);
    a.claim();
    b.claim(); // both claims are in flight; neither has been delivered yet
    chanA.flush();
    chanB.flush();
    const owners = [ta.last(), tb.last()];
    expect(owners.filter((owner) => owner === 'this')).toHaveLength(1);
    expect(owners.filter((owner) => owner === 'other')).toHaveLength(1);
    a.dispose();
    b.dispose();
  });

  it('releasing on pause frees the lock for everyone', () => {
    const a = tab('a');
    const b = tab('b');
    const tb = track(b);
    a.claim();
    a.release();
    a.release(); // no-op
    expect(tb.last()).toBe('none');
    expect(tb.seen).toEqual(['other', 'none']);
  });

  it('ignores its own messages and junk, and works without a channel', () => {
    const a = tab('a');
    (a as unknown as { receive: (d: unknown) => void }).receive(null);
    (a as unknown as { receive: (d: unknown) => void }).receive({ type: 'claim', tab: 'a', ts: 1 });
    expect(track(a).last()).toBe('none');
    const lonely = new TabLock({ channel: null });
    const tl = track(lonely);
    lonely.claim();
    expect(tl.last()).toBe('this');
    lonely.dispose();
  });

  it('a heartbeat from another tab while this tab owns the lock does not steal it', () => {
    const a = tab('a');
    const b = tab('b');
    const ta = track(a);
    b.claim();
    vi.advanceTimersByTime(1);
    a.claim(); // b loses
    (a as unknown as { receive: (d: unknown) => void }).receive({ type: 'heartbeat', tab: 'b' });
    expect(ta.last()).toBe('this');
  });
});
