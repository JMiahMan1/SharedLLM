import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { server } from '../../../test/setup';
import {
  FALLBACK_AFTER_MS, FALLBACK_POLL_MS, MediaEventStream, mediaEventsUrl, REOPEN_MS,
} from '../engine/eventStream';
import { acquireMediaFeed } from '../engine/statusFeed';
import { useMediaStore } from '../store/mediaStore';
import { resetMediaTokenForTests } from '../../../lib/mediaToken';
import { serviceForPath } from '../../../services/api';

class FakeEventSource {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSED = 2;
  static instances: FakeEventSource[] = [];
  readyState = FakeEventSource.CONNECTING;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  private listeners: Record<string, Array<(e: { data: string }) => void>> = {};
  url: string;
  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }
  addEventListener(type: string, fn: (e: { data: string }) => void) {
    (this.listeners[type] ||= []).push(fn);
  }
  close() {
    this.closed = true;
    this.readyState = FakeEventSource.CLOSED;
  }
  open() {
    this.readyState = FakeEventSource.OPEN;
    this.onopen?.();
  }
  emit(type: string, data: unknown) {
    for (const fn of this.listeners[type] || []) fn({ data: typeof data === 'string' ? data : JSON.stringify(data) });
  }
  /** The connection dropped; the browser retries on its own. */
  drop() {
    this.readyState = FakeEventSource.CONNECTING;
    this.onerror?.();
  }
  /** The server refused it; the browser gives up. */
  refuse() {
    this.readyState = FakeEventSource.CLOSED;
    this.onerror?.();
  }
}

const Impl = FakeEventSource as unknown as typeof EventSource;
const latest = () => FakeEventSource.instances.at(-1)!;

function make(overrides: Partial<ConstructorParameters<typeof MediaEventStream>[0]> = {}) {
  const events: unknown[] = [];
  const states: string[] = [];
  const poll = vi.fn().mockResolvedValue(undefined);
  const stream = new MediaEventStream({
    url: async () => 'https://jarvis.test/api/media/events?user=u&mt=t',
    onEvent: (e) => events.push(e),
    onState: (s) => states.push(s),
    poll,
    EventSourceImpl: Impl,
    ...overrides,
  });
  return { stream, events, states, poll };
}

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.useFakeTimers({ shouldAdvanceTime: false });
});
afterEach(() => {
  vi.useRealTimers();
});

