import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { AlertTriangle, RefreshCw } from 'lucide-react';
import { muted, secondaryButton, text } from './ui';

/**
 * The states every media data view has (§4.5): skeletons at the real size
 * while loading, an inline error naming the service with Retry, an empty
 * message with a way forward, and a notice when one service is down but the
 * rest still answered.
 */

/** Placeholder tiles shaped exactly like the cards they stand in for (UX-33). */
export function CardSkeletons({ count = 6, label = 'Loading' }: { count?: number; label?: string }) {
  return (
    <div className="flex gap-3 overflow-hidden sm:gap-4" role="status" aria-label={label}>
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="w-[clamp(132px,38vw,184px)] shrink-0">
          <div className="skeleton aspect-square w-full rounded-[var(--radius-card)]" />
          <div className="skeleton mt-2 h-4 w-3/4 rounded" />
          <div className="skeleton mt-1.5 h-3 w-1/2 rounded" />
        </div>
      ))}
    </div>
  );
}

export function RowSkeletons({ count = 6, label = 'Loading' }: { count?: number; label?: string }) {
  return (
    <div className="space-y-1" role="status" aria-label={label}>
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="flex min-h-14 items-center gap-3 p-2">
          <div className="skeleton h-11 w-11 shrink-0 rounded-lg" />
          <div className="flex-1 space-y-1.5">
            <div className="skeleton h-4 w-1/2 rounded" />
            <div className="skeleton h-3 w-1/3 rounded" />
          </div>
        </div>
      ))}
    </div>
  );
}

export function ErrorRetry({ title, detail, onRetry }: { title: string; detail?: string | null; onRetry?: () => void }) {
  return (
    <div role="alert" className="glass-panel flex flex-col items-start gap-2 rounded-[var(--radius-card)] p-4 sm:flex-row sm:items-center">
      <AlertTriangle size={18} className="shrink-0 text-[var(--site-accent-text)]" aria-hidden />
      <div className="min-w-0 flex-1">
        <p className={`text-sm font-medium ${text}`}>{title}</p>
        {detail ? <p className={`mt-0.5 text-sm ${muted}`}>{detail}</p> : null}
      </div>
      {onRetry ? (
        <button type="button" onClick={onRetry} className={secondaryButton}>
          <RefreshCw size={14} aria-hidden />Retry
        </button>
      ) : null}
    </div>
  );
}

export function EmptyState({
  title, detail, action,
}: {
  title: string;
  detail?: string;
  action?: { label: string; to: string };
}) {
  return (
    <div className="glass-panel rounded-[var(--radius-card)] p-6 text-center">
      <p className={`text-base font-medium ${text}`}>{title}</p>
      {detail ? <p className={`mx-auto mt-1 max-w-md text-sm ${muted}`}>{detail}</p> : null}
      {action ? (
        <Link to={action.to} className={`${secondaryButton} mt-3`}>{action.label}</Link>
      ) : null}
    </div>
  );
}

/** One service is down; what the other one sent is still shown. */
export function PartialNotice({ children }: { children: ReactNode }) {
  return (
    <p role="status" className={`flex items-center gap-2 rounded-xl bg-[var(--site-card-bg)] px-3 py-2 text-sm ${muted}`}>
      <AlertTriangle size={14} className="shrink-0" aria-hidden />
      <span>{children}</span>
    </p>
  );
}
