import { createContext, useContext } from 'react';
import type { useMAWebPlayer } from '../lib/maWebPlayer';

/**
 * The context object alone, apart from MediaPlayerProvider: reading the app's
 * web player must not import the player engine (and its streaming library)
 * into every widget that only wants to send it a command.
 */
export type SharedPlayer = ReturnType<typeof useMAWebPlayer>;

export const MediaPlayerContext = createContext<SharedPlayer | null>(null);

/** The app's web player, or null outside a MediaPlayerProvider (widgets that
 *  also render in isolation, e.g. in tests). */
export function useOptionalWebPlayer(): SharedPlayer | null {
  return useContext(MediaPlayerContext);
}
