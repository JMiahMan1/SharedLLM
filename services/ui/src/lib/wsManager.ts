import { useEffect, useRef, useState, useCallback } from 'react';

export type ConnectionState = 'CONNECTING' | 'CONNECTED' | 'RECONNECTING' | 'DISCONNECTED' | 'FAILED';

export interface WebSocketManagerOptions {
  onMessage?: (e: MessageEvent) => void;
  onStateChange?: (s: ConnectionState) => void;
  onError?: (e: Event) => void;
  maxReconnectAttempts?: number;
  heartbeatIntervalMs?: number;
  heartbeatTimeoutMs?: number;
}

export class WebSocketManager {
  private url: string;
  private options: WebSocketManagerOptions;
  private ws: WebSocket | null = null;
  private state: ConnectionState = 'DISCONNECTED';
  private reconnectAttempts = 0;
  private maxReconnectAttempts: number;
  private heartbeatIntervalMs: number;
  private heartbeatTimeoutMs: number;
  
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private pongTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  
  private messageQueue: (string | ArrayBuffer)[] = [];
  private isClosedIntentionally = false;
  private listeners: Record<string, Set<(event: Event | MessageEvent | CloseEvent) => void>> = {};

  constructor(url: string, options: WebSocketManagerOptions = {}) {
    this.url = url;
    this.options = options;
    this.maxReconnectAttempts = options.maxReconnectAttempts ?? 10;
    this.heartbeatIntervalMs = options.heartbeatIntervalMs ?? 30000;
    this.heartbeatTimeoutMs = options.heartbeatTimeoutMs ?? 10000;
    
    this.connect();
  }

  private setState(newState: ConnectionState) {
    if (this.state === newState) return;
    this.state = newState;
    if (this.options.onStateChange) {
      this.options.onStateChange(newState);
    }
  }

  public getState(): ConnectionState {
    return this.state;
  }

  public get readyState(): number {
    if (this.state === 'CONNECTED') return WebSocket.OPEN;
    if (this.state === 'CONNECTING' || this.state === 'RECONNECTING') return WebSocket.CONNECTING;
    if (this.state === 'DISCONNECTED') return WebSocket.CLOSED;
    return WebSocket.CLOSED;
  }

  public addEventListener(type: string, listener: (event: Event | MessageEvent | CloseEvent) => void) {
    if (!this.listeners[type]) {
      this.listeners[type] = new Set();
    }
    this.listeners[type].add(listener);
  }

  public removeEventListener(type: string, listener: (event: Event | MessageEvent | CloseEvent) => void) {
    if (this.listeners[type]) {
      this.listeners[type].delete(listener);
    }
  }

  private dispatchEvent(type: string, event: Event | MessageEvent | CloseEvent) {
    if (this.listeners[type]) {
      for (const listener of this.listeners[type]) {
        try {
          listener(event);
        } catch (e) {
          console.error(`Error in listener for event type "${type}":`, e);
        }
      }
    }
  }

  private connect() {
    if (this.isClosedIntentionally) return;
    
    this.cleanupTimers();
    
    if (this.reconnectAttempts > 0) {
      this.setState('RECONNECTING');
    } else {
      this.setState('CONNECTING');
    }

    try {
      this.ws = new WebSocket(this.url);
      
      this.ws.onopen = (e) => {
        this.reconnectAttempts = 0;
        this.setState('CONNECTED');
        this.startHeartbeat();
        this.flushQueue();
        this.dispatchEvent('open', e);
      };

      this.ws.onmessage = (event) => {
        this.resetHeartbeatTimeout();
        if (this.options.onMessage) {
          this.options.onMessage(event);
        }
        this.dispatchEvent('message', event);
      };

      this.ws.onerror = (event) => {
        if (this.options.onError) {
          this.options.onError(event);
        }
        this.dispatchEvent('error', event);
      };

      this.ws.onclose = (event) => {
        this.cleanupHeartbeat();
        this.dispatchEvent('close', event);
        
        if (this.isClosedIntentionally) {
          this.setState('DISCONNECTED');
          return;
        }

        this.triggerReconnect();
      };
    } catch (err) {
      if (this.options.onError && err instanceof Event) {
        this.options.onError(err);
      }
      this.triggerReconnect();
    }
  }