describe('MediaEventStream', () => {
  it('opens, delivers snapshot/player/queue events and stops cleanly', async () => {
    const { stream, events, states } = make();
    stream.start();
    stream.start(); // idempotent
    await vi.advanceTimersByTimeAsync(0);
    expect(FakeEventSource.instances).toHaveLength(1);
    latest().open();
    expect(stream.current).toBe('open');
    latest().emit('snapshot', { type: 'snapshot', players: [{ output_id: 'media_player.k', state: 'playing' }] });
    latest().emit('player', { output_id: 'media_player.k', state: 'paused' });
    latest().emit('queue', { output_id: 'ma:o', items_changed: true });
    latest().emit('heartbeat', {});
    latest().emit('player', 'not json');
    expect(events.map((e) => (e as { type: string }).type)).toEqual(['snapshot', 'player', 'queue', 'heartbeat']);
    stream.stop();
    expect(latest().closed).toBe(true);
    expect(states).toEqual(['connecting', 'open', 'closed']);
  });

  it('falls back to polling every 10 s once the stream has been down 5 s, and stops when it is back', async () => {
    const { stream, poll } = make();
    stream.start();
    await vi.advanceTimersByTimeAsync(0);
    latest().open();
    latest().drop();
    expect(stream.current).toBe('closed');
    await vi.advanceTimersByTimeAsync(FALLBACK_AFTER_MS - 1);
    expect(poll).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(poll).toHaveBeenCalledTimes(1);
    expect(stream.polling).toBe(true);
    await vi.advanceTimersByTimeAsync(FALLBACK_POLL_MS);
    expect(poll).toHaveBeenCalledTimes(2);
    // The browser's own retry succeeds: the snapshot resyncs, polling ends.
    latest().open();
    expect(stream.polling).toBe(false);
    await vi.advanceTimersByTimeAsync(FALLBACK_POLL_MS * 3);
    expect(poll).toHaveBeenCalledTimes(2);
    stream.stop();
  });

  it('a stream that opens within 5 s never polls', async () => {
    const { stream, poll } = make();
    stream.start();
    await vi.advanceTimersByTimeAsync(1000);
    latest().open();
    await vi.advanceTimersByTimeAsync(FALLBACK_AFTER_MS * 2);
    expect(poll).not.toHaveBeenCalled();
    stream.stop();
  });

  it('reopens with a fresh URL when the server refuses the stream', async () => {
    let n = 0;
    const { stream } = make({ url: async () => `https://jarvis.test/api/media/events?mt=${(n += 1)}` });
    stream.start();
    await vi.advanceTimersByTimeAsync(0);
    latest().refuse();
    expect(latest().closed).toBe(true);
    await vi.advanceTimersByTimeAsync(REOPEN_MS);
    expect(FakeEventSource.instances).toHaveLength(2);
    expect(latest().url).toContain('mt=2');
    // Events from the old source are ignored.
    stream.stop();
  });

  it('polls and keeps retrying when no stream URL can be built', async () => {
    const { stream, poll } = make({ url: async () => { throw new Error('no token'); } });
    stream.start();
    await vi.advanceTimersByTimeAsync(FALLBACK_AFTER_MS);
    expect(poll).toHaveBeenCalledTimes(1);
    expect(FakeEventSource.instances).toHaveLength(0);
    stream.stop();
    await vi.advanceTimersByTimeAsync(FALLBACK_POLL_MS * 2);
    expect(poll).toHaveBeenCalledTimes(1);
  });

  it('skips fallback polls while the tab is hidden', async () => {
    const { stream, poll } = make({ url: async () => null });
    stream.start();
    await vi.advanceTimersByTimeAsync(FALLBACK_AFTER_MS);
    const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true);
    await vi.advanceTimersByTimeAsync(FALLBACK_POLL_MS * 2);
    expect(poll).toHaveBeenCalledTimes(1);
    hidden.mockRestore();
    stream.stop();
  });
});

describe('the shared feed', () => {
  it('builds the stream URL from the signed media token', async () => {
    vi.useRealTimers();
    resetMediaTokenForTests();
    localStorage.setItem('jarvis_api_key', 'k');
    const url = await mediaEventsUrl();
    expect(url).toMatch(/\/api\/media\/events\?user=alice&mt=mt-1$/);
    localStorage.removeItem('jarvis_api_key');
    resetMediaTokenForTests();
    expect(await mediaEventsUrl()).toBeNull();
  });

  it('reads the full status once for every screen that shares it', async () => {
    vi.useRealTimers();
    let reads = 0;
    server.use(http.post('/execute/media/status', () => {
      reads += 1;
      return HttpResponse.json({ status: 'SUCCESS', detail: { active: null, all_players: [{ entity_id: 'media_player.k', friendly_name: 'K', state: 'idle' }] } });
    }));
    const releaseA = acquireMediaFeed();
    const releaseB = acquireMediaFeed();
    await vi.waitFor(() => expect(useMediaStore.getState().players['media_player.k']?.name).toBe('K'));
    expect(reads).toBe(1);
    releaseA();
    releaseA();
    releaseB();
    expect(useMediaStore.getState().events).toBe('closed');
  });
});

describe('UX-46 failure labels', () => {
  it('names the media service for media calls', () => {
    expect(serviceForPath('/execute/media/status')).toBe('Media service');
    expect(serviceForPath('/api/media/home')).toBe('Media service');
    expect(serviceForPath('/execute/notes')).toBe('Execution service');
    expect(serviceForPath('/api/users/me')).toBe('Identity service');
    expect(serviceForPath('/api/ma-jsonrpc')).toBe('Music Assistant');
    expect(serviceForPath('/api/notes')).toBe('Gateway');
    expect(serviceForPath('/ws/sendspin')).toBe('Music Assistant');
    expect(serviceForPath('/other')).toBe('Jarvis server');
  });
});
