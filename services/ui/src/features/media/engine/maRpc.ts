/**
 * Music Assistant JSON-RPC over one WebSocket (the gateway's
 * `/api/ma-jsonrpc`), as a plain class so it can be tested without React.
 *
 * - `connect()` hands every concurrent caller the same attempt (BUG-46).
 * - Each call waits in a pending map keyed by `message_id` with its own
 *   timeout; nothing is left listening after it settles (BUG-48).
 * - Closing (on purpose or not) fails everything still waiting at once.
 * - MA's pushed events go to `onEvent` subscribers.
 *
 * Reconnecting is deliberately not done here: the web player owns two sockets
 * (this one and Sendspin's audio) that must come back together, so
 * `lib/maWebPlayer.ts` tears both down and reconnects them with backoff
 * (BUG-50) when `onUnexpectedClose` fires.
 */

export const RPC_TIMEOUT_MS = 10_000;
export const RPC_CONNECT_TIMEOUT_MS = 15_000;

/** `objectId` is the player or queue an event is about, when MA names it. */
type MaEventListener = (event: string, data: Record<string, unknown>, objectId?: string) => void;
type CloseListener = (event?: { code?: number; reason?: string }) => void;

interface Pending {
  command: string;
  resolve: (value: unknown) => void;
  reject: (error: Error) => void;
  timer: ReturnType<typeof setTimeout>;
}

export class MaRpcError extends Error {
  readonly command: string;
  constructor(command: string, message: string) {
    super(message);
    this.name = 'MaRpcError';
    this.command = command;
  }
}

/** The text of an MA error answer. */
function errorDetail(data: Record<string, unknown>): string {
  if (typeof data.details === 'string' && data.details) return data.details;
  if (typeof data.error === 'string' && data.error) return data.error;
  if (data.error !== undefined) return JSON.stringify(data.error);
  return `Error code ${String(data.error_code)}`;
}

export class MaRpc {
  private ws: WebSocket | null = null;
  private connecting: Promise<void> | null = null;
  private readonly pending = new Map<string, Pending>();
  private readonly eventListeners = new Set<MaEventListener>();
  private readonly closeListeners = new Set<CloseListener>();
  private seq = 0;
  private closedOnPurpose = false;
  private readonly timeoutMs: number;
  private readonly connectTimeoutMs: number;

  constructor(opts: { timeoutMs?: number; connectTimeoutMs?: number } = {}) {
    this.timeoutMs = opts.timeoutMs ?? RPC_TIMEOUT_MS;
    this.connectTimeoutMs = opts.connectTimeoutMs ?? RPC_CONNECT_TIMEOUT_MS;
  }

  get isOpen(): boolean {
    return this.ws !== null && this.ws.readyState === WebSocket.OPEN;
  }

  /** Calls still waiting for an answer. */
  get pendingCount(): number {
    return this.pending.size;
  }

  /** Open the socket at `url`; concurrent callers share one attempt. */
  connect(url: string): Promise<void> {
    if (this.isOpen) return Promise.resolve();
    if (this.connecting) return this.connecting;
    this.closedOnPurpose = false;
    const ws = new WebSocket(url);
    this.ws = ws;
    ws.addEventListener('message', this.handleMessage as EventListener);
    ws.addEventListener('close', this.handleClose as EventListener);
    this.connecting = new Promise<void>((resolve, reject) => {
      if (ws.readyState === WebSocket.OPEN) {
        resolve();
        return;
      }
      const removeHandlers = () => {
        clearTimeout(timer);
        ws.removeEventListener('open', onOpen);
        ws.removeEventListener('error', onFail);
        ws.removeEventListener('close', onFail);
      };
      // A failed connect must not leave the half-open socket behind: the next
      // connect() would open a second one and leak this.
      const fail = () => {
        removeHandlers();
        if (this.ws === ws) {
          this.ws = null;
          ws.removeEventListener('message', this.handleMessage as EventListener);
          ws.removeEventListener('close', this.handleClose as EventListener);
          try { ws.close(); } catch { /* already gone */ }
        }
      };
      const timer = setTimeout(() => {
        fail();
        reject(new Error('Music Assistant did not answer in time'));
      }, this.connectTimeoutMs);
      const onOpen = () => { removeHandlers(); resolve(); };
      const onFail = () => { fail(); reject(new Error('Could not reach Music Assistant')); };
      ws.addEventListener('open', onOpen);
      ws.addEventListener('error', onFail);
      ws.addEventListener('close', onFail);
    }).finally(() => {
      this.connecting = null;
    });
    return this.connecting;
  }

