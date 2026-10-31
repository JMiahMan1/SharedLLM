import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { Lock, MapPin, MessageCircle, Pause, Play, PlusCircle } from 'lucide-react';
import { isAxiosError } from 'axios';
import { api } from '../../services/api';
import UserAvatar from '../UserAvatar';
import { STATE_LABELS, memberState, placeAt, statusQuery, usageQuery } from '../../lib/memberPage';
import { geoZonesQueryKey, userLocationsQuery, zonePlaces } from '../../lib/wanderQueries';
import { relativeTime } from '../../lib/wanderTrips';
import { fixTimeSeconds } from '../geo/liveLocations';
import { appName } from '../../lib/phoneUsageFormat';
import { lockedLine } from '../../lib/screenTimeText';
import { localDay } from '../../lib/healthHub';
import type { FamilyRecord } from '../../types/api';

const ROLE_WORDS: Record<string, string> = { child: 'Child', teen: 'Teen' };
const LOCK_FOR = [30, 60, 120];
const BONUS = [15, 30, 60];

/**
 * The top of a child's page (Aura's profile): who, what they are doing and
 * where, and the urgent actions one tap away (pause, bonus time, lock, talk).
 */
export default function ProfileHeader({ child }: { child: FamilyRecord }) {
  const username = child.username;
  const name = child.display_name || username;
  const queryClient = useQueryClient();
  const { data: status, isLoading } = useQuery(statusQuery(username));
  const { data: usage } = useQuery(usageQuery(username));
  const { data: where } = useQuery({ ...userLocationsQuery, select: (all) => all[username] ?? null });
  const { data: zones } = useQuery({ queryKey: geoZonesQueryKey(), queryFn: () => api.getGeoZones(), retry: false, staleTime: 5 * 60_000 });
  const [open, setOpen] = useState<'lock' | 'bonus' | null>(null);

  const refresh = () => {
    setOpen(null);
    void queryClient.invalidateQueries({ queryKey: ['screen-time', username] });
  };
  const pause = useMutation({
    mutationFn: (body: { until?: string; resume?: boolean }) => api.pauseScreenTime(username, body),
    onSuccess: refresh,
  });
  const bonus = useMutation({ mutationFn: (minutes: number) => api.giveScreenTimeBonus(username, minutes), onSuccess: refresh });
  const failed = [pause.error, bonus.error].find(Boolean);
  const failure = isAxiosError(failed) ? (failed.response?.data as { detail?: string })?.detail || 'That did not save.' : failed ? 'That did not save.' : null;

  const today = usage?.days.find((d) => d.day === localDay());
  const state = memberState(status, today);
  const lastApp = [...(today?.apps ?? [])].filter((a) => a.last_use).sort((a, b) => (b.last_use || 0) - (a.last_use || 0))[0];
  const place = where ? placeAt(where.latitude, where.longitude, zonePlaces(zones)) : null;
  const seen = where ? fixTimeSeconds(where) : 0;
  const busy = pause.isPending || bonus.isPending;
  const button = 'glass-button inline-flex min-h-11 items-center justify-center gap-1.5 px-3 text-xs font-semibold disabled:opacity-50';

  return (
    <header className="glass-panel rounded-2xl border border-white/10 p-4 sm:p-5 space-y-3" data-testid="member-header">
      <div className="flex items-center gap-3">
        <UserAvatar username={username} avatar={child.avatar} name={name} size={56} />
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-xl font-bold text-white sm:text-2xl">{name}</h1>
          <p className="text-xs text-slate-400">
            {ROLE_WORDS[child.family_role] || 'Family'} · <span data-testid="member-header-state">{STATE_LABELS[state]}</span>
          </p>
        </div>
      </div>

      <dl className="grid grid-cols-1 gap-2 text-xs sm:grid-cols-2">
        <div className="rounded-xl border border-white/10 bg-white/5 px-3 py-2">
          <dt className="text-[10px] uppercase tracking-wider text-slate-500">Now</dt>
          <dd className="text-slate-200" data-testid="member-now">
            {isLoading
              ? '…'
              : status?.locked
                ? lockedLine(status)
                : state === 'in_use' && lastApp
                  ? `Using ${appName(lastApp.package)}`
                  : today?.last_use
                    ? `Last on the phone ${relativeTime(today.last_use)}`
                    : 'No phone use reported today.'}
          </dd>
        </div>
        <div className="rounded-xl border border-white/10 bg-white/5 px-3 py-2">
          <dt className="text-[10px] uppercase tracking-wider text-slate-500">Where</dt>
          <dd className="flex items-center gap-1 text-slate-200" data-testid="member-where">
            <MapPin size={12} className="shrink-0 text-slate-400" />
            {where ? `${place || 'Away from family places'} · ${seen ? relativeTime(seen) : 'time unknown'}` : 'Location is not shared from their phone.'}
          </dd>
        </div>
      </dl>

      {status?.can_manage && (
        <div className="space-y-2">
          <div className="grid grid-cols-2 gap-2 sm:flex sm:flex-wrap" role="group" aria-label={`Quick actions for ${name}`}>
            <button
              type="button"
              className={button}
              disabled={busy}
              onClick={() => pause.mutate(status.paused ? { resume: true } : {})}
              data-testid="member-pause"
            >
              {status.paused ? <Play size={14} /> : <Pause size={14} />} {status.paused ? 'Resume' : 'Pause'}
            </button>
            <button type="button" className={button} aria-expanded={open === 'bonus'} onClick={() => setOpen(open === 'bonus' ? null : 'bonus')} data-testid="member-bonus">
              <PlusCircle size={14} /> Bonus time
            </button>
            <button type="button" className={button} aria-expanded={open === 'lock'} onClick={() => setOpen(open === 'lock' ? null : 'lock')} data-testid="member-lock">
              <Lock size={14} /> Lock now
            </button>
            <Link to="/family" className={button} data-testid="member-talk">
              <MessageCircle size={14} /> Talk
            </Link>
          </div>
          {open && (
            <div className="flex flex-wrap items-center gap-2" role="group" aria-label={open === 'bonus' ? 'Add time today' : 'Lock the phone for'}>
              <span className="text-[11px] text-slate-400">{open === 'bonus' ? 'Add today:' : 'Lock for:'}</span>
              {(open === 'bonus' ? BONUS : LOCK_FOR).map((m) => (
                <button
                  key={m}
                  type="button"
                  disabled={busy}
                  className="min-h-11 rounded-xl border border-white/10 bg-white/5 px-3 text-xs text-slate-200"
                  onClick={() =>
                    open === 'bonus'
                      ? bonus.mutate(m)
                      : pause.mutate({ until: new Date(Date.now() + m * 60000).toISOString() })
                  }
                >
                  {m < 60 ? `${m} min` : `${m / 60} h`}
                </button>
              ))}
            </div>
          )}
          {failure && <p className="text-xs text-amber-300">{failure}</p>}
        </div>
      )}
    </header>
  );
}
