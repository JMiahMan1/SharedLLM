import { isUnavailable, type HubEntity, type RoomsResponse } from './dashboards';

/**
 * The pure part of the home overview at the top of Rooms: which rooms have
 * lights on, and how many cameras there are. Everything comes from the rooms
 * the hub returns for this person; nothing here knows a household's rooms.
 */

export interface RoomLights {
  area_id: string;
  name: string;
  on: HubEntity[];
  total: number;
}

/** Rooms with at least one light on, busiest first; "Other" collects lights in no room. */
export function lightsOnByRoom(rooms: RoomsResponse | undefined): RoomLights[] {
  if (!rooms) return [];
  const groups: RoomLights[] = rooms.areas.map((area) => {
    const lights = area.entities.filter((e) => e.domain === 'light' && !isUnavailable(e.state));
    return { area_id: area.area_id, name: area.name, on: lights.filter((e) => e.state === 'on'), total: lights.length };
  });
  const loose = rooms.unassigned.filter((e) => e.domain === 'light' && !isUnavailable(e.state));
  if (loose.length) groups.push({ area_id: '', name: 'Other', on: loose.filter((e) => e.state === 'on'), total: loose.length });
  return groups
    .filter((g) => g.on.length > 0)
    .sort((a, b) => b.on.length - a.on.length || a.name.localeCompare(b.name));
}

/** Every camera this person can see, in rooms or not. */
export function camerasIn(rooms: RoomsResponse | undefined): HubEntity[] {
  if (!rooms) return [];
  return [...rooms.areas.flatMap((a) => a.entities), ...rooms.unassigned].filter((e) => e.domain === 'camera');
}

/** "3 lights on in 2 rooms", "No lights on". */
export function lightsSummary(groups: RoomLights[]): string {
  const lights = groups.reduce((n, g) => n + g.on.length, 0);
  if (!lights) return 'No lights on';
  const rooms = groups.length;
  return `${lights} light${lights === 1 ? '' : 's'} on in ${rooms} room${rooms === 1 ? '' : 's'}`;
}

/** The DOM id a room card carries, so the overview can jump to it. */
export const roomAnchor = (areaId: string) => `room-${areaId || 'other'}`;
