import type { Page } from '@playwright/test';

/**
 * The web app as the Android app runs it, with the Bluetooth plugins
 * answered by a stand-in (the emulator and a browser have no Bluetooth).
 *
 * Capacitor's custom platform makes getPlatform() say "android" while every
 * other plugin keeps its web implementation; the three Bluetooth plugins get
 * native headers whose calls go to `window.__fakeNative`, which answers from
 * the state given here and records each call in `window.__nativeCalls`.
 * Call it after the page mocks that seed the sign-in.
 */
export interface FakeAndroidState {
  vehicle?: Record<string, unknown>;
  devices?: unknown[];
  proximity?: Record<string, unknown>;
  mesh?: Record<string, unknown>;
  messages?: unknown[];
  /** The chat rooms the phone holds an offline key for (JarvisFamilyMesh.rooms). */
  meshRooms?: unknown[];
  /** Settings > Nearby & offline on the phone (JarvisNearby): the parts and what Android allows. */
  nearby?: Record<string, unknown>;
  /** Offline mode: cached items, queued requests, parent commands, diagnostics. */
  offline?: { cache?: unknown[]; requests?: unknown[]; commands?: unknown[]; diag?: Record<string, unknown>; peerNearby?: boolean };
  /** My devices (JarvisOwnSync): the person's own data as this phone holds it, and the diagnostics. */
  own?: { state?: Record<string, unknown>; diag?: Record<string, unknown> };
}

const PLUGINS: Record<string, string[]> = {
  JarvisVehicleBluetooth: ['getStatus', 'requestPermission', 'listDevices', 'setVehicles', 'getDiagnostics', 'openSettings', 'openBluetoothSettings'],
  JarvisProximity: ['getDiagnostics', 'enable', 'disable'],
  JarvisFamilyMesh: ['getDiagnostics', 'enable', 'disable', 'rooms', 'refreshRooms', 'send', 'list', 'removeListener'],
  JarvisOfflineMode: ['queue', 'requests', 'cache', 'sendCommand', 'commands', 'getDiagnostics'],
  JarvisNearby: ['getState', 'apply', 'requestPermissions'],
  JarvisOwnSync: ['state', 'getDiagnostics', 'noteWatch'],
};

/** A phone with every permission and nothing on yet but car detection. */
export const NEARBY_DEVICE = {
  nearby: false, chat: false, offline: false, car: true, mine: false, parentLink: false,
  scanPermission: true, advertisePermission: true, connectPermission: true, bluetoothOn: true, host: 'none', apiLevel: 37,
};

