import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

// A socket that opens at once and records what was sent (BUG-48).
class FakeSocket extends EventTarget {
  static OPEN = 1;
  static instances: FakeSocket[] = [];
  readyState = 1;
  url: string;
  sent: string[] = [];
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  constructor(url: string) {
    super();
    this.url = url;
    FakeSocket.instances.push(this);
  }
  send(data: string) {
    this.sent.push(data);
  }
  close() {
    this.readyState = 3;
  }
  answer(payload: Record<string, unknown>) {
    this.dispatchEvent(new MessageEvent('message', { data: JSON.stringify(payload) }));
  }
}

vi.mock('@sendspin/sendspin-js', () => ({
  SendspinPlayer: class {
    core: { wsManager: { ws: FakeSocket }; isConnected: boolean };
    constructor(opts: { webSocket: FakeSocket }) {
      this.core = { wsManager: { ws: opts.webSocket }, isConnected: true };
    }
    async connect() {}
    disconnect() {}
    setVolume() {}
    setMuted() {}
  },
}));

import { useMAWebPlayer } from './maWebPlayer';

const controlSocket = () => FakeSocket.instances.find((ws) => ws.url.includes('/api/ma-jsonrpc'))!;

describe('Music Assistant JSON-RPC calls (BUG-48)', () => {
  beforeEach(() => {
    FakeSocket.instances = [];
    vi.stubGlobal('WebSocket', FakeSocket);
    localStorage.setItem('jarvis_api_key', 'test-key');
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it('answers a call by its message id', async () => {
    const { result } = renderHook(() => useMAWebPlayer());
    await act(async () => { await result.current.connect(); });

    const call = result.current.maCommand('players/all');
    const sent = JSON.parse(controlSocket().sent.at(-1)!);
    controlSocket().answer({ message_id: 'someone-else', result: ['wrong'] });
    controlSocket().answer({ message_id: sent.message_id, result: ['player'] });
    await expect(call).resolves.toEqual(['player']);
  });

  it('fails calls still waiting the moment the connection closes', async () => {
    const { result } = renderHook(() => useMAWebPlayer());
    await act(async () => { await result.current.connect(); });

    const started = Date.now();
    const call = result.current.maCommand('players/all');
    act(() => result.current.disconnect());
    await expect(call).rejects.toThrow(/connection closed/);
    expect(Date.now() - started).toBeLessThan(1000);
  });
});
