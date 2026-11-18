import type { ReactNode } from 'react';
import { House } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useIntegrations } from '../../hooks/useIntegrations';
import { CardSkeletons, EmptyState } from '../ui/StateViews';
import { CONNECT_HUB_TITLE } from '../../lib/homehub';
import { hubRequirementMet, type HubRequirement } from './navItems';

/**
 * A page that reads from a home hub, behind the integration it needs. With the
 * integration off the page is not in the nav, and visiting its address shows
 * how to connect one instead of an error from a hub that is not there.
 */
export default function HubRequired({ requires, page, children }: {
  requires: HubRequirement;
  /** The page's name, for the empty state ("Rooms"). */
  page: string;
  children: ReactNode;
}) {
  const integrations = useIntegrations();
  const isAdmin = useAuth().user?.is_admin ?? false;
  if (hubRequirementMet(requires, integrations.hubs)) return <>{children}</>;
  if (!integrations.settled) return <CardSkeletons count={2} label={`Loading ${page}`} />;
  const which = requires === 'homehub' ? 'HomeHub' : 'a home hub (HomeHub or Home Assistant)';
  return (
    <div className="mx-auto max-w-2xl p-4 sm:p-6" data-testid="hub-required">
      <EmptyState
        icon={<House size={28} />}
        title={CONNECT_HUB_TITLE}
        detail={isAdmin
          ? `${page} needs ${which}. Add its address under Accounts & Integrations.`
          : `${page} needs ${which}. Ask an admin to connect one in Settings.`}
        action={isAdmin ? { label: 'Open Accounts & Integrations', to: '/identity' } : null}
      />
    </div>
  );
}
