import { Capacitor, registerPlugin } from '@capacitor/core';
import type { PluginListenerHandle } from '@capacitor/core';

/**
 * The Android side of Family Drop In (DropInCall.java). With auto drop-in on,
 * a ring that arrives while the app is in the background or the phone is
 * locked is answered by the phone itself as a self-managed call, which is what
 * lets the microphone work with the screen off. Native then tells the web app
 * to `join` the room; Hang up on the call notification sends `hangup`; the web
 * app reports `ended` so the native call and its notification end with it.
 */
interface DropInPlugin {
  ended(): Promise<void>;
  /** Newer APKs: the app is in the call, so the phone's safety net (ring after 15 s) stands down. */
  joined(options: { room: string }): Promise<void>;
  /** ``overOtherApps`` only from APKs that also ding by themselves. */
  status(): Promise<{ active: boolean; overOtherApps?: boolean }>;
  openOverlaySettings(): Promise<void>;
  addListener(event: 'join', listener: (data: { room: string; name: string }) => void): Promise<PluginListenerHandle>;
  addListener(event: 'hangup', listener: () => void): Promise<PluginListenerHandle>;
}

let plugin: DropInPlugin | null = null;

function dropIn(): DropInPlugin | null {
  if (Capacitor.getPlatform() !== 'android') return null;
  plugin ??= registerPlugin<DropInPlugin>('DropIn');
  return plugin;
}

/** Listen for native drop-in requests; returns a function that stops listening. */
export function listenForDropIn(
  onJoin: (room: string, name: string) => void,
  onHangUp: () => void,
): () => void {
  const native = dropIn();
  if (!native) return () => {};
  const handles: Promise<PluginListenerHandle>[] = [];
  try {
    handles.push(native.addListener('join', (data) => {
      if (data?.room) onJoin(data.room, data.name || 'Family call');
    }));
    handles.push(native.addListener('hangup', onHangUp));
  } catch {
    // An older APK without the plugin: drop-in still works while the app is open.
  }
  return () => {
    for (const handle of handles) void handle.then((h) => h.remove()).catch(() => {});
  };
}

/** The web app's call is over: end the native drop-in call, if there is one. */
export function reportDropInEnded(): void {
  const native = dropIn();
  if (!native) return;
  void native.ended().catch(() => {
    // An older APK without the plugin has no native call to end.
  });
}

/** The app joined a call: tell the phone, so a Drop In it answered is not rung again after 15 s. */
export function reportDropInJoined(room: string): void {
  const native = dropIn();
  if (!native) return;
  void native.joined({ room }).catch(() => {
    // An older APK has no safety net to stand down.
  });
}

/**
 * The phone's own Drop In state: whether it may open a call over other apps
 * (Android's "Display over other apps"), and whether this APK is new enough to
 * ding by itself. Null off Android, or from an APK too old to say.
 */
export async function nativeDropInStatus(): Promise<{ overOtherApps: boolean } | null> {
  const native = dropIn();
  if (!native) return null;
  try {
    const s = await native.status();
    return typeof s?.overOtherApps === 'boolean' ? { overOtherApps: s.overOtherApps } : null;
  } catch {
    return null;
  }
}

/** Open Android's "Display over other apps" page for Jarvis. */
export async function openOverlaySettings(): Promise<boolean> {
  const native = dropIn();
  if (!native) return false;
  try {
    await native.openOverlaySettings();
    return true;
  } catch {
    return false;
  }
}
