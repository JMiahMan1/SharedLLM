import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

/** A WebSocket the test opens and closes by hand. */
class FakeSocket {
  static instances: FakeSocket[] = [];
  /** The player names handed to Sendspin, newest last. */
  static clientNames: string[] = [];
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
  /** How many `players/get` answers report the player unavailable first. */
  static unavailableAnswers = 0;
  send(data: string) {
    this.sent.push(data);
    // Answer like MA: a new Sendspin player is listed a moment after connecting.
    const msg = JSON.parse(data) as { message_id: string; command: string };
    if (msg.command === 'players/get') {
      const available = FakeSocket.unavailableAnswers <= 0;
      FakeSocket.unavailableAnswers -= 1;
      queueMicrotask(() => {
        const e = { data: JSON.stringify({ message_id: msg.message_id, result: { available } }) };
        for (const fn of this.listeners.message || []) fn(e);
      });
    }
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
    constructor(opts: { webSocket: FakeSocket; clientName: string }) {
      this.core = { wsManager: { ws: opts.webSocket }, isConnected: true };
      FakeSocket.clientNames.push(opts.clientName);
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
const stored = vi.hoisted(() => ({ values: {} as Record<string, string> }));
vi.mock('../lib/storage', () => ({
  storageGetSync: (k: string) => (k === 'jarvis_api_key' ? 'test-key' : stored.values[k] ?? null),
}));

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
  FakeSocket.clientNames = [];
  FakeSocket.unavailableAnswers = 0;
  stored.values = {};
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

  it('names the player after the person, even from a profile stored twice-encoded', async () => {
    stored.values = { jarvis_user: JSON.stringify(JSON.stringify({ display_name: 'Jeremiah' })) };
    await connected();
    expect(FakeSocket.clientNames.at(-1)).toMatch(/^Jeremiah's Web Player/);
  });

  it('never names the player with a stored blob of data', async () => {
    stored.values = { jarvis_user: 'not json', username: '{"id":1,"username":"default"}' };
    await connected();
    expect(FakeSocket.clientNames.at(-1)).toMatch(/^Web Player \(/);
  });

  it('waits for MA to list the new player before playing on it', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    FakeSocket.unavailableAnswers = 2;
    const hook = renderHook(() => useMAWebPlayer());
    let played: Promise<void> | undefined;
    act(() => {
      played = hook.result.current.play('library://track/1');
    });
    await act(async () => {
      await Promise.resolve();
      lastControl().open();
      await vi.advanceTimersByTimeAsync(1000);
    });
    const commands = lastControl().sent.map((s) => (JSON.parse(s) as { command: string }).command);
    // Asked until available, and only then told to play.
    expect(commands.filter((c) => c === 'players/get')).toHaveLength(3);
    expect(commands.indexOf('player_queues/play_media')).toBeGreaterThan(commands.lastIndexOf('players/get'));
    void played?.catch(() => {});
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
