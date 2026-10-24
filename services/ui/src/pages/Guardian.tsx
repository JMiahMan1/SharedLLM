import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { ShieldHalf } from 'lucide-react';
import { isAxiosError } from 'axios';
import { api } from '../services/api';
import { useAuth } from '../context/AuthContext';
import RecapCard from '../components/guardian/RecapCard';
import GuidanceCard from '../components/guardian/GuidanceCard';
import JournalCard from '../components/guardian/JournalCard';
import ActivityFeedCard from '../components/guardian/ActivityFeedCard';
import ScreenTimeControls from '../components/health/ScreenTimeControls';
import SuggestionLanesControls from '../components/health/SuggestionLanesControls';

/**
 * The parents' page (Phase 11 step 7): one child at a time — their week and
 * its concerns, prayer and guidance grounded in what happened, a talk
 * journal, what they have been doing, and every control (screen time, apps,
 * websites, suggestions). Identity's visibility rule decides who sees a
 * child; everyone else gets a plain explanation.
 */
const Guardian = () => {
  const me = useAuth().user?.username || '';
  const [params, setParams] = useSearchParams();
  const { data: family = [], isLoading, error } = useQuery({
    queryKey: ['family-roster'],
    queryFn: () => api.getFamily(),
    retry: false,
    staleTime: 10 * 60 * 1000,
  });
  const children = useMemo(() => family.filter((m) => m.is_minor && m.username !== me), [family, me]);
  const asked = params.get('child');
  const child = children.find((c) => c.username === asked) || children[0];
  const name = child ? child.display_name || child.username : '';

  return (
    <div className="mx-auto max-w-4xl space-y-3 px-1 pb-12 sm:space-y-4 sm:px-0" data-testid="guardian-page">
      <header className="glass-panel rounded-2xl border border-white/10 p-4 sm:p-5">
        <div className="flex items-center gap-3">
          <div className="shrink-0 rounded-xl border border-sky-500/20 bg-sky-500/10 p-2.5">
            <ShieldHalf size={24} className="text-sky-300" />
          </div>
          <div className="min-w-0">
            <h1 className="text-xl font-bold text-white sm:text-2xl">Family guardian</h1>
            <p className="mt-0.5 text-xs text-slate-400">Each child's week, what to pray about, and the boundaries you set.</p>
          </div>
        </div>
        {children.length > 1 && (
          <div className="mt-3 flex flex-wrap gap-2" role="tablist" aria-label="Choose a child">
            {children.map((c) => (
              <button
                key={c.username}
                type="button"
                role="tab"
                aria-selected={c.username === child?.username}
                onClick={() => setParams({ child: c.username })}
                className={`min-h-11 rounded-xl border px-3 text-xs ${c.username === child?.username ? 'border-sky-400/40 bg-sky-400/10 text-white' : 'border-white/10 bg-white/5 text-slate-400'}`}
              >
                {c.display_name || c.username}
              </button>
            ))}
          </div>
        )}
      </header>

      {isLoading ? (
        <div className="h-40 skeleton rounded-2xl" aria-hidden />
      ) : isAxiosError(error) && error.response?.status === 403 ? (
        <p className="glass-panel rounded-2xl p-5 text-sm text-slate-300" data-testid="guardian-not-a-parent">
          This page is for parents. It shows each child's week to their own parents only.
        </p>
      ) : error ? (
        <p className="glass-panel rounded-2xl p-5 text-sm text-amber-300">The family list could not be loaded.</p>
      ) : !child ? (
        <p className="glass-panel rounded-2xl p-5 text-sm text-slate-300" data-testid="guardian-no-children">
          No children in the family yet. Family roles are set in Admin → Users → Family.
        </p>
      ) : (
        <div className="space-y-3 sm:space-y-4" key={child.username}>
          <RecapCard username={child.username} name={name} />
          <GuidanceCard username={child.username} name={name} />
          <JournalCard username={child.username} name={name} />
          <ActivityFeedCard username={child.username} name={name} />
          <ScreenTimeControls username={child.username} name={name} />
          <SuggestionLanesControls username={child.username} name={name} />
        </div>
      )}
    </div>
  );
};

export default Guardian;
