type MessageListener = (event: { data: string }) => void;

/**
 * Test double for WebSocket. Auto-opens on the next microtask so code that
 * assigns handlers right after `new WebSocket(url)` still receives them.
 * Tests drive the connection with emitServerMessage()/emitServerClose().
 */
export class FakeWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;

  readonly url: string;
  readyState = FakeWebSocket.CONNECTING;

  onopen: (() => void) | null = null;
  onmessage: MessageListener | null = null;
  onerror: ((event: unknown) => void) | null = null;
  onclose: ((event: { code: number; reason: string }) => void) | null = null;

  readonly sentMessages: string[] = [];

  private listeners: Record<string, Array<(event: never) => void>> = {};

  constructor(url: string) {
    this.url = url;
    queueMicrotask(() => this.open());
  }

  open(): void {
    if (this.readyState !== FakeWebSocket.CONNECTING) return;
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
    this.dispatchEvent('open', {} as never);
  }

  send(data: string): void {
    this.sentMessages.push(data);
  }

  close(code = 1000, reason = ''): void {
    if (this.readyState === FakeWebSocket.CLOSED) return;
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.({ code, reason });
    this.dispatchEvent('close', { code, reason } as never);
  }

  addEventListener(type: string, listener: (event: never) => void): void {
    (this.listeners[type] ??= []).push(listener);
  }

  removeEventListener(type: string, listener: (event: never) => void): void {
    this.listeners[type] = (this.listeners[type] ?? []).filter((l) => l !== listener);
  }

  /** Simulate a message pushed from the server. */
  emitServerMessage(data: string): void {
    this.onmessage?.({ data });
    this.dispatchEvent('message', { data } as never);
  }

  /** Simulate the server dropping the connection. */
  emitServerClose(code = 1000, reason = ''): void {
    this.close(code, reason);
  }

  /** Simulate a network-level error. */
  emitError(event: unknown = { type: 'error' }): void {
    this.onerror?.(event);
    this.dispatchEvent('error', event as never);
  }

  private dispatchEvent(type: string, event: never): void {
    for (const listener of this.listeners[type] ?? []) listener(event);
  }
}
