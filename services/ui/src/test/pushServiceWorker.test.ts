import { describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

/**
 * public/push-sw.js: a browser notice shows its buttons, and a tapped button answers
 * POST /api/push/action as the person, the same call the bell makes. The worker runs here against
 * stand-ins for the browser's service worker globals.
 */

const SOURCE = readFileSync(resolve(__dirname, '../../public/push-sw.js'), 'utf8');

type Listener = (event: Record<string, unknown>) => void;

function worker({ maxActions = 2, fetchAnswer }: {
  maxActions?: number;
  fetchAnswer?: (url: string, init: RequestInit) => Promise<Response>;
} = {}) {
  const listeners: Record<string, Listener> = {};
  const shown: { title: string; options: Record<string, unknown> }[] = [];
  const store = new Map<string, string>();
  const opened: string[] = [];
  const navigated: string[] = [];
  const client = {
    focus: vi.fn(async () => client),
    navigate: vi.fn(async (url: string) => { navigated.push(url); return client; }),
    postMessage: vi.fn(),
  };
  let clients: unknown[] = [];
  const self = {
    addEventListener: (type: string, fn: Listener) => { listeners[type] = fn; },
    registration: { showNotification: vi.fn(async (title: string, options: Record<string, unknown>) => { shown.push({ title, options }); }) },
    clients: {
      matchAll: vi.fn(async () => clients),
      openWindow: vi.fn(async (url: string) => { opened.push(url); }),
    },
    Notification: { maxActions },
  };
  const caches = {
    open: async () => ({
      match: async (k: string) => (store.has(k) ? new Response(store.get(k)) : undefined),
      put: async (k: string, r: Response) => { store.set(k, await r.text()); },
      delete: async (k: string) => store.delete(k),
    }),
  };
  const fetchMock = vi.fn(fetchAnswer ?? (async () => new Response(JSON.stringify({ status: 'SUCCESS' }), { status: 200 })));
  new Function('self', 'caches', 'fetch', SOURCE)(self, caches, fetchMock);

  async function fire(type: string, event: Record<string, unknown>) {
    const pending: Promise<unknown>[] = [];
    listeners[type]({ ...event, waitUntil: (p: Promise<unknown>) => pending.push(p) });
    await Promise.all(pending);
  }
  return {
    shown, fetchMock, opened, navigated, client, store,
    withWindow: () => { clients = [client]; },
    push: (payload: unknown) => fire('push', { data: { json: () => payload, text: () => JSON.stringify(payload) } }),
    click: (data: unknown, action = '', reply?: string) =>
      fire('notificationclick', { action, reply, notification: { data, close: vi.fn() } }),
    giveKey: (key: string | null) => fire('message', { data: { type: 'jarvis-push-key', key } }),
  };
}

const SNOOZE = { label: 'Snooze 5 min', type: 'callback', callback: 'timer_snooze', params: { title: 'Tea' } };
const STOP = { label: 'Stop', type: 'callback', callback: 'timer_stop', params: { id: 't1' } };
const OPEN = { label: 'Open', type: 'view', url: '/family?tab=announce' };
const REPLY = { label: 'Reply', type: 'reply', callback: 'chat_reply', params: { token: 'abc' } };

describe('push service worker', () => {
  it('shows a notice with its buttons, as many as the browser can', async () => {
    const sw = worker();
    await sw.push({ title: 'Timer: Tea', body: 'Tea is done', tag: 'timer:t1', data: { url: '/family' },
      actions: [STOP, SNOOZE, OPEN] });
    const { title, options } = sw.shown[0];
    expect(title).toBe('Timer: Tea');
    expect(options.tag).toBe('timer:t1');
    expect(options.actions).toEqual([{ action: 'a0', title: 'Stop' }, { action: 'a1', title: 'Snooze 5 min' }]);
    expect((options.data as Record<string, unknown>).jarvis_actions).toEqual([STOP, SNOOZE, OPEN]);
  });

  it('asks for text for a reply button and leaves a plain notice without buttons', async () => {
    const sw = worker({ maxActions: 3 });
    await sw.push({ title: 'Alex', body: 'On my way', actions: [REPLY] });
    await sw.push({ title: 'Report', body: 'Ready', data: { report_id: 'r1' } });
    expect(sw.shown[0].options.actions).toEqual([{ action: 'a0', title: 'Reply', type: 'text', placeholder: 'Reply' }]);
    expect(sw.shown[1].options.actions).toBeUndefined();
    expect(sw.shown[1].options.tag).toBe('r1');
  });

  it('answers a callback button with the same call the bell makes, as the signed-in person', async () => {
    const sw = worker();
    await sw.giveKey('person-key');
    await sw.click({ jarvis_actions: [STOP, SNOOZE] }, 'a1');
    expect(sw.fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = sw.fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/push/action');
    expect(init.method).toBe('POST');
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer person-key');
    expect(JSON.parse(String(init.body))).toEqual({ callback: 'timer_snooze', params: { title: 'Tea' } });
    expect(sw.shown).toEqual([]);  // done: nothing more to say
    expect(sw.opened).toEqual([]);
  });

  it('sends a typed reply', async () => {
    const sw = worker();
    await sw.giveKey('person-key');
    await sw.click({ jarvis_actions: [REPLY] }, 'a0', 'Be right there');
    const init = sw.fetchMock.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(String(init.body))).toEqual({ callback: 'chat_reply', params: { token: 'abc' }, reply: 'Be right there' });
  });

  it('opens the app for a reply the browser could not ask text for, and for a view button', async () => {
    const sw = worker();
    await sw.giveKey('person-key');
    await sw.click({ url: '/family?chat=abc', jarvis_actions: [REPLY] }, 'a0');
    expect(sw.fetchMock).not.toHaveBeenCalled();
    expect(sw.opened).toEqual(['/family?chat=abc']);
    sw.withWindow();
    await sw.click({ jarvis_actions: [OPEN] }, 'a0');
    expect(sw.navigated).toEqual(['/family?tab=announce']);
  });

  it('says so when no one has signed in here, and forgets a key the gateway refuses', async () => {
    const sw = worker({ fetchAnswer: async () => new Response(JSON.stringify({ detail: 'Authentication required' }), { status: 401 }) });
    await sw.click({ jarvis_actions: [SNOOZE] }, 'a0');
    expect(sw.fetchMock).not.toHaveBeenCalled();
    expect(sw.shown[0].title).toBe('Snooze 5 min did not run');
    await sw.giveKey('old-key');
    await sw.click({ jarvis_actions: [SNOOZE] }, 'a0');
    expect(sw.shown[1].options.body).toMatch(/Sign in to Jarvis/);
    expect(sw.store.size).toBe(0);
  });

  it('shows the gateway\'s own words when a button fails', async () => {
    const sw = worker({ fetchAnswer: async () => new Response(JSON.stringify({ status: 'FAILURE', message: 'No matching timer found.' }), { status: 200 }) });
    await sw.giveKey('person-key');
    await sw.click({ jarvis_actions: [STOP] }, 'a0');
    expect(sw.shown[0]).toMatchObject({ title: 'Stop did not run', options: { body: 'No matching timer found.' } });
  });

  it('a sign-out clears the key', async () => {
    const sw = worker();
    await sw.giveKey('person-key');
    expect(sw.store.size).toBe(1);
    await sw.giveKey(null);
    expect(sw.store.size).toBe(0);
  });
});
