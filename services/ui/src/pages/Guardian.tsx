import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, Navigate, useSearchParams } from 'react-router-dom';
import { isAxiosError } from 'axios';
import {
  ArrowRight,
  CalendarClock,
  HeartHandshake,
  LayoutGrid,
  Lock,
  Pause,
  Play,
  PlusCircle,
  ShieldCheck,
  ShieldHalf,
  Sparkles,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { api } from '../services/api';
import { useAuth } from '../context/AuthContext';
import { memberPath, statusQuery } from '../lib/memberPage';
import { duration } from '../lib/phoneUsageFormat';
import { lockedLine } from '../lib/screenTimeText';
import UserAvatar from '../components/UserAvatar';
import { TimeRing } from '../components/member/charts';
import RecapCard from '../components/guardian/RecapCard';
import GuidanceCard from '../components/guardian/GuidanceCard';
import JournalCard from '../components/guardian/JournalCard';
import RequestsCard from '../components/guardian/RequestsCard';
import ChoresCard from '../components/guardian/ChoresCard';
import TalkRequestCard from '../components/guardian/TalkRequestCard';
import GeofenceAlertsCard from '../components/guardian/GeofenceAlertsCard';
import NotificationLockCard from '../components/guardian/NotificationLockCard';
import DevicePolicyCard from '../components/guardian/DevicePolicyCard';
import ConcernsCard from '../components/member/ConcernsCard';
import DevicesSection from '../components/member/sections/DevicesSection';
import ScreenTimeControls from '../components/health/ScreenTimeControls';
import EarnTimeControls from '../components/health/EarnTimeControls';
import ScreenTimeCard from '../components/health/ScreenTimeCard';
import AskForSiteCard from '../components/health/AskForSiteCard';
import PhoneProtectionCard from '../components/health/PhoneProtectionCard';
import type { FamilyRecord, ScreenTimeStatus } from '../types/api';

const PAGE = 'mx-auto w-full min-w-0 max-w-6xl space-y-4 px-1 pb-28 sm:space-y-5 sm:px-0';
const GRID = 'grid min-w-0 grid-cols-1 content-start items-start gap-3 sm:gap-4 md:grid-cols-2 lg:grid-cols-2';

type GuardianDomain = 'all' | 'overview' | 'heart' | 'time' | 'safety';

const DOMAINS: Array<{ id: GuardianDomain; label: string; icon: LucideIcon; description: string }> = [
  { id: 'all', label: 'All areas', icon: LayoutGrid, description: 'Every guardian card in one view' },
  { id: 'overview', label: 'Overview & Alerts', icon: Sparkles, description: 'Weekly recap, open requests, and content concerns' },
  { id: 'heart', label: 'Heart & Faith', icon: HeartHandshake, description: 'Scripture, prayer prompts, and parent conversation journal' },
  { id: 'time', label: 'Time & Chores', icon: CalendarClock, description: 'Daily chores, earned screen time, and weekly routines' },
  { id: 'safety', label: 'Safety & Devices', icon: ShieldCheck, description: 'Geofence alerts, lock-screen notifications, and device protection' },
];

const ROLE_WORDS: Record<string, string> = { child: 'Child', teen: 'Teen' };
const LOCK_FOR = [30, 60, 120];
const BONUS = [15, 30, 60];

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
    <header className="glass-panel relative overflow-hidden rounded-2xl border border-white/10 p-4 sm:p-5">
      <div className="pointer-events-none absolute -right-12 -top-12 h-36 w-36 rounded-full bg-sky-500/10 blur-3xl" aria-hidden />
      <div className="relative flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 flex-1 items-center gap-3.5">
          <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border border-sky-400/25 bg-gradient-to-br from-sky-500/20 to-indigo-500/10 shadow-inner">
            <ShieldHalf size={24} className="text-sky-300" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-xl font-bold tracking-tight text-white sm:text-2xl">Family guardian</h1>
              {child && (
                <span className="rounded-full border border-sky-400/25 bg-sky-400/10 px-2.5 py-0.5 text-[11px] font-medium text-sky-200">
                  {ROLE_WORDS[child.family_role || ''] || 'Child'}
                </span>
              )}
            </div>
            <p className="mt-0.5 break-words text-xs leading-relaxed text-slate-400">{description}</p>
          </div>
        </div>
        {child && (
          <Link
            to={memberPath(child.username)}
            className="glass-button inline-flex min-h-11 shrink-0 items-center gap-1.5 rounded-xl px-3.5 text-xs font-semibold text-sky-100"
            data-testid="guardian-full-page"
          >
            Full page <ArrowRight size={14} />
          </Link>
        )}
      </div>
    </header>
  );
}

