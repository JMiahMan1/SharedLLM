import { apiClient } from '../services/api';

/**
 * Devices > Light groups: lights grouped so they switch as one, a real light in the hub
 * (services/homehub/light_groups.py, the group helper). Parents and admins make them through
 * the gateway (services/gateway/light_group_routes.py). A group appears like any light: in its
 * room, in schedules and automations, and to voice ("turn off the kitchen lights").
 */

export interface LightGroupMember {
  entity_id: string;
  name: string;
  state: string | null;
  /** The light was deleted from the hub since: skipped until the group is edited. */
  missing: boolean;
  /** A duplicate copy hidden on the Devices page: still switched with the group. */
  hidden: boolean;
  area: string | null;
}

export interface LightGroup {
  entry_id: string;
  entity_id: string | null;
  name: string;
  area_id: string | null;
  area: string | null;
  state: string | null;
  members: LightGroupMember[];
  missing: number;
}

export interface LightChoice {
  entity_id: string;
  name: string;
  area: string | null;
  hidden: boolean;
  available: boolean;
}

export interface LightGroups {
  groups: LightGroup[];
  lights: LightChoice[];
  areas: { area_id: string; name: string }[];
}

export interface LightGroupDraft {
  name: string;
  area_id: string;
  entities: string[];
}

export const LIGHT_GROUPS_KEY = ['homehub-light-groups'];

export async function getLightGroups(): Promise<LightGroups> {
  const resp = await apiClient.get('/api/homehub/light_groups');
  return { groups: resp.data?.groups ?? [], lights: resp.data?.lights ?? [], areas: resp.data?.areas ?? [] };
}

export async function createLightGroup(draft: LightGroupDraft): Promise<LightGroup> {
  const resp = await apiClient.post('/api/homehub/light_groups', bodyOf(draft));
  return resp.data;
}

export async function updateLightGroup(entryId: string, draft: LightGroupDraft): Promise<LightGroup> {
  const resp = await apiClient.patch(`/api/homehub/light_groups/${encodeURIComponent(entryId)}`, bodyOf(draft));
  return resp.data;
}

export async function deleteLightGroup(entryId: string): Promise<void> {
  await apiClient.delete(`/api/homehub/light_groups/${encodeURIComponent(entryId)}`);
}

// -- pure helpers (unit-tested) ------------------------------------------------------

export function bodyOf(draft: LightGroupDraft): { name: string; area_id: string | null; entities: string[] } {
  return { name: draft.name.trim().replace(/\s+/g, ' '), area_id: draft.area_id || null, entities: [...draft.entities] };
}

export function draftOf(group?: LightGroup): LightGroupDraft {
  if (!group) return { name: '', area_id: '', entities: [] };
  return { name: group.name, area_id: group.area_id ?? '', entities: group.members.map((m) => m.entity_id) };
}

/** Why the draft cannot be saved yet, or null when it can. */
export function draftProblem(draft: LightGroupDraft): string | null {
  if (!draft.name.trim()) return 'Give the group a name.';
  if (draft.entities.length < 2) return 'Choose at least two lights.';
  return null;
}

export function toggleLight(draft: LightGroupDraft, entityId: string): LightGroupDraft {
  const has = draft.entities.includes(entityId);
  return { ...draft, entities: has ? draft.entities.filter((e) => e !== entityId) : [...draft.entities, entityId] };
}

/** "3 lights", "3 lights · 1 is gone" for a group card. */
export function memberSummary(group: LightGroup): string {
  const n = group.members.length;
  const parts = [`${n} ${n === 1 ? 'light' : 'lights'}`];
  if (group.missing) parts.push(`${group.missing} ${group.missing === 1 ? 'is' : 'are'} gone`);
  const on = group.members.filter((m) => m.state === 'on').length;
  if (on && on < n) parts.push(`${on} on`);
  return parts.join(' · ');
}

export interface ChoiceRoom { room: string; lights: LightChoice[] }

/**
 * The lights that can join, by room, filtered by what was typed (name or room). A light the
 * group already holds but the hub no longer has is listed first, so it can be taken out.
 */
export function choicesByRoom(data: Pick<LightGroups, 'lights'>, draft: LightGroupDraft, query: string,
  members: LightGroupMember[] = []): ChoiceRoom[] {
  const q = query.trim().toLowerCase();
  const known = new Set(data.lights.map((l) => l.entity_id));
  const gone: LightChoice[] = members.filter((m) => !known.has(m.entity_id) && draft.entities.includes(m.entity_id))
    .map((m) => ({ entity_id: m.entity_id, name: m.name, area: null, hidden: false, available: false }));
  const rooms = new Map<string, LightChoice[]>();
  for (const light of data.lights) {
    const room = light.area || 'No room';
    if (q && !light.name.toLowerCase().includes(q) && !room.toLowerCase().includes(q)) continue;
    rooms.set(room, [...(rooms.get(room) ?? []), light]);
  }
  const out = [...rooms.entries()].map(([room, lights]) => ({ room, lights }));
  out.sort((a, b) => (a.room === 'No room' ? 1 : b.room === 'No room' ? -1 : a.room.localeCompare(b.room)));
  return gone.length ? [{ room: 'No longer in the hub', lights: gone }, ...out] : out;
}
