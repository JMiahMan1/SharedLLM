import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import UserAvatar from '../UserAvatar';
import { TimeRing } from './charts';
import { STATE_LABELS, memberPath, memberState, statusQuery, usageQuery, type MemberState } from '../../lib/memberPage';
import { localDay } from '../../lib/healthHub';
import type { FamilyRecord } from '../../types/api';

const DOT: Record<MemberState, string> = {
  in_use: 'bg-emerald-400',
  idle: 'bg-slate-400',
  locked: 'bg-amber-400',
  paused: 'bg-red-400',
  bedtime: 'bg-sky-400',
  offline: 'bg-slate-600',
};

/** One child at a glance: who, what state they are in (in words too), today's time against the limit. */
function StripMember({ child, selected }: { child: FamilyRecord; selected: boolean }) {
  const { data: status } = useQuery(statusQuery(child.username));
  const { data: usage } = useQuery(usageQuery(child.username));
  const today = usage?.days.find((d) => d.day === localDay());
  const state = memberState(status, today);
  const name = child.display_name || child.username;
  return (
    <Link
      to={memberPath(child.username)}
      aria-current={selected ? 'page' : undefined}
      className={`flex min-h-11 shrink-0 items-center gap-3 rounded-2xl border px-3 py-2 ${selected ? 'border-sky-400/40 bg-sky-400/10' : 'border-white/10 bg-white/5'}`}
      data-testid={`member-strip-${child.username}`}
    >
      <span className="relative">
        <UserAvatar username={child.username} avatar={child.avatar} name={name} size={36} />
        <span className={`absolute -bottom-0.5 -right-0.5 h-3 w-3 rounded-full ring-2 ring-slate-900 ${DOT[state]}`} aria-hidden />
      </span>
      <span className="min-w-0">
        <span className="block truncate text-sm font-semibold text-white">{name}</span>
        <span className="block text-[11px] text-slate-400" data-testid={`member-state-${child.username}`}>{STATE_LABELS[state]}</span>
      </span>
      {status && <TimeRing used={status.used_min} limit={status.limit_min} size={36} label={false} />}
    </Link>
  );
}

/** The family at a glance (Aura's dashboard): every child the viewer may see, scrolling sideways on a phone. */
export default function MemberStrip({ members, selected }: { members: FamilyRecord[]; selected: string }) {
  return (
    <nav className="-mx-1 flex gap-2 overflow-x-auto px-1 pb-1" aria-label="Your children" data-testid="member-strip">
      {members.map((c) => (
        <StripMember key={c.username} child={c} selected={c.username === selected} />
      ))}
    </nav>
  );
}