/** Live status and one-tap controls for the selected child. */
function ChildStatusBanner({ child, status }: { child: FamilyRecord; status?: ScreenTimeStatus }) {
  const username = child.username;
  const name = child.display_name || username;
  const queryClient = useQueryClient();
  const [openAction, setOpenAction] = useState<'lock' | 'bonus' | null>(null);

  const refresh = () => {
    setOpenAction(null);
    void queryClient.invalidateQueries({ queryKey: ['screen-time', username] });
  };
  const pause = useMutation({
    mutationFn: (body: { until?: string; resume?: boolean }) => api.pauseScreenTime(username, body),
    onSuccess: refresh,
  });
  const bonus = useMutation({
    mutationFn: (minutes: number) => api.giveScreenTimeBonus(username, minutes),
    onSuccess: refresh,
  });

  if (!status) return null;

  const busy = pause.isPending || bonus.isPending;
  const statusBadge = status.paused
    ? { label: 'Paused', cls: 'border-red-400/30 bg-red-400/10 text-red-200' }
    : status.locked
      ? { label: status.reason === 'bedtime' ? 'Bedtime' : status.reason === 'school' ? 'School hours' : 'Locked', cls: 'border-amber-400/30 bg-amber-400/10 text-amber-200' }
      : { label: 'Active', cls: 'border-emerald-400/30 bg-emerald-400/10 text-emerald-200' };

  const summaryText = status.locked
    ? lockedLine(status)
    : status.remaining_min !== null
      ? `${duration(status.remaining_min * 60000)} remaining today · ${duration(status.used_min * 60000)} used`
      : `${duration(status.used_min * 60000)} used today · no daily limit`;

  return (
    <section
      className="glass-panel rounded-2xl border border-white/10 p-3.5 sm:p-4 space-y-3"
      aria-label={`${name} status and quick controls`}
      data-testid="guardian-child-status"
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 items-center gap-3">
          <UserAvatar username={username} avatar={child.avatar} name={name} size={44} />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="truncate text-sm font-bold text-white">{name}</span>
              <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${statusBadge.cls}`}>
                {statusBadge.label}
              </span>
              {status.bonus_min > 0 && (
                <span className="rounded-full border border-sky-400/25 bg-sky-400/10 px-2 py-0.5 text-[10px] font-medium text-sky-200">
                  +{status.bonus_min}m bonus
                </span>
              )}
            </div>
            <p className="mt-0.5 text-xs text-slate-300">{summaryText}</p>
          </div>
          <div className="hidden sm:block">
            <TimeRing used={status.used_min} limit={status.limit_min} size={44} label={false} />
          </div>
        </div>

        {status.can_manage && (
          <div className="grid grid-cols-3 gap-2 sm:flex sm:flex-wrap sm:items-center" role="group" aria-label={`Quick actions for ${name}`}>
            <button
              type="button"
              disabled={busy}
              onClick={() => pause.mutate(status.paused ? { resume: true } : {})}
              className="glass-button inline-flex min-h-11 items-center justify-center gap-1.5 rounded-xl px-3 text-xs font-semibold disabled:opacity-50"
              data-testid="guardian-quick-pause"
            >
              {status.paused ? <Play size={14} /> : <Pause size={14} />}
              <span>{status.paused ? 'Resume' : 'Pause'}</span>
            </button>
            <button
              type="button"
              disabled={busy}
              aria-expanded={openAction === 'bonus'}
              onClick={() => setOpenAction(openAction === 'bonus' ? null : 'bonus')}
              className="glass-button inline-flex min-h-11 items-center justify-center gap-1.5 rounded-xl px-3 text-xs font-semibold disabled:opacity-50"
              data-testid="guardian-quick-bonus"
            >
              <PlusCircle size={14} />
              <span>Bonus</span>
            </button>
            <button
              type="button"
              disabled={busy}
              aria-expanded={openAction === 'lock'}
              onClick={() => setOpenAction(openAction === 'lock' ? null : 'lock')}
              className="glass-button inline-flex min-h-11 items-center justify-center gap-1.5 rounded-xl px-3 text-xs font-semibold disabled:opacity-50"
              data-testid="guardian-quick-lock"
            >
              <Lock size={14} />
              <span>Lock</span>
            </button>
          </div>
        )}
      </div>

      {openAction && status.can_manage && (
        <div className="flex flex-wrap items-center gap-2 border-t border-white/5 pt-2.5">
          <span className="text-[11px] font-medium text-slate-400">
            {openAction === 'bonus' ? 'Give extra time today:' : 'Lock devices for:'}
          </span>
          {(openAction === 'bonus' ? BONUS : LOCK_FOR).map((m) => (
            <button
              key={m}
              type="button"
              disabled={busy}
              onClick={() =>
                openAction === 'bonus'
                  ? bonus.mutate(m)
                  : pause.mutate({ until: new Date(Date.now() + m * 60000).toISOString() })
              }
              className="min-h-11 rounded-xl border border-sky-400/30 bg-sky-400/10 px-3 text-xs font-semibold text-sky-100 hover:bg-sky-400/20 disabled:opacity-50"
            >
              {m < 60 ? `+${m} min` : `${m / 60} hr`}
            </button>
          ))}
        </div>
      )}
    </section>
  );
}

function SectionHeading({ icon: Icon, title, subtitle }: { icon: LucideIcon; title: string; subtitle: string }) {
  return (
    <div className="flex items-center gap-2.5 px-1 pt-1">
      <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-white/10 bg-white/5 text-slate-300">
        <Icon size={14} />
      </div>
      <div className="min-w-0">
        <h2 className="text-xs font-bold uppercase tracking-wider text-slate-200">{title}</h2>
        <p className="text-[11px] text-slate-400">{subtitle}</p>
      </div>
    </div>
  );
}

/** A child's own side: their time, asking a parent, and a quiet way to talk. */
function GuardianSelf() {
  return (
    <div className={PAGE} data-testid="guardian-page">
      <Header description="Your screen time, website requests, phone protection, and a quiet way to reach a parent." />
      <div className={GRID}>
        <ScreenTimeCard />
        <TalkRequestCard />
        <AskForSiteCard />
        <PhoneProtectionCard />
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
  const [domain, setDomain] = useState<GuardianDomain>('all');
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

  const showOverview = domain === 'all' || domain === 'overview';
  const showHeart = domain === 'all' || domain === 'heart';
  const showTime = domain === 'all' || domain === 'time';
  const showSafety = domain === 'all' || domain === 'safety';

  return (
    <div className={PAGE} data-testid="guardian-page">
      <Header child={child} description={`${name}: the week, what to talk about and every control.`} />

      {children.length > 1 && (
        <nav
          className="-mx-1 flex gap-2.5 overflow-x-auto px-1 pb-1"
          aria-label="Your children"
          data-testid="guardian-children"
          data-scroll-x
        >
          {children.map((c) => {
            const label = c.display_name || c.username;
            const active = c.username === child.username;
            return (
              <button
                key={c.username}
                type="button"
                aria-label={label}
                aria-pressed={active}
                onClick={() => setSelected(c.username)}
                className={`flex min-h-11 shrink-0 items-center gap-2.5 rounded-2xl border px-3.5 py-1.5 text-sm font-semibold transition-all ${
                  active
                    ? 'border-sky-400/50 bg-sky-400/15 text-white shadow-sm'
                    : 'border-white/10 bg-white/5 text-slate-300 hover:border-white/20 hover:text-white'
                }`}
              >
                <span aria-hidden="true" className="shrink-0">
                  <UserAvatar username={c.username} avatar={c.avatar} name={label} size={28} />
                </span>
                <span>{label}</span>
              </button>
            );
          })}
        </nav>
      )}

      <ChildStatusBanner child={child} status={status} />

      <nav
        className="sticky top-0 z-10 -mx-1 flex gap-1.5 overflow-x-auto rounded-2xl border border-white/10 bg-slate-950/85 p-1.5 backdrop-blur"
        aria-label="Guardian sections"
        data-testid="guardian-domain-nav"
        data-scroll-x
      >
        {DOMAINS.map((d) => {
          const Icon = d.icon;
          const active = domain === d.id;
          return (
            <button
              key={d.id}
              type="button"
              aria-pressed={active}
              onClick={() => setDomain(d.id)}
              className={`inline-flex min-h-11 shrink-0 items-center gap-2 rounded-xl px-3.5 text-xs font-semibold transition-colors ${
                active
                  ? 'border border-sky-400/40 bg-sky-400/15 text-white'
                  : 'border border-transparent text-slate-400 hover:bg-white/5 hover:text-slate-200'
              }`}
            >
              <Icon size={14} className={active ? 'text-sky-300' : 'text-slate-400'} />
              <span>{d.label}</span>
            </button>
          );
        })}
      </nav>

      {showOverview && (
        <section className="space-y-3" aria-label="Overview and alerts">
          <SectionHeading
            icon={Sparkles}
            title="Overview & Alerts"
            subtitle="Weekly activity summary, pending requests, and flagged content"
          />
          <div className={GRID}>
            <div className="min-w-0 md:col-span-2 lg:col-span-2">
              <RecapCard username={child.username} name={name} />
            </div>
            <RequestsCard username={child.username} name={name} />
            <ConcernsCard username={child.username} name={name} />
          </div>
        </section>
      )}

      {showHeart && (
        <section className="space-y-3" aria-label="Heart and faith">
          <SectionHeading
            icon={HeartHandshake}
            title="Heart & Faith"
            subtitle="Scripture prompts, conversation starters, and shared parent notes"
          />
          <div className={GRID}>
            <GuidanceCard username={child.username} name={name} />
            <JournalCard username={child.username} name={name} />
          </div>
        </section>
      )}

      {showTime && (
        <section className="space-y-3" aria-label="Time and chores">
          <SectionHeading
            icon={CalendarClock}
            title="Time & Chores"
            subtitle="Daily chores, earned bonus minutes, and weekly screen-time schedule"
          />
          <div className={GRID}>
            <ChoresCard username={child.username} name={name} />
            <EarnTimeControls username={child.username} name={name} />
            <ScreenTimeControls username={child.username} name={name} />
          </div>
        </section>
      )}

      {showSafety && (
        <section className="space-y-3" aria-label="Safety and devices">
          <SectionHeading
            icon={ShieldCheck}
            title="Safety & Devices"
            subtitle="Geofence alerts, lock-screen protection, and hardware management"
          />
          <div className={GRID}>
            <GeofenceAlertsCard username={child.username} name={name} />
            <NotificationLockCard username={child.username} name={name} />
            {/* Guard state, MDM device policies, and tamper reports: the father and admins only (the
                server refuses anyone else, the mother included). */}
            {status?.technical && <DevicePolicyCard username={child.username} name={name} />}
            {status?.technical && <DevicesSection username={child.username} name={name} />}
          </div>
        </section>
      )}
    </div>
  );
}
