import { useQuery } from '@tanstack/react-query';
import { api } from '../services/api';
import type { RavenMission } from '../services/api';

export function useRavenMissions() {
  return useQuery<RavenMission[]>({
    queryKey: ['raven-missions'],
    queryFn: () => api.getUserMissions(),
    refetchInterval: 30000,
    // The worker marks a running mission "executing"; filtering on "running"
    // alone hid every running mission from the dashboard.
    select: (missions) =>
      missions.filter((m) => ['queued', 'executing', 'running', 'paused'].includes(m.status)),
  });
}