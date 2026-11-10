import { useEffect, useRef } from 'react';
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
      className={`flex min-h-11 shrink-0 snap-start items-center gap-2.5 rounded-2xl border py-1.5 pl-1.5 pr-2.5 transition-colors sm:gap-3 sm:px-3 sm:py-2 ${
        selected ? 'border-sky-400/40 bg-sky-400/10' : 'border-white/10 bg-white/5 hover:bg-white/10'
      }`}
      data-testid={`member-strip-${child.username}`}
    >
      <span className="relative">
        <UserAvatar username={child.username} avatar={child.avatar} name={name} size={32} />
        <span className={`absolute -bottom-0.5 -right-0.5 h-3 w-3 rounded-full ring-2 ring-slate-900 ${DOT[state]}`} aria-hidden />
      </span>
      <span className="min-w-0 max-w-[7.5rem]">
        <span className="block truncate text-sm font-semibold text-white">{name}</span>
        <span className="block text-[11px] text-slate-400" data-testid={`member-state-${child.username}`}>{STATE_LABELS[state]}</span>
      </span>
      {status && <TimeRing used={status.used_min} limit={status.limit_min} size={30} label={false} />}
    </Link>
  );
}

/** The family at a glance (Aura's dashboard): every child the viewer may see, scrolling sideways on a phone. */
export default function MemberStrip({ members, selected }: { members: FamilyRecord[]; selected: string }) {
  // The children first (they are who the page is for), then the other parent; otherwise the family's own order.
  const ordered = [...members].sort((a, b) => Number(Boolean(b.is_minor)) - Number(Boolean(a.is_minor)));
  const ref = useRef<HTMLElement>(null);
  // The one being shown stays in view on a phone, where the strip scrolls sideways.
  useEffect(() => {
    const strip = ref.current;
    const current = strip?.querySelector<HTMLElement>('[aria-current="page"]');
    if (!strip || !current) return;
    if (current.offsetLeft < strip.scrollLeft || current.offsetLeft + current.offsetWidth > strip.scrollLeft + strip.clientWidth) {
      strip.scrollTo({ left: Math.max(0, current.offsetLeft - 8) });
    }
  }, [selected]);
  if (ordered.length < 2) return null;
  return (
    <nav
      ref={ref}
      className="-mx-1 flex snap-x gap-2 overflow-x-auto px-1 pb-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      aria-label="Your children"
      data-testid="member-strip"
    >
      {ordered.map((c) => (
        <StripMember key={c.username} child={c} selected={c.username === selected} />
      ))}
    </nav>
  );
}
