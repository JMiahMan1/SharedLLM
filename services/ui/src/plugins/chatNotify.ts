import { registerPlugin } from '@capacitor/core';
import type { PluginListenerHandle } from '@capacitor/core';

/** Android's family chat notifications (a long poll held by a native service). */
export interface ChatNotifyPluginInterface {
  /** Asks for notification permission (Android 13+) and starts listening. */
  start(): Promise<{ notifications: boolean }>;
  stop(): Promise<void>;
  /** The conversation on screen, which then does not notify; null when none. */
  setActiveConversation(options: { token: string | null }): Promise<void>;
  /** A notification was tapped; `path` is the page to open. */
  addListener(event: 'open', cb: (data: { path: string }) => void): Promise<PluginListenerHandle>;
}

const ChatNotify = registerPlugin<ChatNotifyPluginInterface>('ChatNotify');

export default ChatNotify;
