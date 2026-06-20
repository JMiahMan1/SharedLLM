/* ── BroadcastChannel coordination (multi-tab prevention) ─────────────── */

const CHANNEL_NAME = 'jarvis-web-player';
const BC_MSG = {
  IS_ACTIVE: 'IS_WEBPLAYER_ACTIVE',
  IS_ACTIVE_RESPONSE: 'WEBPLAYER_IS_ACTIVE',
  TAKING_CONTROL: 'TAKING_CONTROL:',
  CONTROL_AVAILABLE: 'CONTROL_AVAILABLE',
  CONTROL_TAKEN: 'CONTROL_TAKEN',
};

const TIMEOUT_DURATION_MS = 75_000;

let unsubCallbacks: (() => void)[] = [];
let activePlayerChecks: Array<{ resolve: (v: boolean) => void; timeout: number }> = [];
let highestPriority: string | undefined;

function genId(): string {
  const arr = new Uint32Array(10);
  crypto.getRandomValues(arr);
  return arr.join('');
}

const bc = new BroadcastChannel(CHANNEL_NAME);

/* ── Tab mode enum ─────────────────────────────────────────────────────── */

export type WebPlayerTabMode = 'disabled' | 'controls_only';

export interface WebPlayerState {
  tabMode: WebPlayerTabMode;
  player_id: string | null;
  lastUpdate: number;
}

const defaultState: WebPlayerState = {
  tabMode: 'disabled',
  player_id: null,
  lastUpdate: 0,
};

/* ── Persistence helpers ───────────────────────────────────────────────── */

const STORAGE_KEY = 'jarvis_web_player_id';

export function getPlayerId(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

export function savePlayerId(id: string): void {
  try {
    localStorage.setItem(STORAGE_KEY, id);
  } catch { /* ignore */ }
}

export function clearPlayerId(): void {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch { /* ignore */ }
}

/* ── State store ───────────────────────────────────────────────────────── */

let state: WebPlayerState = { ...defaultState };

export function getState(): WebPlayerState {
  // Restore player_id from localStorage on every read
  const stored = getPlayerId();
  if (stored && !state.player_id) {
    state.player_id = stored;
  }
  return { ...state };
}

export function setState(patch: Partial<WebPlayerState>): void {
  state = { ...state, ...patch };
  if (patch.player_id) {
    savePlayerId(patch.player_id);
  }
}

/* ── BroadcastChannel handlers ─────────────────────────────────────────── */

bc.onmessage = (event) => {
  if (typeof event.data === 'string' && event.data.startsWith(BC_MSG.TAKING_CONTROL)) {
    const priority = event.data.substring(BC_MSG.TAKING_CONTROL.length);
    if (highestPriority !== undefined) {
      highestPriority = highestPriority > priority ? highestPriority : priority;
    } else {
      highestPriority = priority;
    }
  }

  switch (event.data) {
    case BC_MSG.IS_ACTIVE:
      if (isPlaybackActive()) {
        if (timedOut()) {
          // Tab was throttled — release control
          releaseControl(true);
        } else {
          bc.postMessage(BC_MSG.IS_ACTIVE_RESPONSE);
        }
      }
      break;
    case BC_MSG.IS_ACTIVE_RESPONSE:
      for (const check of activePlayerChecks) {
        clearTimeout(check.timeout);
        check.resolve(true);
      }
      activePlayerChecks = [];
      break;
    case BC_MSG.CONTROL_AVAILABLE:
      // Another tab released — check if we should take over
      break;
    case BC_MSG.CONTROL_TAKEN:
      // Another tab took control — fall back
      if (isPlaybackActive()) {
        releaseControl(true);
      }
      break;
  }
};

/* ── Core helpers ──────────────────────────────────────────────────────── */

function isPlaybackActive(): boolean {
  return state.tabMode !== 'disabled' && state.player_id !== null;
}

function timedOut(): boolean {
  return Date.now() - state.lastUpdate >= TIMEOUT_DURATION_MS;
}

async function isAnotherTabActive(): Promise<boolean> {
  return new Promise((resolve) => {
    const timeout = window.setTimeout(() => {
      activePlayerChecks = activePlayerChecks.filter((c) => c.timeout !== timeout);
      resolve(false);
    }, 500);
    activePlayerChecks.push({ resolve, timeout });
    bc.postMessage(BC_MSG.IS_ACTIVE);
  });
}

function genPriority(): string {
  const interacted = document.querySelector('[data-webplayer-interact]') !== null;
  const visible = !document.hidden;
  const uid = genId();
  return (interacted ? '1' : '0') + (visible ? '1' : '0') + uid;
}

async function canTakeControl(): Promise<boolean> {
  const priority = genPriority();
  if (highestPriority !== undefined) {
    highestPriority = highestPriority > priority ? highestPriority : priority;
  } else {
    highestPriority = priority;
  }
  bc.postMessage(BC_MSG.TAKING_CONTROL + priority);

  return new Promise((resolve) => {
    setTimeout(() => {
      const won = highestPriority === priority;
      highestPriority = undefined;
      resolve(won);
    }, 2000);
  });
}

export function releaseControl(silent: boolean = false): void {
  if (isPlaybackActive()) {
    bc.postMessage(BC_MSG.CONTROL_AVAILABLE);
    if (!silent) {
      setState({ tabMode: 'controls_only' });
    }
  }
}

/* ── Tab visibility / throttling ───────────────────────────────────────── */

export function handleVisibilityChange(): void {
  if (document.hidden && isPlaybackActive()) {
    // Tab hidden — we'll still update lastUpdate via timer so timeout detection works
  }
}

/* ── Cleanup ───────────────────────────────────────────────────────────── */

export function destroy(): void {
  if (isPlaybackActive()) {
    bc.postMessage(BC_MSG.CONTROL_AVAILABLE);
  }
  bc.onmessage = null;
  for (const cb of unsubCallbacks) cb();
  unsubCallbacks = [];
  for (const check of activePlayerChecks) clearTimeout(check.timeout);
  activePlayerChecks = [];
}

/* ── Page hide handler (unmount) ───────────────────────────────────────── */

let _pageHideBound = false;

export function installPageHideListener(): void {
  if (_pageHideBound) return;
  _pageHideBound = true;
  window.addEventListener('pagehide', () => {
    if (isPlaybackActive()) {
      bc.postMessage(BC_MSG.CONTROL_AVAILABLE);
    }
  });
}

/* ── Expose BroadcastChannel for external subscribe (e.g. disconnect handler) ─ */

export function subscribe(fn: (data: unknown) => void): () => void {
  const handler = (event: MessageEvent) => fn(event.data);
  // We can't override bc.onmessage, but we expose bc for direct use
  // This is a simplified approach — for full subscription support the caller
  // should use the provided hooks instead
  unsubCallbacks.push(() => {}); // placeholder
  return () => {};
}

// Expose the channel directly for subscribe/unsubscribe patterns
export { bc };
