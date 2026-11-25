import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { ChevronRight } from 'lucide-react';

/**
 * One stat on the profile page, and a way into it: every stat is a link to
 * the page that already does that thing (the reader, the quiz, a recipe, a
 * game). At least 44px tall, reachable by keyboard, with a visible focus ring
 * and a chevron so it reads as tappable.
 */
export function StatTile({ to, value, label, hint, accent = 'text-white', testId, ariaLabel }: {
  to: string;
  value: ReactNode;
  label: string;
  hint?: ReactNode;
  /** Tailwind text colour for the value. */
  accent?: string;
  testId?: string;
  ariaLabel?: string;
}) {
  return (
    <Link
      to={to}
      data-testid={testId}
      aria-label={ariaLabel}
      className="group flex min-h-11 min-w-0 items-center gap-2 rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2.5 transition-colors hover:border-white/20 hover:bg-white/[0.07] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-purple-400/70"
    >
      <span className="min-w-0 flex-1">
        <span className={`block truncate text-lg font-semibold leading-tight tabular-nums ${accent}`}>{value}</span>
        <span className="block truncate text-[11px] text-slate-400">{label}</span>
        {hint ? <span className="block truncate text-[11px] text-slate-500">{hint}</span> : null}
      </span>
      <ChevronRight size={16} aria-hidden className="shrink-0 text-slate-500 transition-transform group-hover:translate-x-0.5 group-hover:text-slate-300" />
    </Link>
  );
}

/** A row in a list of things (a recipe, a game) that opens that thing. */
export function StatRow({ to, title, detail, badge, testId, ariaLabel }: {
  to: string;
  title: ReactNode;
  detail?: ReactNode;
  badge?: ReactNode;
  testId?: string;
  ariaLabel?: string;
}) {
  return (
    <Link
      to={to}
      data-testid={testId}
      aria-label={ariaLabel}
      className="group flex min-h-11 min-w-0 items-center gap-3 rounded-xl px-2.5 py-2 transition-colors hover:bg-white/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-purple-400/70"
    >
      {badge ? <span className="shrink-0">{badge}</span> : null}
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm text-slate-100">{title}</span>
        {detail ? <span className="block truncate text-[11px] text-slate-400">{detail}</span> : null}
      </span>
      <ChevronRight size={16} aria-hidden className="shrink-0 text-slate-500 group-hover:text-slate-300" />
    </Link>
  );
}

/** A grid of tiles that stays two across on the smallest phone. */
export function TileGrid({ children }: { children: ReactNode }) {
  return <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">{children}</div>;
}
