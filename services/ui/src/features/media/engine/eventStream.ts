import { getServerOrigin } from '../../../lib/serverUrl';
import { ensureMediaToken } from '../../../lib/mediaToken';
import type { MediaEvent } from '../store/ingest';

/** Poll this often while the stream is down (§7.2). */
export const FALLBACK_POLL_MS = 10_000;
/** Start polling once the stream has been down this long. */
export const FALLBACK_AFTER_MS = 5_000;
/** Reopen a stream the server closed (EventSource gives up on HTTP errors). */
export const REOPEN_MS = 3_000;

export type StreamState = 'connecting' | 'open' | 'closed';

const EVENT_TYPES = ['snapshot', 'player', 'queue', 'heartbeat'] as const;

export interface MediaEventStreamOptions {
  /** The stream URL, or null when it cannot be built (no token yet). */
  url?: () => Promise<string | null>;
  onEvent: (event: MediaEvent) => void;
  onState?: (state: StreamState) => void;
  /** One status fetch, used while the stream is down. */
  poll: () => Promise<void>;
  EventSourceImpl?: typeof EventSource;
}

/** `/api/media/events` signed with the media token (EventSource cannot send headers). */
export async function mediaEventsUrl(): Promise<string | null> {
  const mt = await ensureMediaToken();
  if (!mt) return null;
  const base = getServerOrigin().replace(/\/+$/, '');
  return `${base}/api/media/events?user=${encodeURIComponent(mt.user)}&mt=${encodeURIComponent(mt.token)}`;
}

/**
 * The server's media event stream (SSE, §7.2): a snapshot of every player on
 * connect, then player and queue changes as they happen, so remote outputs
 * update within a second instead of on the next poll.
 *
 * EventSource reconnects by itself after a dropped connection; when the
 * server refuses it (an expired token) it stops, so this reopens it with a
 * fresh URL. While the stream has been down for 5 s the store is fed by
 * polling every 10 s instead, and polling stops the moment the stream is back.
 */
export class MediaEventStream {
  private source: EventSource | null = null;
  private state: StreamState = 'closed';
  private stopped = true;
  private fallbackTimer: ReturnType<typeof setTimeout> | null = null;
  private pollTimer: ReturnType<typeof setInterval> | null = null;
  private reopenTimer: ReturnType<typeof setTimeout> | null = null;
  private readonly opts: Required<Omit<MediaEventStreamOptions, 'onState' | 'EventSourceImpl'>> & Pick<MediaEventStreamOptions, 'onState' | 'EventSourceImpl'>;

  constructor(opts: MediaEventStreamOptions) {
    this.opts = { url: mediaEventsUrl, ...opts };
  }

  get current(): StreamState {
    return this.state;
  }

  get polling(): boolean {
    return this.pollTimer !== null;
  }

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    void this.open();
  }

  stop(): void {
    this.stopped = true;
    this.closeSource();
    this.clearTimers();
    this.setState('closed');
  }

  private setState(state: StreamState): void {
    if (this.state === state) return;
    this.state = state;
    this.opts.onState?.(state);
  }

  private async open(): Promise<void> {
    this.setState('connecting');
    this.armFallback();
    let url: string | null;
    try {
      url = await this.opts.url();
    } catch {
      url = null;
    }
    if (this.stopped) return;
    const Impl = this.opts.EventSourceImpl ?? (typeof EventSource !== 'undefined' ? EventSource : undefined);
    if (!url || !Impl) {
      this.down();
      this.scheduleReopen();
      return;
    }
    const source = new Impl(url);
    this.source = source;
    source.onopen = () => {
      if (this.source !== source) return;
      this.setState('open');
      this.stopFallback();
    };
    source.onerror = () => {
      if (this.source !== source) return;
      // CONNECTING: the browser is already retrying. CLOSED: it gave up.
      if (source.readyState === Impl.CLOSED) {
        this.closeSource();
        this.scheduleReopen();
      }
      this.down();
    };
    for (const type of EVENT_TYPES) {
      source.addEventListener(type, (message) => {
        if (this.source !== source) return;
        let data: Record<string, unknown>;
        try {
          data = JSON.parse(String((message as MessageEvent).data));
        } catch {
          return;
        }
        this.opts.onEvent({ ...data, type } as MediaEvent);
      });
    }
  }

  /** The stream is not delivering: say so, and poll if it stays that way. */
  private down(): void {
    this.setState('closed');
    this.armFallback();
  }

  private armFallback(): void {
    if (this.fallbackTimer || this.pollTimer || this.stopped) return;
    this.fallbackTimer = setTimeout(() => {
      this.fallbackTimer = null;
      if (this.stopped || this.state === 'open') return;
      void this.opts.poll();
      this.pollTimer = setInterval(() => {
        if (typeof document !== 'undefined' && document.hidden) return;
        void this.opts.poll();
      }, FALLBACK_POLL_MS);
    }, FALLBACK_AFTER_MS);
  }

  private stopFallback(): void {
    if (this.fallbackTimer) clearTimeout(this.fallbackTimer);
    if (this.pollTimer) clearInterval(this.pollTimer);
    this.fallbackTimer = null;
    this.pollTimer = null;
  }

  private scheduleReopen(): void {
    if (this.reopenTimer || this.stopped) return;
    this.reopenTimer = setTimeout(() => {
      this.reopenTimer = null;
      if (!this.stopped) void this.open();
    }, REOPEN_MS);
  }

  private closeSource(): void {
    const source = this.source;
    this.source = null;
    try { source?.close(); } catch { /* already closed */ }
  }

  private clearTimers(): void {
    this.stopFallback();
    if (this.reopenTimer) clearTimeout(this.reopenTimer);
    this.reopenTimer = null;
  }
}
