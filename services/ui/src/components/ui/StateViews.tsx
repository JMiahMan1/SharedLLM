import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { AlertTriangle, RefreshCw } from 'lucide-react';
import { HEADER_ACTION } from './PageHeader';

/**
 * Loading, error and empty states for the home pages, so each one looks and
 * reads the same: skeletons the size of the cards they stand in for, an error
 * that says what failed with a Try again, and an empty state that says where
 * to go next with a button that goes there.
 */

/** Placeholder cards in the page's grid while it loads. */
export function CardSkeletons({ count = 4, label = 'Loading', className = 'grid gap-4 sm:grid-cols-2 xl:grid-cols-3', height = 'h-40' }: {
  count?: number; label?: string; className?: string; height?: string;
}) {
  return (
    <div className={className} role="status" aria-label={label} data-testid="card-skeletons">
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="glass-card rounded-2xl p-4 space-y-3">
          <div className="skeleton h-5 w-1/3 rounded" />
          <div className={`skeleton w-full rounded-xl ${height}`} />
        </div>
      ))}
    </div>
  );
}

export function ErrorRetry({ title, detail, onRetry }: { title: string; detail?: string | null; onRetry?: () => void }) {
  return (
    <div role="alert" className="glass-panel flex flex-col items-start gap-3 rounded-2xl border border-red-500/20 p-4 sm:flex-row sm:items-center">
      <AlertTriangle size={18} className="shrink-0 text-red-400" aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-red-200">{title}</p>
        {detail ? <p className="mt-0.5 text-sm text-slate-400">{detail}</p> : null}
      </div>
      {onRetry ? (
        <button type="button" onClick={onRetry} className={HEADER_ACTION}>
          <RefreshCw size={14} aria-hidden />Try again
        </button>
      ) : null}
    </div>
  );
}

export interface EmptyAction {
  label: string;
  /** A page to go to, or a handler. */
  to?: string;
  onClick?: () => void;
}

export function EmptyState({ icon, title, detail, action, children }: {
  icon?: ReactNode; title: string; detail?: ReactNode; action?: EmptyAction | null; children?: ReactNode;
}) {
  return (
    <div className="glass-panel rounded-2xl border border-white/10 p-6 text-center" data-testid="empty-state">
      {icon && <div className="mx-auto mb-2 flex justify-center text-slate-500" aria-hidden>{icon}</div>}
      <p className="text-base font-medium text-slate-100">{title}</p>
      {detail ? <p className="mx-auto mt-1 max-w-md text-sm text-slate-400">{detail}</p> : null}
      {action?.to && <Link to={action.to} className={`${HEADER_ACTION} mt-3`}>{action.label}</Link>}
      {action && !action.to && action.onClick && (
        <button type="button" onClick={action.onClick} className={`${HEADER_ACTION} mt-3`}>{action.label}</button>
      )}
      {children}
    </div>
  );
}
