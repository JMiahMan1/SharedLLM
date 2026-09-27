type MessageListener = (event: { data: unknown }) => void;

const registry = new Map<string, Set<FakeBroadcastChannel>>();

/**
 * Test double for BroadcastChannel. Instances sharing a name deliver
 * postMessage() to each other so multi-tab coordination (e.g. web-player
 * tab lock) can be exercised in a single test.
 */
export class FakeBroadcastChannel {
  readonly name: string;
  onmessage: MessageListener | null = null;

  private listeners: MessageListener[] = [];
  private closed = false;

  constructor(name: string) {
    this.name = name;
    const peers = registry.get(name) ?? new Set<FakeBroadcastChannel>();
    peers.add(this);
    registry.set(name, peers);
  }

  postMessage(data: unknown): void {
    if (this.closed) return;
    for (const peer of registry.get(this.name) ?? []) {
      if (peer === this || peer.closed) continue;
      peer.deliver(data);
    }
  }

  close(): void {
    if (this.closed) return;
    this.closed = true;
    registry.get(this.name)?.delete(this);
  }

  addEventListener(_type: 'message', listener: MessageListener): void {
    this.listeners.push(listener);
  }

  removeEventListener(_type: 'message', listener: MessageListener): void {
    this.listeners = this.listeners.filter((l) => l !== listener);
  }

  private deliver(data: unknown): void {
    this.onmessage?.({ data });
    for (const listener of [...this.listeners]) listener({ data });
  }

  /** Drop all channels between tests. */
  static reset(): void {
    registry.clear();
  }
}
