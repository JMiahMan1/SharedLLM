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
