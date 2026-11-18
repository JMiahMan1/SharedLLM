import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api, type IntegrationsStatus } from '../services/api';
import type { HubIntegrations } from '../components/layout/navItems';

/**
 * Which optional home integrations are on (`GET /api/integrations/status`).
 * The gateway reads them once at startup, so one fetch per session is enough.
 * While loading or on an error both read as off: a page never offers a
 * feature for an integration it could not confirm. `settled` says the answer
 * is in (or failed for good), so a gate can tell "still asking" from "off".
 */
export function useIntegrations() {
  const query = useQuery<IntegrationsStatus>({
    queryKey: ['integrations-status'],
    queryFn: () => api.getIntegrationsStatus(),
    staleTime: Infinity,
    retry: 1,
  });
  const homehub = query.data?.homehub?.enabled === true;
  const homeAssistant = query.data?.home_assistant?.enabled === true;
  const hubs = useMemo<HubIntegrations>(() => ({ homehub, homeAssistant }), [homehub, homeAssistant]);
  return {
    ...query,
    homehub,
    homeAssistant,
    /** Either home hub is on. */
    anyHub: homehub || homeAssistant,
    hubs,
    settled: query.isSuccess || query.isError,
  };
}
