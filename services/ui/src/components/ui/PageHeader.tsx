import type { ReactNode } from 'react';
import type { LucideIcon } from 'lucide-react';

/**
 * The header every home page shares (Rooms, Climate, Cameras, Energy, History,
 * Automations, Schedules, Devices): the icon tile, title and one line of what
 * the page is for, as Health and Guardian already do, with the page's actions
 * on the right. On a phone the actions wrap under the title instead of
 * squeezing it.
 */
export default function PageHeader({
  icon: Icon, title, subtitle, tone = 'text-purple-400', actions, children,
}: {
  icon: LucideIcon;
  title: string;
  subtitle?: ReactNode;
  /** Text colour class for the icon. */
  tone?: string;
  actions?: ReactNode;
  /** Anything under the title row (tabs, status chips). */
  children?: ReactNode;
}) {
  return (
    <header className="glass-panel rounded-2xl border border-white/10 p-4 sm:p-5 space-y-3" data-testid="page-header">
      <div className="flex flex-wrap items-center gap-3">
        <div className="shrink-0 rounded-xl border border-white/10 bg-white/5 p-2.5" aria-hidden>
          <Icon size={22} className={tone} />
        </div>
        <div className="min-w-0 flex-1">
          <h1 className="text-xl font-bold text-white sm:text-2xl">{title}</h1>
          {subtitle && <p className="mt-0.5 text-xs text-slate-400">{subtitle}</p>}
        </div>
        {actions && <div className="flex w-full flex-wrap gap-2 sm:w-auto sm:justify-end">{actions}</div>}
      </div>
      {children}
    </header>
  );
}

/** A 44px secondary action for a page header or card (link or button styling). */
export const HEADER_ACTION =
  'inline-flex min-h-11 min-w-11 items-center justify-center gap-1.5 rounded-xl border border-white/10 bg-white/5 px-3 text-sm text-slate-200 hover:bg-white/10 disabled:opacity-50';