export async function fakeAndroid(page: Page, state: FakeAndroidState): Promise<void> {
  await page.addInitScript(({ plugins, initial }) => {
    type Bag = Record<string, unknown>;
    type FakeWindow = Window & { __nativeCalls?: unknown[]; CapacitorCustomPlatform?: unknown; Capacitor?: Bag };
    const win = window as FakeWindow;
    const s = JSON.parse(JSON.stringify(initial)) as Bag & { messages?: unknown[] };
    const calls: Array<{ plugin: string; method: string; options: unknown }> = [];
    win.__nativeCalls = calls;
    win.CapacitorCustomPlatform = { name: 'android', plugins: {} };
    // The app keeps its sign-in in Preferences on Android (web: localStorage with this prefix);
    // copy what the page mocks seeded, and point it at this server.
    for (const key of ['jarvis_api_key', 'jarvis_user']) {
      const v = localStorage.getItem(key);
      if (v !== null) localStorage.setItem(`CapacitorStorage.${key}`, v);
    }
    localStorage.setItem('CapacitorStorage.jarvis_server_url', location.origin);
    const answer = (plugin: string, method: string, options: Bag): unknown => {
      calls.push({ plugin, method, options });
      if (plugin === 'JarvisVehicleBluetooth') {
        if (method === 'listDevices') return { ...(s.vehicle as Bag), devices: s.devices ?? [] };
        if (method === 'getDiagnostics') return s.vehicle;
        if (['getStatus', 'requestPermission'].includes(method)) return s.vehicle;
        return {};
      }
      if (plugin === 'JarvisProximity') {
        if (method === 'enable') s.proximity = { ...(s.proximity as Bag), enabled: true };
        if (method === 'disable') s.proximity = { ...(s.proximity as Bag), enabled: false };
        return s.proximity;
      }
      if (plugin === 'JarvisFamilyMesh') {
        if (method === 'enable') s.mesh = { ...(s.mesh as Bag), enabled: true };
        if (method === 'disable') s.mesh = { ...(s.mesh as Bag), enabled: false };
        if (method === 'list') {
          const all = (s.messages ?? []) as Bag[];
          return { messages: options?.room ? all.filter((m) => m.room === options.room) : all };
        }
        if (method === 'rooms') return { rooms: (s as Bag & { meshRooms?: unknown[] }).meshRooms ?? [] };
        if (method === 'refreshRooms') return {};
        if (method === 'send') {
          const m = { id: String(Date.now()).padStart(32, '0'), t: options.kind, from: 'parent', room: options.room,
            sent_at: Date.now() / 1000, mine: true, synced: false, hops: 0, ...(options.fields as Bag) };
          s.messages = [...(s.messages ?? []), m];
          return { id: m.id };
        }
        return s.mesh;
      }
      if (plugin === 'JarvisNearby') {
        const n = { ...((s.nearby ?? {}) as Bag) };
        if (method === 'apply') {
          for (const k of ['nearby', 'chat', 'offline', 'car', 'mine']) if (typeof options[k] === 'boolean') n[k] = options[k];
          const radio = Boolean(n.nearby || n.chat || n.offline || n.mine || n.parentLink);
          n.host = radio ? 'own' : 'none';
          s.nearby = n;
        }
        return n;
      }
      if (plugin === 'JarvisOwnSync') {
        const own = (s.own ?? {}) as Bag & { state?: Bag; diag?: Bag };
        if (method === 'state') return own.state ?? { enabled: false, devices: [] };
        if (method === 'getDiagnostics') return own.diag ?? { enabled: false, turnedOn: false, peers: [] };
        return {};
      }
      if (plugin === 'JarvisOfflineMode') {
        const o = (s.offline ?? {}) as Bag & { requests?: Bag[]; commands?: Bag[] };
        if (method === 'cache') return { items: o.cache ?? [] };
        if (method === 'requests') return { requests: o.requests ?? [], online: false, peerNearby: Boolean(o.peerNearby) };
        if (method === 'commands') return { commands: o.commands ?? [] };
        if (method === 'getDiagnostics') return o.diag ?? {};
        if (method === 'queue') {
          const id = String(Date.now()).padStart(32, '0');
          o.requests = [...(o.requests ?? []), { id, kind: options.kind, body: options.body, issued_at: 1, expires_at: 2,
            status: o.peerNearby ? 'waiting_for_online_phone' : 'no_family_phone_nearby' }];
          s.offline = o;
          return { id };
        }
        if (method === 'sendCommand') {
          const id = String(Date.now()).padStart(32, '1');
          o.commands = [...(o.commands ?? []), { id, child: options.child, action: options.action, arg: options.minutes ?? 0,
            issued_at: Math.floor(Date.now() / 1000), reported: false }];
          s.offline = o;
          return { id };
        }
      }
      return {};
    };
    win.Capacitor = {
      ...win.Capacitor,
      PluginHeaders: Object.entries(plugins).map(([name, methods]) => ({
        name,
        methods: (methods as string[]).map((m) => ({ name: m, rtype: 'promise' })),
      })),
      nativePromise: (plugin: string, method: string, options: Bag) =>
        new Promise((resolve) => setTimeout(() => resolve(answer(plugin, method, options)), 10)),
      nativeCallback: () => 'cb',
    };
  }, { plugins: PLUGINS, initial: { ...state, nearby: { ...NEARBY_DEVICE, ...(state.nearby ?? {}) } } });
}

/**
 * Settings > Nearby & offline on the server for this test (GET and PUT, the
 * caller's own under `me`, others by name); register it after the page mocks
 * so it answers first. A child (`minor`) is read-only to themselves.
 */
export async function mockNearbySettings(
  page: Page,
  mine: Record<string, boolean>,
  opts: { minor?: boolean; others?: Record<string, Record<string, boolean>> } = {},
): Promise<void> {
  const store: Record<string, Record<string, boolean>> = {
    me: { nearby: false, chat: false, offline: false, car: true, mine: false, ...mine },
    ...Object.fromEntries(Object.entries(opts.others ?? {}).map(([u, s]) => [u, { nearby: false, chat: false, offline: false, car: true, mine: false, ...s }])),
  };
  await page.route(/\/api\/users\/[^/]+\/nearby-settings$/, async (route) => {
    const who = decodeURIComponent(new URL(route.request().url()).pathname.split('/')[3]);
    const self = who === 'me';
    const minor = self ? Boolean(opts.minor) : true;
    const saved = (store[who] ??= { nearby: false, chat: false, offline: false, car: true, mine: false });
    if (route.request().method() === 'PUT') {
      if (self && minor) return route.fulfill({ status: 403, json: { detail: 'A parent manages these on a child\'s phone' } });
      Object.assign(saved, (route.request().postDataJSON() as { settings?: Record<string, boolean> }).settings ?? {});
    }
    return route.fulfill({ json: { username: who, settings: { ...saved }, managed_by_parent: minor,
      can_change: self ? !minor : true, always_on: minor ? ['parent_link'] : [] } });
  });
}
