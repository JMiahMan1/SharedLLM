import { registerPlugin } from '@capacitor/core';
import type { PluginListenerHandle } from '@capacitor/core';

export type JarvisMediaAction = 'play' | 'pause' | 'next' | 'previous';

/** Android's media-playback service: keeps the web player alive in the
 *  background and puts its controls on the lock screen and in the shade.
 *  When the app is swiped away mid-song it carries on as the same Music
 *  Assistant player natively, until `reclaim` hands playback back. */
export interface JarvisMediaPluginInterface {
  update(options: {
    title: string;
    artist: string;
    playing: boolean;
    /** The web player's Sendspin id and name, so a hand-off is the same player. */
    playerId?: string;
    playerName?: string;
    volume?: number;
  }): Promise<void>;
  /** Ends the native player if it is running; `playing` says whether to carry on here. */
  reclaim(): Promise<{ playing: boolean }>;
  stop(): Promise<void>;
  addListener(event: 'action', cb: (data: { action: JarvisMediaAction }) => void): Promise<PluginListenerHandle>;
}

const JarvisMedia = registerPlugin<JarvisMediaPluginInterface>('JarvisMedia');

export default JarvisMedia;
