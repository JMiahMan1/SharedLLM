import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { Lock, MapPin, MessageCircle, Pause, Play, PlusCircle, Smartphone } from 'lucide-react';
import toast from 'react-hot-toast';
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
import { useMemberDeviceNoun } from '../../hooks/useDeviceNoun';

const ROLE_WORDS: Record<string, string> = { child: 'Child', teen: 'Teen' };
const LOCK_FOR = [30, 60, 120];
const BONUS = [15, 30, 60];
const minutesWord = (m: number) => (m < 60 ? `${m} min` : `${m / 60} hr`);
const STATE_DOT: Record<string, string> = {
  in_use: 'bg-emerald-400', idle: 'bg-slate-400', locked: 'bg-amber-400', paused: 'bg-red-400', bedtime: 'bg-sky-400', offline: 'bg-slate-600',
};

/**
 * The top of a child's page (Aura's profile): who, what they are doing and
 * where, and the urgent actions one tap away (pause, bonus time, lock, talk).
 */
export default function ProfileHeader({ child }: { child: FamilyRecord }) {
  const device = useMemberDeviceNoun(child.username);
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
  // Every quick action says what it did (or that it did not), so a tap is never silent.
  const pause = useMutation({
    mutationFn: (body: { until?: string; resume?: boolean }) => api.pauseScreenTime(username, body),
    onSuccess: (_d, body) => {
      refresh();
      toast.success(body.resume ? `${name}'s screen time is back on` : body.until
        ? `${name}'s ${device} is locked until ${new Date(body.until).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`
        : `${name}'s screen time is paused until you resume it`);
    },
  });
  const bonus = useMutation({
    mutationFn: (minutes: number) => api.giveScreenTimeBonus(username, minutes),
    onSuccess: (_d, minutes) => {
      refresh();
      toast.success(`Gave ${name} ${minutesWord(minutes)} more today`);
    },
  });
  const failed = [pause.error, bonus.error].find(Boolean);
  const failure = isAxiosError(failed) ? (failed.response?.data as { detail?: string })?.detail || 'That did not save.' : failed ? 'That did not save.' : null;

  const today = usage?.days.find((d) => d.day === localDay());
  const state = memberState(status, today);
  const lastApp = [...(today?.apps ?? [])].filter((a) => a.last_use).sort((a, b) => (b.last_use || 0) - (a.last_use || 0))[0];
  const place = where ? placeAt(where.latitude, where.longitude, zonePlaces(zones)) : null;
  const seen = where ? fixTimeSeconds(where) : 0;
  const busy = pause.isPending || bonus.isPending;

  const action = 'glass-button flex min-h-14 flex-col items-center justify-center gap-1 px-1 text-[11px] font-semibold disabled:opacity-50 sm:min-h-11 sm:flex-row sm:gap-1.5 sm:px-3 sm:text-xs';
  const now = isLoading
    ? '…'
    : status?.locked
      ? lockedLine(status)
      : state === 'in_use' && lastApp
        ? `Using ${appName(lastApp.package)}`
        : today?.last_use
          ? `Last on the ${device} ${relativeTime(today.last_use)}`
          : `No ${device} use reported today.`;

  return (
    <header className="glass-panel space-y-3 rounded-2xl border border-white/10 p-3.5 sm:p-5" data-testid="member-header">
      <div className="flex items-center gap-3">
        <UserAvatar username={username} avatar={child.avatar} name={name} size={48} />
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-xl font-bold leading-tight text-white sm:text-2xl">{name}</h1>
          <p className="mt-0.5 flex items-center gap-1.5 text-xs text-slate-400">
            <span>{ROLE_WORDS[child.family_role] || 'Family'}</span>
            <span aria-hidden>·</span>
            <span className={`h-2 w-2 shrink-0 rounded-full ${STATE_DOT[state] || 'bg-slate-500'}`} aria-hidden />
            <span data-testid="member-header-state">{STATE_LABELS[state]}</span>
          </p>
        </div>
      </div>

      <dl className="divide-y divide-white/5 rounded-xl border border-white/10 bg-white/[0.03] text-xs sm:grid sm:grid-cols-2 sm:divide-x sm:divide-y-0">
        <div className="flex min-w-0 items-start gap-2 px-3 py-2">
          <Smartphone size={14} className="mt-0.5 shrink-0 text-slate-400" aria-hidden />
          <div className="min-w-0">
            <dt className="sr-only">Now</dt>
            <dd className="text-slate-200" data-testid="member-now">{now}</dd>
          </div>
        </div>
        <div className="flex min-w-0 items-start gap-2 px-3 py-2">
          <MapPin size={14} className="mt-0.5 shrink-0 text-slate-400" aria-hidden />
          <div className="min-w-0">
            <dt className="sr-only">Where</dt>
            <dd className="text-slate-200" data-testid="member-where">
              {where ? (
                <>
                  {place || 'Away from family places'}
                  <span className="text-slate-500"> · {seen ? relativeTime(seen) : 'time unknown'}</span>
                </>
              ) : (
                `Location is not shared from their ${device}.`
              )}
            </dd>
          </div>
        </div>
      </dl>

      {status?.can_manage && (
        <div className="space-y-2">
          <div className="grid grid-cols-4 gap-1.5 sm:flex sm:flex-wrap sm:gap-2" role="group" aria-label={`Quick actions for ${name}`}>
            <button
              type="button"
              className={action}
              disabled={busy}
              onClick={() => pause.mutate(status.paused ? { resume: true } : {})}
              data-testid="member-pause"
            >
              {status.paused ? <Play size={16} /> : <Pause size={16} />} {status.paused ? 'Resume' : 'Pause'}
            </button>
            <button
              type="button"
              className={`${action} ${open === 'bonus' ? 'ring-1 ring-sky-400/50' : ''}`}
              aria-expanded={open === 'bonus'}
              aria-controls="member-quick-options"
              onClick={() => setOpen(open === 'bonus' ? null : 'bonus')}
              data-testid="member-bonus"
            >
              <PlusCircle size={16} /> <span>Bonus<span className="hidden sm:inline"> time</span></span>
            </button>
            <button
              type="button"
              className={`${action} ${open === 'lock' ? 'ring-1 ring-sky-400/50' : ''}`}
              aria-expanded={open === 'lock'}
              aria-controls="member-quick-options"
              onClick={() => setOpen(open === 'lock' ? null : 'lock')}
              data-testid="member-lock"
            >
              <Lock size={16} /> <span>Lock<span className="hidden sm:inline"> now</span></span>
            </button>
            <Link to="/family" className={action} data-testid="member-talk">
              <MessageCircle size={16} /> Talk
            </Link>
          </div>
          {open && (
            <div
              id="member-quick-options"
              className="space-y-2 rounded-xl border border-white/10 bg-white/[0.03] p-2.5"
              role="group"
              aria-label={open === 'bonus' ? 'Add time today' : `Lock the ${device} for`}
              data-testid="member-quick-options"
            >
              <p className="text-[11px] text-slate-400">
                {open === 'bonus' ? `Add to ${name}'s time today` : `Lock ${name}'s ${device} for`}
              </p>
              <div className="grid grid-cols-4 gap-1.5 sm:flex sm:flex-wrap sm:gap-2">
                {(open === 'bonus' ? BONUS : LOCK_FOR).map((m) => (
                  <button
                    key={m}
                    type="button"
                    disabled={busy}
                    className="min-h-11 rounded-xl border border-sky-400/30 bg-sky-400/10 px-3 text-xs font-semibold text-sky-100 hover:bg-sky-400/20 disabled:opacity-50"
                    onClick={() =>
                      open === 'bonus'
                        ? bonus.mutate(m)
                        : pause.mutate({ until: new Date(Date.now() + m * 60000).toISOString() })
                    }
                  >
                    {open === 'bonus' ? `+${minutesWord(m)}` : minutesWord(m)}
                  </button>
                ))}
                <button
                  type="button"
                  className="min-h-11 rounded-xl px-3 text-xs text-slate-400 hover:text-slate-200"
                  onClick={() => setOpen(null)}
                  data-testid="member-quick-cancel"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
          {failure && <p className="text-xs text-amber-300" role="alert">{failure}</p>}
        </div>
      )}
    </header>
  );
}
