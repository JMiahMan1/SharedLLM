import { Capacitor, registerPlugin } from '@capacitor/core';

/**
 * Home-screen shortcuts for chat conversations (Android).
 *
 * The native side (JarvisChatShortcut, ChatShortcutPlugin.java) asks the
 * launcher to pin a shortcut with the other person's name and picture, and
 * also publishes it as a long-lived conversation shortcut (long-press on the
 * Jarvis icon). Tapping it opens the app on `path`, the same way a chat
 * notification does; a private chat's path lands on its lock screen.
 *
 * Only on an APK with the plugin. The web build (and an older APK) never
 * offers it.
 */
export interface ChatShortcutRequest {
  /** Stable per conversation (`chat:<token>`), so adding again updates it. */
  id: string;
  /** The other person's name, or the group's. */
  label: string;
  /** The in-app page to open: `/family?chat=<token>`. */
  path: string;
  /** Background for the icon and the monogram (#rrggbb). */
  color: string;
  /** Drawn on `color` when there is no picture. */
  monogram: string;
  /** The picture as base64 PNG (no `data:` prefix); absent means the monogram. */
  icon?: string;
}

export interface ChatShortcutPluginInterface {
  /** Whether this phone's launcher can pin shortcuts. */
  supported(): Promise<{ supported: boolean }>;
  /** `updated`: it was on the home screen already; `requested`: Android is adding it (it may show its own dialog). */
  pin(options: ChatShortcutRequest): Promise<{ requested: boolean; updated: boolean }>;
  /**
   * A chat was opened: keep it for the launcher's widget list ("Jarvis chat")
   * and the long-press list, without pinning. Not on APKs before build 689.
   */
  offer?(options: ChatShortcutRequest): Promise<void>;
  /** The conversation is gone: the shortcut stays on the home screen greyed out with `message`. */
  disable(options: { id: string; message: string }): Promise<void>;
}

const registered = registerPlugin<ChatShortcutPluginInterface>('JarvisChatShortcut');

declare global {
  interface Window {
    /** Hermetic browser tests only (the dev server): stands in for the Android plugin. */
    __jarvisChatShortcutMock?: ChatShortcutPluginInterface;
  }
}

/** The plugin, or null where there is none (the web build, iOS). */
export function chatShortcutPlugin(): ChatShortcutPluginInterface | null {
  if (import.meta.env.DEV && typeof window !== 'undefined' && window.__jarvisChatShortcutMock) {
    return window.__jarvisChatShortcutMock;
  }
  return Capacitor.isNativePlatform() && Capacitor.getPlatform() === 'android' ? registered : null;
}

/** Whether "Add to home screen" can be offered: the plugin is there and the launcher pins. */
export async function chatShortcutSupported(plugin = chatShortcutPlugin()): Promise<boolean> {
  if (!plugin) return false;
  try {
    return Boolean((await plugin.supported()).supported);
  } catch {
    // An APK without the plugin answers "not implemented": not offered.
    return false;
  }
}

export default registered;
