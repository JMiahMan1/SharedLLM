import type { ReactNode } from 'react';
import type { LucideIcon } from 'lucide-react';

interface MemberCardProps {
  title: string;
  icon: LucideIcon;
  /** Tailwind text colour for the icon. */
  tone?: string;
  testId: string;
  /** Right side of the header: a range, a total, a small control. */
  aside?: ReactNode;
  /** Spans both columns of the desktop grid. */
  wide?: boolean;
  isLoading?: boolean;
  /** Shown instead of the body when the data could not be loaded. */
  error?: string | null;
  /** Shown instead of the body when there is nothing to show (never invented). */
  empty?: string | null;
  children?: ReactNode;
}

/**
 * One card of the family member page, with the loading, error and empty
 * states every data view on the page has. Cards reused from elsewhere (the
 * recap, guidance, journal, controls) bring their own.
 */
export default function MemberCard({ title, icon: Icon, tone = 'text-sky-300', testId, aside, wide, isLoading, error, empty, children }: MemberCardProps) {
  return (
    // A wide card spans every column the grid has. `col-span-2` in a grid that
    // is still one column (the member page below lg) made a second, implicit
    // column and crushed the cards beside it to a sliver.
    <section
      className={`glass-panel min-w-0 rounded-2xl border border-white/10 p-3.5 sm:p-5 space-y-3 ${wide ? 'md:col-span-full' : ''}`}
      data-testid={testId}
      aria-label={title}
    >
      <div className="flex items-center justify-between gap-2.5 border-b border-white/[0.06] pb-2.5">
        <h3 className="flex min-w-0 items-center gap-2.5 text-sm font-semibold tracking-tight text-white">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border border-white/10 bg-white/[0.04]">
            <Icon size={16} className={`shrink-0 ${tone}`} />
          </span>
          <span className="truncate">{title}</span>
        </h3>
        {aside && <div className="shrink-0 text-[11px] font-medium text-slate-300">{aside}</div>}
      </div>
      {isLoading ? (
        <div className="h-24 skeleton rounded-xl" aria-hidden data-testid={`${testId}-loading`} />
      ) : error ? (
        <p className="rounded-xl border border-amber-400/20 bg-amber-400/5 p-3 text-xs text-amber-300" data-testid={`${testId}-error`}>{error}</p>
      ) : empty ? (
        <p className="rounded-xl border border-white/5 bg-white/[0.02] p-3 text-xs text-slate-400" data-testid={`${testId}-empty`}>{empty}</p>
      ) : (
        children
      )}
    </section>
  );
}
