import { apiClient } from '../services/api';

/**
 * Who can watch a camera (admins). Reading asks the gateway, which works out
 * for every family member whether they can watch it now. Changing it uses the
 * device lock every device has (Identity's entity protection), so cameras
 * follow the same rules as everything else:
 *
 * - not limited: each person's device access decides (their role, or the
 *   devices assigned to them);
 * - limited: only the people on the camera's list, plus admins and roles that
 *   may use locked devices ("always").
 */

export interface CameraViewer {
  username: string;
  name: string;
  is_admin: boolean;
  /** Admins and roles that may use locked devices: they can watch whatever the list says. */
  always: boolean;
  can_watch: boolean;
}

export interface CameraViewers {
  entity_id: string;
  /** Limited to a list (a device lock), rather than each person's device access. */
  limited: boolean;
  permitted: string[];
  people: CameraViewer[];
}

export async function getCameraViewers(entityId: string): Promise<CameraViewers> {
  return (await apiClient.get<CameraViewers>(`/api/admin/cameras/${encodeURIComponent(entityId)}/viewers`)).data;
}

/**
 * The list to lock the camera to when one person's switch changes: everyone who
 * can watch now (not counting the "always" people, who need no place on it),
 * with that one person added or taken off.
 */
export function permitListAfter(people: CameraViewer[], username: string, watch: boolean): string[] {
  const names = new Set(people.filter((p) => !p.always && p.can_watch).map((p) => p.username));
  if (watch) names.add(username); else names.delete(username);
  return [...names].sort();
}

/** One line on how this camera's viewers are decided. */
export function viewersSummary(v: CameraViewers): string {
  const watching = v.people.filter((p) => p.can_watch).length;
  const of = `${watching} of ${v.people.length} people can watch.`;
  return v.limited
    ? `${of} Only the people switched on (and admins) can watch this camera.`
    : `${of} Following each person’s device access; changing a switch limits this camera to the people switched on.`;
}
