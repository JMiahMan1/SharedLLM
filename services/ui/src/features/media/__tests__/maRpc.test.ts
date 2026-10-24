import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FakeWebSocket } from '../../../test/fakes/FakeWebSocket';
import { eventIsForPlayer, MaRpc } from '../engine/maRpc';

let sockets: FakeWebSocket[] = [];
class TrackedSocket extends FakeWebSocket {
  constructor(url: string) {
    super(url);
    sockets.push(this);
  }
}

const sent = (ws: FakeWebSocket) => ws.sentMessages.map((m) => JSON.parse(m) as { message_id: string; command: string; args: unknown });

beforeEach(() => {
  sockets = [];
  vi.stubGlobal('WebSocket', TrackedSocket);
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

async function open(rpc = new MaRpc()) {
  await rpc.connect('ws://gw/api/ma-jsonrpc');
  return { rpc, ws: sockets.at(-1)! };
}

describe('MaRpc', () => {
  it('BUG-46 shares one connection attempt between concurrent callers', async () => {
    const rpc = new MaRpc();
    const a = rpc.connect('ws://gw/api/ma-jsonrpc');
    const b = rpc.connect('ws://gw/api/ma-jsonrpc');
    expect(a).toBe(b);
    await Promise.all([a, b]);
    expect(sockets).toHaveLength(1);
    expect(rpc.isOpen).toBe(true);
    await rpc.connect('ws://gw/api/ma-jsonrpc');
    expect(sockets).toHaveLength(1);
  });

  it('answers each call by its own message id', async () => {
    const { rpc, ws } = await open();
    const first = rpc.call('players/all');
    const second = rpc.call('players/get', { player_id: 'p1' });
    const [m1, m2] = sent(ws);
    expect(m2).toMatchObject({ command: 'players/get', args: { player_id: 'p1' } });
    ws.emitServerMessage(JSON.stringify({ message_id: m2.message_id, result: { player_id: 'p1' } }));
    ws.emitServerMessage(JSON.stringify({ message_id: 'not-mine', result: 'wrong' }));
    ws.emitServerMessage(JSON.stringify({ message_id: m1.message_id, result: ['a'] }));
    await expect(first).resolves.toEqual(['a']);
    await expect(second).resolves.toEqual({ player_id: 'p1' });
    expect(rpc.pendingCount).toBe(0);
  });

  it('resolves an answer that carries no result', async () => {
    const { rpc, ws } = await open();
    const call = rpc.call('players/cmd/pause', { player_id: 'p1' });
    ws.emitServerMessage(JSON.stringify({ message_id: sent(ws)[0].message_id }));
    await expect(call).resolves.toBeUndefined();
  });

  it('BUG-53 rejects with MA\'s own error text', async () => {
    const { rpc, ws } = await open();
    const call = rpc.call('player_queues/next', { queue_id: 'q' });
    ws.emitServerMessage(JSON.stringify({ message_id: sent(ws)[0].message_id, error_code: 999, details: 'The player is not available' }));
    await expect(call).rejects.toThrow('Music Assistant: The player is not available');
    const other = rpc.call('x');
    ws.emitServerMessage(JSON.stringify({ message_id: sent(ws)[1].message_id, error: { code: 1 } }));
    await expect(other).rejects.toThrow('{"code":1}');
  });

  it('BUG-48 times a call out and forgets it', async () => {
    vi.useFakeTimers();
    const rpc = new MaRpc({ timeoutMs: 500 });
    const connecting = rpc.connect('ws://gw/api/ma-jsonrpc');
    await vi.advanceTimersByTimeAsync(0);
    await connecting;
    const call = rpc.call('players/all');
    const failed = expect(call).rejects.toThrow('did not answer players/all');
    await vi.advanceTimersByTimeAsync(501);
    await failed;
    expect(rpc.pendingCount).toBe(0);
  });

  it('BUG-48 fails every waiting call the moment the socket drops, and reports it', async () => {
    const { rpc, ws } = await open();
    const lost = vi.fn();
    rpc.onUnexpectedClose(lost);
    const a = rpc.call('players/all');
    const b = rpc.call('player_queues/items');
    ws.emitServerClose(1006, 'gone');
    await expect(a).rejects.toThrow(/connection closed/);
    await expect(b).rejects.toThrow(/connection closed/);
    expect(lost).toHaveBeenCalledWith(expect.objectContaining({ code: 1006 }));
    expect(rpc.isOpen).toBe(false);
    await expect(rpc.call('players/all')).rejects.toThrow('not connected');
  });

  it('closing on purpose fails waiting calls but is not a lost connection', async () => {
    const { rpc } = await open();
    const lost = vi.fn();
    rpc.onUnexpectedClose(lost);
    const a = rpc.call('players/all');
    rpc.close('shutdown');
    await expect(a).rejects.toThrow('Music Assistant connection closed (shutdown)');
    expect(lost).not.toHaveBeenCalled();
    rpc.close('again');
  });

  it('rejects the connection when the socket closes or errors before opening', async () => {
    class NeverOpens extends FakeWebSocket {
      open() { /* stays connecting */ }
    }
    vi.stubGlobal('WebSocket', NeverOpens);
    const rpc = new MaRpc();
    const p = rpc.connect('ws://gw/x');
    // Find the socket via the promise's side effect: close it from outside.
    await Promise.resolve();
    (rpc as unknown as { ws: FakeWebSocket }).ws.emitError();
    await expect(p).rejects.toThrow('Could not reach Music Assistant');
  });

  it('gives up connecting after the timeout', async () => {
    vi.useFakeTimers();
    class NeverOpens extends FakeWebSocket {
      open() { /* stays connecting */ }
    }
    vi.stubGlobal('WebSocket', NeverOpens);
    const rpc = new MaRpc({ connectTimeoutMs: 1000 });
    const p = rpc.connect('ws://gw/x');
    const failed = expect(p).rejects.toThrow('did not answer in time');
    await vi.advanceTimersByTimeAsync(1001);
    await failed;
  });

  it('delivers pushed events with the envelope object id, and ignores junk', async () => {
    const { rpc, ws } = await open();
    const seen = vi.fn();
    const off = rpc.onEvent(seen);
    ws.emitServerMessage('not json');
    ws.emitServerMessage(JSON.stringify({ event: 'player_updated', object_id: 'p1', data: { volume_level: 10 } }));
    ws.emitServerMessage(JSON.stringify({ event: 'queue_updated', data: { queue_id: 'q' } }));
    expect(seen).toHaveBeenNthCalledWith(1, 'player_updated', { volume_level: 10 }, 'p1');
    expect(seen).toHaveBeenNthCalledWith(2, 'queue_updated', { queue_id: 'q' }, undefined);
    off();
    ws.emitServerMessage(JSON.stringify({ event: 'player_updated', data: {} }));
    expect(seen).toHaveBeenCalledTimes(2);
  });

  it('fails a call whose send throws', async () => {
    const { rpc, ws } = await open();
    ws.send = () => { throw new Error('socket is closing'); };
    await expect(rpc.call('players/all')).rejects.toThrow('socket is closing');
  });
});

describe('eventIsForPlayer', () => {
  it('BUG-52 matches player ids exactly, not as substrings', () => {
    expect(eventIsForPlayer('player_updated', { player_id: 'kitchen' }, 'kitchen')).toBe(true);
    expect(eventIsForPlayer('player_updated', { player_id: 'kitchen_2' }, 'kitchen')).toBe(false);
    expect(eventIsForPlayer('player_updated', { player_id: 'kit' }, 'kitchen')).toBe(false);
    expect(eventIsForPlayer('queue_updated', { queue_id: 'kitchen' }, 'kitchen')).toBe(true);
    expect(eventIsForPlayer('queue_time_updated', { queue_id: 'other' }, 'kitchen')).toBe(false);
  });
  it('prefers the envelope object id', () => {
    expect(eventIsForPlayer('player_updated', { player_id: 'kitchen' }, 'kitchen', 'office')).toBe(false);
    expect(eventIsForPlayer('player_updated', {}, 'kitchen', 'kitchen')).toBe(true);
  });
  it('lets through events that name nobody, and everything when no id is known', () => {
    expect(eventIsForPlayer('providers_updated', {}, 'kitchen')).toBe(true);
    expect(eventIsForPlayer('player_updated', { player_id: 'x' }, '')).toBe(true);
  });
});
