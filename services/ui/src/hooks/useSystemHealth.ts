import { useQuery } from '@tanstack/react-query';
import { api } from '../services/api';
import type { HealthStatus } from '../services/api';

/**
 * Gateway readiness (`/health/ready`): every backing service's status.
 *
 * One query for the header pulse, the sidebar footer and the dashboard's
 * service board. They used to poll it separately every 5 to 10 s, and each poll
 * fans out to every service, so a single open tab kept the whole stack busy.
 */
export function useSystemHealth() {
  return useQuery<HealthStatus>({
    queryKey: ['health'],
    queryFn: () => api.getHealth(),
    refetchInterval: 15_000,
    retry: 2,
  });
}

/** The gateway's build (`/info`), or null when it is not reported. */
export function useServiceInfo() {
  return useQuery({
    queryKey: ['service-info'],
    queryFn: () => api.getInfo(),
    refetchOnWindowFocus: false,
    staleTime: 5 * 60_000,
    retry: false,
  });
}
