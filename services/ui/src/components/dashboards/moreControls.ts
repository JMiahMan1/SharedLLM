import { createContext } from 'react';
import type { HubEntity } from '../../lib/dashboards';

/**
 * Where a row's name opens the entity's full control (the device control library's sheet). A page
 * that provides it (Rooms) makes every name a button; without it names are plain text.
 */
export const MoreControlsContext = createContext<((entity: HubEntity) => void) | null>(null);
