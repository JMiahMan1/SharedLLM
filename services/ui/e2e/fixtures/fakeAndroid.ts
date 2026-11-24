import type { Page } from '@playwright/test';

/**
 * The web app as the Android app runs it, with Wander's Bluetooth plugins
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
}

const PLUGINS: Record<string, string[]> = {
  JarvisVehicleBluetooth: ['getStatus', 'requestPermission', 'listDevices', 'setVehicles', 'getDiagnostics', 'openSettings', 'openBluetoothSettings'],
  JarvisProximity: ['getDiagnostics', 'enable', 'disable'],
  JarvisFamilyMesh: ['getDiagnostics', 'enable', 'disable', 'setRoom', 'send', 'list'],
};

export async function fakeAndroid(page: Page, state: FakeAndroidState): Promise<void> {
  await page.addInitScript(({ plugins, initial }) => {
    const s = JSON.parse(JSON.stringify(initial)) as Record<string, any>;
    const calls: Array<{ plugin: string; method: string; options: unknown }> = [];
    (window as any).__nativeCalls = calls;
    (window as any).CapacitorCustomPlatform = { name: 'android', plugins: {} };
    // The app keeps its sign-in in Preferences on Android (web: localStorage with this prefix);
    // copy what the page mocks seeded, and point it at this server.
    for (const key of ['jarvis_api_key', 'jarvis_user']) {
      const v = localStorage.getItem(key);
      if (v !== null) localStorage.setItem(`CapacitorStorage.${key}`, v);
    }
    localStorage.setItem('CapacitorStorage.jarvis_server_url', location.origin);
    const answer = (plugin: string, method: string, options: any): unknown => {
      calls.push({ plugin, method, options });
      if (plugin === 'JarvisVehicleBluetooth') {
        if (method === 'listDevices') return { ...s.vehicle, devices: s.devices ?? [] };
        if (method === 'getDiagnostics') return s.vehicle;
        if (['getStatus', 'requestPermission'].includes(method)) return s.vehicle;
        return {};
      }
      if (plugin === 'JarvisProximity') {
        if (method === 'enable') s.proximity = { ...s.proximity, enabled: true };
        if (method === 'disable') s.proximity = { ...s.proximity, enabled: false };
        return s.proximity;
      }
      if (plugin === 'JarvisFamilyMesh') {
        if (method === 'enable') s.mesh = { ...s.mesh, enabled: true };
        if (method === 'disable') s.mesh = { ...s.mesh, enabled: false };
        if (method === 'list') return { messages: s.messages ?? [] };
        if (method === 'send') {
          const m = { id: String(Date.now()).padStart(32, '0'), t: options.kind, from: 'parent', room: 'abcd1234',
            sent_at: Date.now() / 1000, mine: true, synced: false, hops: 0, ...options.fields };
          s.messages = [...(s.messages ?? []), m];
          return { id: m.id };
        }
        if (method === 'setRoom') return {};
        return s.mesh;
      }
      return {};
    };
    (window as any).Capacitor = {
      ...(window as any).Capacitor,
      PluginHeaders: Object.entries(plugins).map(([name, methods]) => ({
        name,
        methods: (methods as string[]).map((m) => ({ name: m, rtype: 'promise' })),
      })),
      nativePromise: (plugin: string, method: string, options: unknown) =>
        new Promise((resolve) => setTimeout(() => resolve(answer(plugin, method, options)), 10)),
      nativeCallback: () => 'cb',
    };
  }, { plugins: PLUGINS, initial: state });
}