  private triggerReconnect() {
    if (this.isClosedIntentionally) return;

    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      this.setState('FAILED');
      return;
    }

    // Exponential backoff with jitter: 1s -> 2s -> 4s -> 8s -> ..., max 30s
    const backoff = Math.min(30000, Math.pow(2, this.reconnectAttempts) * 1000);
    const jitter = Math.random() * 1000;
    const delay = backoff + jitter;
    
    this.reconnectAttempts++;
    this.setState('RECONNECTING');

    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = setTimeout(() => {
      this.connect();
    }, delay);
  }

  private startHeartbeat() {
    this.cleanupHeartbeat();
    this.pingTimer = setInterval(() => {
      this.sendPing();
    }, this.heartbeatIntervalMs);
  }

  private sendPing() {
    if (this.state !== 'CONNECTED' || !this.ws) return;
    
    try {
      this.ws.send(JSON.stringify({ type: 'ping' }));
    } catch {
      this.triggerReconnect();
      return;
    }

    this.pongTimer = setTimeout(() => {
      if (this.ws) {
        this.ws.close();
      }
    }, this.heartbeatTimeoutMs);
  }

  private resetHeartbeatTimeout() {
    if (this.pongTimer) {
      clearTimeout(this.pongTimer);
      this.pongTimer = null;
    }
  }

  private cleanupHeartbeat() {
    if (this.pingTimer) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
    this.resetHeartbeatTimeout();
  }

  private cleanupTimers() {
    this.cleanupHeartbeat();
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  private flushQueue() {
    if (this.state !== 'CONNECTED' || !this.ws) return;
    while (this.messageQueue.length > 0) {
      const msg = this.messageQueue.shift();
      if (msg !== undefined) {
        this.ws.send(msg);
      }
    }
  }

  public send(data: string | ArrayBuffer) {
    if (this.state === 'CONNECTED' && this.ws) {
      this.ws.send(data);
    } else {
      this.messageQueue.push(data);
    }
  }

  public close() {
    this.isClosedIntentionally = true;
    this.cleanupTimers();
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    this.setState('DISCONNECTED');
  }
}

export function useWebSocket(
  url: string | null,
  options?: {
    onMessage?: (e: MessageEvent) => void;
    onStateChange?: (s: ConnectionState) => void;
    onError?: (e: Event) => void;
  }
) {
  const [state, setState] = useState<ConnectionState>('DISCONNECTED');
  const [lastMessage, setLastMessage] = useState<MessageEvent | null>(null);
  const managerRef = useRef<WebSocketManager | null>(null);

  const optionsRef = useRef(options);
  
  // Safe ref assignment inside useEffect
  useEffect(() => {
    optionsRef.current = options;
  });

  useEffect(() => {
    if (!url) {
      return;
    }

    const manager = new WebSocketManager(url, {
      onMessage: (e) => {
        setLastMessage(e);
        if (optionsRef.current?.onMessage) {
          optionsRef.current.onMessage(e);
        }
      },
      onStateChange: (s) => {
        setState(s);
        if (optionsRef.current?.onStateChange) {
          optionsRef.current.onStateChange(s);
        }
      },
      onError: (e) => {
        if (optionsRef.current?.onError) {
          optionsRef.current.onError(e);
        }
      },
    });

    managerRef.current = manager;
    setState(manager.getState());

    return () => {
      manager.close();
      managerRef.current = null;
    };
  }, [url]);

  const send = useCallback((data: string | ArrayBuffer) => {
    if (managerRef.current) {
      managerRef.current.send(data);
    }
  }, []);

  const close = useCallback(() => {
    if (managerRef.current) {
      managerRef.current.close();
    }
  }, []);

  return {
    state,
    lastMessage,
    send,
    close,
  };
}