  /** Send `command` and wait for MA's answer (or its error, or the timeout). */
  call(command: string, args: Record<string, unknown> = {}, opts: { timeoutMs?: number } = {}): Promise<unknown> {
    const ws = this.ws;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      return Promise.reject(new MaRpcError(command, 'Music Assistant is not connected'));
    }
    this.seq += 1;
    const id = `counter${this.seq}`;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new MaRpcError(command, `Music Assistant did not answer ${command}`));
      }, opts.timeoutMs ?? this.timeoutMs);
      this.pending.set(id, { command, resolve, reject, timer });
      try {
        ws.send(JSON.stringify({ message_id: id, command, args }));
      } catch (err) {
        clearTimeout(timer);
        this.pending.delete(id);
        reject(new MaRpcError(command, err instanceof Error ? err.message : String(err)));
      }
    });
  }

  /** Hear MA's pushed events (`player_updated`, `queue_updated`, …). */
  onEvent(listener: MaEventListener): () => void {
    this.eventListeners.add(listener);
    return () => this.eventListeners.delete(listener);
  }

  /** Hear the socket closing without `close()` having been called. */
  onUnexpectedClose(listener: CloseListener): () => void {
    this.closeListeners.add(listener);
    return () => this.closeListeners.delete(listener);
  }

  /** Close on purpose and fail everything still waiting; safe to repeat. */
  close(reason = 'closed'): void {
    this.closedOnPurpose = true;
    const ws = this.ws;
    this.ws = null;
    if (ws) {
      ws.removeEventListener('message', this.handleMessage as EventListener);
      ws.removeEventListener('close', this.handleClose as EventListener);
      try { ws.close(1000, reason); } catch { /* already gone */ }
    }
    this.failAll(`Music Assistant connection closed (${reason})`);
  }

  private failAll(message: string): void {
    const waiting = [...this.pending.values()];
    this.pending.clear();
    for (const p of waiting) {
      clearTimeout(p.timer);
      p.reject(new MaRpcError(p.command, message));
    }
  }

  private readonly handleMessage = (event: MessageEvent) => {
    let data: Record<string, unknown>;
    try {
      data = JSON.parse(String(event.data));
    } catch {
      return; // not JSON
    }
    if (!data || typeof data !== 'object') return;
    const id = data.message_id;
    if (id !== undefined && id !== null) {
      const p = this.pending.get(String(id));
      if (!p) return; // someone else's answer, or one that already timed out
      this.pending.delete(String(id));
      clearTimeout(p.timer);
      if (data.error !== undefined || data.error_code !== undefined) {
        p.reject(new MaRpcError(p.command, `Music Assistant: ${errorDetail(data)}`));
      } else {
        p.resolve(data.result);
      }
      return;
    }
    if (typeof data.event === 'string') {
      const payload = (data.data && typeof data.data === 'object' ? data.data : {}) as Record<string, unknown>;
      const objectId = typeof data.object_id === 'string' && data.object_id ? data.object_id : undefined;
      for (const listener of this.eventListeners) listener(data.event, payload, objectId);
    }
  };

  private readonly handleClose = (event: { code?: number; reason?: string }) => {
    this.ws = null;
    this.failAll(`Music Assistant connection closed (${event?.code ?? 'unknown'})`);
    if (this.closedOnPurpose) return;
    for (const listener of this.closeListeners) listener(event);
  };
}

/**
 * Whether an MA event is about `playerId`. Strict equality on the id the
 * event names (BUG-52): `includes` both ways let "kitchen" match
 * "kitchen_2". MA's envelope `object_id` wins when present; events that name
 * no player apply to everyone.
 */
export function eventIsForPlayer(
  event: string,
  data: Record<string, unknown>,
  playerId: string,
  objectId?: string,
): boolean {
  if (!playerId) return true;
  if (objectId) return objectId === playerId;
  const id = event === 'player_updated'
    ? data.player_id ?? data.id
    : event === 'queue_updated' || event === 'queue_items_updated' || event === 'queue_time_updated'
      ? data.queue_id ?? data.player_id
      : undefined;
  if (id === undefined || id === null || id === '') return true;
  return String(id) === playerId;
}
