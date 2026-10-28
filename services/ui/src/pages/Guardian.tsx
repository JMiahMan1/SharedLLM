import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, Navigate, useSearchParams } from 'react-router-dom';
import { isAxiosError } from 'axios';
import { ArrowRight, ShieldHalf } from 'lucide-react';
import { api } from '../services/api';
import { useAuth } from '../context/AuthContext';
import { memberPath, statusQuery } from '../lib/memberPage';
import RecapCard from '../components/guardian/RecapCard';
import GuidanceCard from '../components/guardian/GuidanceCard';
import JournalCard from '../components/guardian/JournalCard';
import RequestsCard from '../components/guardian/RequestsCard';
import TalkRequestCard from '../components/guardian/TalkRequestCard';
import GeofenceAlertsCard from '../components/guardian/GeofenceAlertsCard';
import ConcernsCard from '../components/member/ConcernsCard';
import DevicesSection from '../components/member/sections/DevicesSection';
import ScreenTimeControls from '../components/health/ScreenTimeControls';
import ScreenTimeCard from '../components/health/ScreenTimeCard';
import AskForSiteCard from '../components/health/AskForSiteCard';
import PhoneProtectionCard from '../components/health/PhoneProtectionCard';
import type { FamilyRecord } from '../types/api';

const PAGE = 'mx-auto w-full min-w-0 max-w-6xl space-y-3 px-1 pb-28 sm:space-y-4 sm:px-0';
const GRID = 'grid min-w-0 grid-cols-1 content-start items-start gap-3 sm:gap-4 lg:grid-cols-2';

/**
 * The family guardian page.
 *
 * One place for the guarding side of the household, out of Health: a parent
 * sees one child at a time - the week, concerns, prayer and guidance, the talk
 * journal, the screen-time schedule and (father/admin only) the phones'
 * protection and tamper reports. A child sees their own side instead: the time
 * left, asking a parent, the phone's protection and "I need to talk".
 *
 * Every card is the same one the family member page uses; this page only
 * gathers them, so there is one implementation of each. `/guardian?child=…`
 * (older notices) still opens that child's family member page.
 */
export default function Guardian() {
  const user = useAuth().user;
  const [params] = useSearchParams();
  const child = params.get('child');

  if (child) return <Navigate to={memberPath(child)} replace />;
  if (user?.is_minor) return <GuardianSelf />;
  return <GuardianChildren />;
}

function Header({ description, child }: { description: string; child?: FamilyRecord }) {
  return (
    <header className="glass-panel rounded-2xl border border-white/10 p-4 sm:p-5">
      <div className="flex items-center gap-3">
        <div className="shrink-0 rounded-xl border border-sky-500/20 bg-sky-500/10 p-2.5">
          <ShieldHalf size={24} className="text-sky-300" />
        </div>
        <div className="min-w-0 flex-1">
          <h1 className="text-xl font-bold text-white sm:text-2xl">Family guardian</h1>
          <p className="mt-0.5 text-xs text-slate-400">{description}</p>
        </div>
        {child && (
          <Link
            to={memberPath(child.username)}
            className="glass-button inline-flex min-h-11 shrink-0 items-center gap-1.5 px-3 text-xs font-semibold"
            data-testid="guardian-full-page"
          >
            Full page <ArrowRight size={14} />
          </Link>
        )}
      </div>
    </header>
  );
}

/** A child's own side: their time, asking a parent, and a quiet way to talk. */
function GuardianSelf() {
  return (
    <div className={PAGE} data-testid="guardian-page">
      <Header description="Your phone, your time, and asking a parent." />
      <div className={GRID}>
        <ScreenTimeCard />
        <AskForSiteCard />
        <PhoneProtectionCard />
        <TalkRequestCard />
      </div>
    </div>
  );
}

/** A parent's side: one child at a time, the guarding cards that are theirs. */
function GuardianChildren() {
  const me = useAuth().user?.username || '';
  const { data: family = [], isLoading, error } = useQuery({
    queryKey: ['family-roster'],
    queryFn: () => api.getFamily(),
    retry: false,
    staleTime: 10 * 60 * 1000,
  });
  const children = useMemo(() => family.filter((m) => m.is_minor && m.username !== me), [family, me]);
  const [selected, setSelected] = useState('');
  const child = children.find((c) => c.username === selected) ?? children[0];
  const { data: status } = useQuery({ ...statusQuery(child?.username || ''), enabled: Boolean(child) });
  const name = child ? child.display_name || child.username : '';

  if (isLoading) {
    return (
      <div className={PAGE} data-testid="guardian-page">
        <Header description="The week, what to talk about and every control, one child at a time." />
        <div className="h-40 skeleton rounded-2xl" aria-hidden />
      </div>
    );
  }

  if (isAxiosError(error) && error.response?.status === 403) {
    return (
      <div className={PAGE} data-testid="guardian-page">
        <Header description="The week, what to talk about and every control, one child at a time." />
        <p className="glass-panel rounded-2xl p-5 text-sm text-slate-300" data-testid="guardian-not-a-parent">
          This page is for parents. It shows each child to their own parents only.
        </p>
      </div>
    );
  }

  if (error) {
    return (
      <div className={PAGE} data-testid="guardian-page">
        <Header description="The week, what to talk about and every control, one child at a time." />
        <p className="glass-panel rounded-2xl p-5 text-sm text-amber-300">The family list could not be loaded.</p>
      </div>
    );
  }

  if (!child) {
    return (
      <div className={PAGE} data-testid="guardian-page">
        <Header description="The week, what to talk about and every control, one child at a time." />
        <p className="glass-panel rounded-2xl p-5 text-sm text-slate-300" data-testid="guardian-no-children">
          No children in the family yet. Family roles are set in Admin → Users → Family.
        </p>
      </div>
    );
  }

  return (
    <div className={PAGE} data-testid="guardian-page">
      <Header child={child} description={`${name}: the week, what to talk about and every control.`} />

      {children.length > 1 && (
        <nav className="-mx-1 flex gap-2 overflow-x-auto px-1 pb-1" aria-label="Your children" data-testid="guardian-children">
          {children.map((c) => (
            <button
              key={c.username}
              type="button"
              aria-pressed={c.username === child.username}
              onClick={() => setSelected(c.username)}
              className={`min-h-11 shrink-0 rounded-2xl border px-3 text-sm ${
                c.username === child.username ? 'border-sky-400/40 bg-sky-400/10 text-white' : 'border-white/10 bg-white/5 text-slate-400'
              }`}
            >
              {c.display_name || c.username}
            </button>
          ))}
        </nav>
      )}

      <div className={GRID}>
        <div className="min-w-0 lg:col-span-2">
          <RecapCard username={child.username} name={name} />
        </div>
        <RequestsCard username={child.username} name={name} />
        <ConcernsCard username={child.username} name={name} />
        <GeofenceAlertsCard username={child.username} name={name} />
        <GuidanceCard username={child.username} name={name} />
        <JournalCard username={child.username} name={name} />
        <ScreenTimeControls username={child.username} name={name} />
        {/* Guard state and tamper reports: the father and admins only (the
            server refuses anyone else, the mother included). */}
        {status?.technical && <DevicesSection username={child.username} name={name} />}
      </div>
    </div>
  );
}
