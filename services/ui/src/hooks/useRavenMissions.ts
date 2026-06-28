import { useQuery } from '@tanstack/react-query';
import { api } from '../services/api';
import type { RavenMission } from '../services/api';

export function useRavenMissions() {
  return useQuery<RavenMission[]>({
    queryKey: ['raven-missions'],
    queryFn: () => api.getUserMissions(),
    refetchInterval: 30000,
    select: (missions) =>
      missions.filter((m) => ['queued', 'running', 'paused'].includes(m.status)),
  });
}