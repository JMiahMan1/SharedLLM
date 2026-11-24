import { useMemo } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { isAxiosError } from 'axios';
import { Lock, RefreshCw, Trophy } from 'lucide-react';
import { api } from '../services/api';
import { useAuth } from '../context/AuthContext';
import PageHeader, { HEADER_ACTION } from '../components/ui/PageHeader';
import { CardSkeletons, ErrorRetry } from '../components/ui/StateViews';
import UserAvatar from '../components/UserAvatar';
import { useFamilyMembers } from '../components/chat/familyDirectory';
import ProfileAreaCard from '../components/profile/ProfileAreaCard';
import SharingPanel from '../components/profile/SharingPanel';
import { AREA_TITLES, firstName, type ProfileStats } from '../lib/profileStats';

/** Accounts that are the household's machinery, not a person. */
const NOT_PEOPLE = new Set(['default', 'anonymous', 'household']);

/**
 * The profile page (docs/PROFILE_STATS.md): one person across Jarvis, from
 * Bible reading to garden care to game scores. A person sees all of their own;
 * the family sees each area only when its owner shares it, and the server
 * withholds the rest. Every stat opens the page that does that thing.
 */
export default function Profile() {
  const { user } = useAuth();
  const me = (user?.username || '').toLowerCase();
  const { username: asked } = useParams();
  const subject = (asked || me).toLowerCase();
  const own = subject === me;
  const key = own ? 'me' : subject;
  const queryClient = useQueryClient();
  const members = useFamilyMembers();
  const people = useMemo(() => {
    const list = members.filter((m) => m.username && !NOT_PEOPLE.has(m.username.toLowerCase()));
    return [...list].sort((a, b) => (a.username === me ? -1 : b.username === me ? 1 : (a.display_name || '').localeCompare(b.display_name || '')));
  }, [members, me]);

  const { data, isLoading, error, refetch, isFetching } = useQuery<ProfileStats>({
    queryKey: ['profile-stats', key],
    queryFn: () => api.getProfileStats(key),
    enabled: Boolean(me),
    staleTime: 60 * 1000,
    retry: false,
  });
  const refresh = async () => {
    const fresh = await api.getProfileStats(key, true);
    queryClient.setQueryData(['profile-stats', key], fresh);
  };

  const name = data?.person.display_name || people.find((p) => p.username === subject)?.display_name || subject;
  const visible = (data?.cards ?? []).filter((c) => c.state !== 'hidden');
  const hidden = (data?.cards ?? []).filter((c) => c.state === 'hidden');
  const who = { username: subject, name, own };

  return (
    <div className="mx-auto w-full min-w-0 max-w-6xl space-y-3 px-1 pb-28 sm:space-y-4 sm:px-0" data-testid="profile-page">
      <PageHeader
        icon={Trophy}
        tone="text-amber-300"
        title={own ? 'My profile' : `${name}'s profile`}
        subtitle={own ? 'Your streaks, scores and habits across Jarvis. Tap any of them to pick it up.' : `What ${firstName(name)} shares with the family. Tap a score to try to beat it.`}
        actions={(
          <button type="button" className={HEADER_ACTION} onClick={() => void refresh()} disabled={isFetching} data-testid="profile-refresh">
            <RefreshCw size={14} aria-hidden className={isFetching ? 'animate-spin' : ''} />Refresh
          </button>
        )}
      >
        {people.length > 1 && (
          <nav aria-label="Family profiles" className="-mx-1 flex gap-2 overflow-x-auto px-1 pb-1" data-testid="profile-people">
            {people.map((p) => {
              const current = p.username.toLowerCase() === subject;
              return (
                <Link
                  key={p.username}
                  to={p.username.toLowerCase() === me ? '/profile' : `/profile/${encodeURIComponent(p.username)}`}
                  aria-current={current ? 'page' : undefined}
                  data-testid={`profile-person-${p.username}`}
                  className={`flex min-h-11 shrink-0 items-center gap-2 rounded-full border py-1 pl-1 pr-3 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-purple-400/70 ${
                    current ? 'border-amber-300/40 bg-amber-300/10 text-white' : 'border-white/10 text-slate-300 hover:bg-white/5'
                  }`}
                >
                  <UserAvatar username={p.username} avatar={p.avatar} name={p.display_name} size={32} />
                  <span className="max-w-[8rem] truncate">{p.username.toLowerCase() === me ? 'Me' : firstName(p.display_name || p.username)}</span>
                </Link>
              );
            })}
          </nav>
        )}
      </PageHeader>

      {isLoading ? (
        <CardSkeletons count={6} label="Loading the profile" className="grid gap-3 md:grid-cols-2" height="h-48" />
      ) : isAxiosError(error) && error.response?.status === 404 ? (
        <p className="glass-panel rounded-2xl p-5 text-sm text-slate-300" data-testid="profile-not-found">No one in the family has that name.</p>
      ) : error ? (
        <ErrorRetry title="The profile could not be loaded" detail="Jarvis is not answering just now." onRetry={() => void refetch()} />
      ) : data ? (
        <>
          {visible.length === 0 ? (
            <div className="glass-panel flex items-center gap-3 rounded-2xl border border-white/10 p-5 text-sm text-slate-300" data-testid="profile-nothing-shared">
              <Lock size={16} aria-hidden className="text-slate-500" />
              {firstName(name)} has not shared anything on their profile yet.
            </div>
          ) : (
            <div className="grid min-w-0 gap-3 md:grid-cols-2" data-testid="profile-cards">
              {visible.map((card) => <ProfileAreaCard key={card.area} card={card} who={who} />)}
            </div>
          )}
          {!own && hidden.length > 0 && visible.length > 0 && (
            <p className="flex items-center gap-2 px-2 text-xs text-slate-500" data-testid="profile-private-areas">
              <Lock size={12} aria-hidden />
              {firstName(name)} keeps {hidden.map((c) => AREA_TITLES[c.area].toLowerCase()).join(', ')} private.
            </p>
          )}
          {own && <SharingPanel sharing={data.sharing} />}
        </>
      ) : null}
    </div>
  );
}
