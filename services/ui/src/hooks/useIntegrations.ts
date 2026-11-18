import { useQuery } from '@tanstack/react-query';
import { api, type IntegrationsStatus } from '../services/api';

/**
 * Which optional home integrations are on (`GET /api/integrations/status`).
 * The gateway reads them once at startup, so one fetch per session is enough.
 * While loading or on an error both read as off: a page never offers a
 * feature for an integration it could not confirm.
 */
export function useIntegrations() {
  const query = useQuery<IntegrationsStatus>({
    queryKey: ['integrations-status'],
    queryFn: () => api.getIntegrationsStatus(),
    staleTime: Infinity,
    retry: 1,
  });
  return {
    ...query,
    homehub: query.data?.homehub.enabled === true,
    homeAssistant: query.data?.home_assistant.enabled === true,
  };
}
