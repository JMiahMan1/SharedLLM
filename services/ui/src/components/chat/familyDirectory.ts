import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../services/api';

export interface FamilyMember {
  username: string;
  display_name: string;
  nextcloud_user: string | null;
  avatar: string | null;
}

export type FamilyLookup = (actorId?: string | null) => FamilyMember | undefined;

/**
 * The household as chat draws it: a Talk sender id (their Nextcloud account,
 * or their SharedLLM username) to their name and profile picture.
 *
 * Display names are indexed too: a one-to-one room does not always carry the
 * other account's id, so the name Talk shows is the only handle available.
 */
export function buildFamilyLookup(members: FamilyMember[] | undefined): FamilyLookup {
  const byId = new Map<string, FamilyMember>();
  for (const member of Array.isArray(members) ? members : []) {
    if (!member?.username) continue;
    byId.set(member.username.toLowerCase(), member);
    if (member.nextcloud_user) byId.set(member.nextcloud_user.toLowerCase(), member);
    if (member.display_name) byId.set(member.display_name.toLowerCase(), member);
  }
  return (actorId) => (actorId ? byId.get(actorId.trim().toLowerCase()) : undefined);
}

export function useFamilyDirectory(): FamilyLookup {
  const { data } = useQuery({
    queryKey: ['family-directory'],
    queryFn: () => api.getFamilyDirectory(),
    staleTime: 5 * 60 * 1000,
    retry: false,
  });
  return useMemo(() => buildFamilyLookup(data), [data]);
}

/** Words in a Home Assistant entity id that identify the entity, not a person. */
const ENTITY_NOISE = new Set(['person', 'user', 'users', 'phone', 'mobile', 'tracker', 'device', 'gps', 'ha', 'app']);

/**
 * Every name a family member might be known by in chat or a map payload.
 * `person.michele_phone` and the app's own `user.michele` key both get back to
 * the account `michele`, so one directory entry covers every spelling.
 */
export function familyLookupKeys(id?: string | null, name?: string | null): string[] {
  const keys = new Set<string>();
  const add = (value?: string | null) => {
    const key = value?.trim().toLowerCase();
    if (key) keys.add(key);
  };
  add(id);
  add(name);
  if (id) {
    const bare = id.replace(/^(person|user)\./i, '').replace(/^users\//i, '').trim();
    add(bare);
    for (const token of bare.replace(/-/g, '_').split('_')) {
      if (token.length >= 3 && !ENTITY_NOISE.has(token)) add(token);
    }
  }
  return [...keys];
}

/** The directory entry for a chat actor, HA person, or app location key. */
export function findFamilyMember(
  lookup: FamilyLookup,
  id?: string | null,
  name?: string | null,
): FamilyMember | undefined {
  for (const key of familyLookupKeys(id, name)) {
    const member = lookup(key);
    if (member) return member;
  }
  return undefined;
}

/** Bright, readable name colours on the dark chat background, one per person. */
const NAME_COLOURS = ['#f472b6', '#fbbf24', '#34d399', '#38bdf8', '#a78bfa', '#fb923c', '#f87171', '#2dd4bf'];

export function nameColour(name: string): string {
  let hash = 0;
  for (let i = 0; i < name.length; i += 1) hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  return NAME_COLOURS[hash % NAME_COLOURS.length];
}
