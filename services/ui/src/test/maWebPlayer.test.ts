import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

/** A WebSocket the test opens and closes by hand. */
class FakeSocket {
  static instances: FakeSocket[] = [];
  static OPEN = 1;
  static CONNECTING = 0;
  static CLOSED = 3;
  readyState = 0;
  url: string;
  sent: string[] = [];
  onclose: ((e: { code: number; reason: string }) => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  private listeners: Record<string, Array<(e: unknown) => void>> = {};
  constructor(url: string) {
    this.url = url;
    FakeSocket.instances.push(this);
  }
  addEventListener(type: string, fn: (e: unknown) => void) {
    (this.listeners[type] ||= []).push(fn);
  }
  removeEventListener(type: string, fn: (e: unknown) => void) {
    this.listeners[type] = (this.listeners[type] || []).filter((f) => f !== fn);
  }
  send(data: string) {
    this.sent.push(data);
  }
  open() {
    this.readyState = 1;
    for (const fn of this.listeners.open || []) fn({});
  }
  /** The network dropped it. */
  drop(code = 1006) {
    this.readyState = 3;
    const e = { code, reason: '' };
    this.onclose?.(e);
    for (const fn of this.listeners.close || []) fn(e);
  }
  close() {
    this.readyState = 3;
  }
}

vi.mock('@sendspin/sendspin-js', () => ({
  SendspinPlayer: class {
    volume = 70;
    muted = false;
    trackProgress = null;
    core: { wsManager: { ws: FakeSocket }; isConnected: boolean };
    constructor(opts: { webSocket: FakeSocket }) {
      this.core = { wsManager: { ws: opts.webSocket }, isConnected: true };
    }
    async connect() {
      this.core.wsManager.ws.open();
    }
    disconnect() {}
    setVolume() {}
    setMuted() {}
    sendCommand() {}
  },
}));
vi.mock('../lib/storage', () => ({ storageGetSync: (k: string) => (k === 'jarvis_api_key' ? 'test-key' : null) }));

import { useMAWebPlayer, reconnectDelayMs } from '../lib/maWebPlayer';

const sockets = (path: string) => FakeSocket.instances.filter((s) => s.url.includes(path));
const lastControl = () => sockets('/api/ma-jsonrpc').at(-1)!;

async function connected() {
  const hook = renderHook(() => useMAWebPlayer());
  let done: Promise<void> | undefined;
  act(() => {
    done = hook.result.current.connect();
  });
  await act(async () => {
    await Promise.resolve();
    lastControl().open();
    await done;
  });
  return hook;
}

beforeEach(() => {
  FakeSocket.instances = [];
  vi.stubGlobal('WebSocket', FakeSocket);
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('the web player connection', () => {
  it('backs off 1 s, 2 s, 4 s … up to 30 s', () => {
    expect([1, 2, 3, 4, 6, 10].map(reconnectDelayMs)).toEqual([1000, 2000, 4000, 8000, 30000, 30000]);
  });

  it('connects both sockets through the gateway with the API key', async () => {
    const { result } = await connected();
    expect(result.current.isConnected).toBe(true);
    expect(result.current.connectionState).toBe('CONNECTED');
    expect(sockets('/api/sendspin')[0].url).toContain('token=test-key');
    expect(lastControl().url).toContain('token=test-key');
  });

  it('reconnects by itself when the network drops the control socket', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { result } = await connected();

    act(() => lastControl().drop());
    expect(result.current.isConnected).toBe(false);
    expect(result.current.connectionState).toBe('RECONNECTING');

    const before = sockets('/api/ma-jsonrpc').length;
    await act(async () => { await vi.advanceTimersByTimeAsync(1100); });
    expect(sockets('/api/ma-jsonrpc').length).toBe(before + 1);
    await act(async () => { lastControl().open(); await vi.advanceTimersByTimeAsync(10); });

    expect(result.current.isConnected).toBe(true);
    expect(result.current.connectionState).toBe('CONNECTED');
  });

  it('also recovers when the audio socket drops', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { result } = await connected();
    act(() => sockets('/api/sendspin')[0].drop());
    expect(result.current.connectionState).toBe('RECONNECTING');
    await act(async () => { await vi.advanceTimersByTimeAsync(1100); });
    await act(async () => { lastControl().open(); await vi.advanceTimersByTimeAsync(10); });
    expect(result.current.connectionState).toBe('CONNECTED');
  });

  it('does not reconnect after an explicit disconnect', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { result } = await connected();
    const count = FakeSocket.instances.length;
    act(() => result.current.disconnect());
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(FakeSocket.instances.length).toBe(count);
    expect(result.current.connectionState).toBe('DISCONNECTED');
  });

  it('plays the exact Music Assistant URI it is given (no guessed audiobook URIs)', async () => {
    const { result } = await connected();
    const control = lastControl();
    let done: Promise<void> | undefined;
    act(() => {
      done = result.current.play('library://audiobook/260');
    });
    await act(async () => { await Promise.resolve(); });
    const sent = JSON.parse(control.sent.at(-1)!);
    expect(sent.command).toBe('player_queues/play_media');
    expect(sent.args.media).toBe('library://audiobook/260');
    await act(async () => {
      control.onmessage?.({ data: JSON.stringify({ message_id: sent.message_id, result: null }) });
      // The hook listens with addEventListener for answers.
      for (const fn of (control as unknown as { listeners: Record<string, Array<(e: unknown) => void>> }).listeners.message || []) {
        fn({ data: JSON.stringify({ message_id: sent.message_id, result: {} }) });
      }
      await done;
    });
    expect(result.current.mediaUri).toBe('library://audiobook/260');
  });
});
