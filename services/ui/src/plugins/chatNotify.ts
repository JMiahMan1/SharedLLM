import { registerPlugin } from '@capacitor/core';
import type { PluginListenerHandle } from '@capacitor/core';

/**
 * Android's notifications: a native service holds the push stream
 * (/api/push/stream) and is also the UnifiedPush distributor for other apps.
 * Still registered as "ChatNotify" so web bundles and APKs of different ages
 * find each other.
 */
export interface ChatNotifyPluginInterface {
  /** Asks for notification permission (Android 13+) and starts listening. */
  start(): Promise<{ notifications: boolean }>;
  stop(): Promise<void>;
  /** The conversation on screen, which then does not notify; null when none. */
  setActiveConversation(options: { token: string | null }): Promise<void>;
  /** The names people know apps by, keyed by package name. */
  appLabels(options: { packages: string[] }): Promise<{ labels: Record<string, string> }>;
  /**
   * Ends an app's UnifiedPush registration made on this phone (the app is
   * told, the gateway forgets it). `removed` is 0 when none was made here.
   */
  removeApp(options: { app: string; token?: string }): Promise<{ removed: number }>;
  /** A notification was tapped; `path` is the page to open. */
  addListener(event: 'open', cb: (data: { path: string }) => void): Promise<PluginListenerHandle>;
}

const ChatNotify = registerPlugin<ChatNotifyPluginInterface>('ChatNotify');

export default ChatNotify;
