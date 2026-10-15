import { registerPlugin } from '@capacitor/core';
import type { PluginListenerHandle } from '@capacitor/core';

export type JarvisMediaAction = 'play' | 'pause' | 'next' | 'previous';

/** Android's media-playback service: keeps the web player alive in the
 *  background and puts its controls on the lock screen and in the shade. */
export interface JarvisMediaPluginInterface {
  update(options: { title: string; artist: string; playing: boolean }): Promise<void>;
  stop(): Promise<void>;
  addListener(event: 'action', cb: (data: { action: JarvisMediaAction }) => void): Promise<PluginListenerHandle>;
}

const JarvisMedia = registerPlugin<JarvisMediaPluginInterface>('JarvisMedia');

export default JarvisMedia;
