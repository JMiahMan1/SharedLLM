/**
 * Which browser tab plays this device's audio (BUG-66).
 *
 * Every tab of the app registers the same Music Assistant player (the
 * Sendspin id is kept in localStorage), so two tabs streaming at once would
 * echo each other. The tab that starts playing claims the lock over the
 * `jarvis-media` BroadcastChannel; the tab that held it hears the claim and
 * lets go of its audio (`onLost`), then offers "Take over". The owner
 * heartbeats every 2 s; a claim nobody has renewed for 6 s has expired (the
 * tab closed or crashed).
 */

export const CHANNEL_NAME = 'jarvis-media';
export const HEARTBEAT_MS = 2000;
export const LOCK_TTL_MS = 6000;

type LockMessage =
  | { type: 'claim'; tab: string; ts: number }
  | { type: 'heartbeat'; tab: string }
  | { type: 'release'; tab: string };

interface ChannelLike {
  postMessage(data: unknown): void;
  close(): void;
  addEventListener(type: 'message', listener: (event: { data: unknown }) => void): void;
}

export type LockOwner = 'none' | 'this' | 'other';

export class TabLock {
  readonly tabId: string;
  private channel: ChannelLike | null;
  private owner: LockOwner = 'none';
  private otherSeenAt = 0;
  /** When this tab last claimed; breaks a same-tick tie between two claims. */
  private claimedAt = 0;
  private heartbeat: ReturnType<typeof setInterval> | null = null;
  /** Drops a stale other-tab owner once its heartbeats stop (BUG-66). */
  private expiryTimer: ReturnType<typeof setTimeout> | null = null;
  private readonly lostListeners = new Set<() => void>();
  private readonly changeListeners = new Set<(owner: LockOwner) => void>();

  constructor(opts: { channel?: ChannelLike | null; tabId?: string } = {}) {
    this.tabId = opts.tabId ?? Math.random().toString(36).slice(2);
    this.channel = opts.channel !== undefined
      ? opts.channel
      : typeof BroadcastChannel !== 'undefined' ? new BroadcastChannel(CHANNEL_NAME) : null;
    this.channel?.addEventListener('message', (event) => this.receive(event.data));
  }

  /** Who holds the lock now; an unrenewed claim from another tab has expired. */
  get current(): LockOwner {
    if (this.owner === 'other' && Date.now() - this.otherSeenAt > LOCK_TTL_MS) this.setOwner('none');
    return this.owner;
  }

  /** This tab starts playing: tell the others to let go. */
  claim(): void {
    if (this.owner === 'this') return;
    this.clearExpiry();
    this.claimedAt = Date.now();
    this.setOwner('this');
    this.post({ type: 'claim', tab: this.tabId, ts: this.claimedAt });
    this.heartbeat = setInterval(() => this.post({ type: 'heartbeat', tab: this.tabId }), HEARTBEAT_MS);
  }

  /** This tab stopped playing. */
  release(): void {
    if (this.owner !== 'this') return;
    this.stopHeartbeat();
    this.setOwner('none');
    this.post({ type: 'release', tab: this.tabId });
  }

  /** Another tab took the audio: stop this tab's. */
  onLost(listener: () => void): () => void {
    this.lostListeners.add(listener);
    return () => this.lostListeners.delete(listener);
  }

  onChange(listener: (owner: LockOwner) => void): () => void {
    this.changeListeners.add(listener);
    return () => this.changeListeners.delete(listener);
  }

  dispose(): void {
    this.release();
    this.stopHeartbeat();
    this.clearExpiry();
    this.channel?.close();
    this.channel = null;
  }

  private receive(data: unknown): void {
    const msg = data as LockMessage | null;
    if (!msg || typeof msg !== 'object' || msg.tab === this.tabId) return;
    if (msg.type === 'claim') {
      // Two tabs claiming in the same tick: the newer claim wins; the tab id
      // breaks an exact tie, so exactly one tab ends up owning the audio.
      if (this.owner === 'this') {
        const theirs = typeof msg.ts === 'number' ? msg.ts : 0;
        if (theirs < this.claimedAt || (theirs === this.claimedAt && msg.tab < this.tabId)) return;
      }
      const hadIt = this.owner === 'this';
      this.stopHeartbeat();
      this.otherSeenAt = Date.now();
      this.setOwner('other');
      this.armExpiry();
      if (hadIt) for (const listener of this.lostListeners) listener();
    } else if (msg.type === 'heartbeat') {
      if (this.owner === 'this') return; // two owners only for an instant; the newer claim wins
      this.otherSeenAt = Date.now();
      this.setOwner('other');
      this.armExpiry();
    } else if (msg.type === 'release' && this.owner === 'other') {
      this.clearExpiry();
      this.setOwner('none');
    }
  }

  private post(msg: LockMessage): void {
    try {
      this.channel?.postMessage(msg);
    } catch { /* channel closed */ }
  }

  private setOwner(owner: LockOwner): void {
    if (this.owner === owner) return;
    this.owner = owner;
    for (const listener of this.changeListeners) listener(owner);
  }

  private stopHeartbeat(): void {
    if (this.heartbeat) clearInterval(this.heartbeat);
    this.heartbeat = null;
  }

  /** (Re)arm the timer that drops an owner whose heartbeats have stopped. */
  private armExpiry(): void {
    this.clearExpiry();
    this.expiryTimer = setTimeout(() => {
      this.expiryTimer = null;
      if (this.owner === 'other' && Date.now() - this.otherSeenAt >= LOCK_TTL_MS) this.setOwner('none');
    }, LOCK_TTL_MS);
  }

  private clearExpiry(): void {
    if (this.expiryTimer) clearTimeout(this.expiryTimer);
    this.expiryTimer = null;
  }
}
