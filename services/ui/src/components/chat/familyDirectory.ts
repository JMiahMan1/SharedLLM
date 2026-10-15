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
 */
export function useFamilyDirectory(): FamilyLookup {
  const { data } = useQuery({
    queryKey: ['family-directory'],
    queryFn: () => api.getFamilyDirectory(),
    staleTime: 5 * 60 * 1000,
    retry: false,
  });
  return useMemo(() => {
    const byId = new Map<string, FamilyMember>();
    for (const member of Array.isArray(data) ? data : []) {
      byId.set(member.username.toLowerCase(), member);
      if (member.nextcloud_user) byId.set(member.nextcloud_user.toLowerCase(), member);
    }
    return (actorId) => (actorId ? byId.get(actorId.toLowerCase()) : undefined);
  }, [data]);
}

/** Bright, readable name colours on the dark chat background, one per person. */
const NAME_COLOURS = ['#f472b6', '#fbbf24', '#34d399', '#38bdf8', '#a78bfa', '#fb923c', '#f87171', '#2dd4bf'];

export function nameColour(name: string): string {
  let hash = 0;
  for (let i = 0; i < name.length; i += 1) hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  return NAME_COLOURS[hash % NAME_COLOURS.length];
}
