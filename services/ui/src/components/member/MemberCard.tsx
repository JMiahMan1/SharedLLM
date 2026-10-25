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
    <section
      className={`glass-panel min-w-0 rounded-2xl p-4 sm:p-5 space-y-3 ${wide ? 'lg:col-span-2' : ''}`}
      data-testid={testId}
      aria-label={title}
    >
      <div className="flex items-center justify-between gap-2">
        <h3 className="flex min-w-0 items-center gap-2 text-sm font-semibold text-white">
          <Icon size={16} className={`shrink-0 ${tone}`} /> <span className="truncate">{title}</span>
        </h3>
        {aside && <div className="shrink-0 text-[11px] text-slate-400">{aside}</div>}
      </div>
      {isLoading ? (
        <div className="h-24 skeleton rounded-xl" aria-hidden data-testid={`${testId}-loading`} />
      ) : error ? (
        <p className="text-xs text-amber-300" data-testid={`${testId}-error`}>{error}</p>
      ) : empty ? (
        <p className="text-xs text-slate-400" data-testid={`${testId}-empty`}>{empty}</p>
      ) : (
        children
      )}
    </section>
  );
}
