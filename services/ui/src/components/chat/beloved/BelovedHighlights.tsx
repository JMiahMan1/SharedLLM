import { useState, type ReactNode } from 'react';
import { Heart } from 'lucide-react';
import { todayPhrases, type BelovedActivityEntry } from '../../../lib/beloved/activity';
import type { BelovedTip, BelovedTipTarget } from '../../../lib/beloved/tips';
import { seenRecord } from './seen';

/**
 * Small, content-free pieces of the home page and notes: the spouse's activity
 * today, the day's feature tip, the "Read" marker and the one-tap heart back.
 */

export function TodayStrip({
  activity,
  me,
  spouse,
  spouseName,
  now,
}: {
  activity: readonly BelovedActivityEntry[];
  me: string;
  /** The spouse's username, the only identity the activity log holds. */
  spouse: string;
  spouseName: string;
  now?: Date;
}) {
  const phrases = todayPhrases(activity, me, spouse, now);
  if (!phrases.length) return null;
  return (
    <section className="rounded-2xl border border-rose-300/15 bg-rose-500/[0.07] p-3" data-testid="beloved-today-strip" aria-label={`Today with ${spouseName}`}>
      <p className="text-xs font-semibold uppercase tracking-wide text-rose-200/80">Today with {spouseName}</p>
      <ul className="mt-1 flex flex-wrap gap-x-2 gap-y-0.5 text-sm text-slate-100">
        {phrases.map((phrase, i) => (
          <li key={`${i}-${phrase}`} className="flex min-w-0 items-center gap-2" data-testid="beloved-today-item">
            {i > 0 && <span aria-hidden className="text-rose-300/60">·</span>}
            <span className="min-w-0">{phrase}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function TipCard({
  tip,
  onOpen,
}: {
  tip: BelovedTip;
  onOpen: (target: BelovedTipTarget) => void;
}) {
  return (
    <section className="rounded-2xl border border-amber-200/15 bg-amber-400/[0.06] p-3" data-testid="beloved-tip" aria-label="Did you know?">
      <p className="text-sm leading-relaxed text-amber-50">{tip.text}</p>
      <button
        type="button"
        onClick={() => onOpen(tip.target)}
        data-testid="beloved-tip-link"
        className="mt-1 flex min-h-11 items-center rounded-xl px-0.5 text-sm font-semibold text-amber-200 underline underline-offset-2 hover:text-amber-100"
      >
        {tip.link}
      </button>
    </section>
  );
}

export function HeartBackButton({
  onSend,
  busy = false,
}: {
  onSend: () => void;
  busy?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onSend}
      disabled={busy}
      aria-label="Send a heart back"
      title="Send a heart back"
      data-testid="beloved-heart-back"
      className="flex min-h-11 shrink-0 items-center justify-center gap-1.5 rounded-full border border-rose-300/30 bg-rose-500/15 px-3 text-xs font-semibold text-rose-100 hover:bg-rose-500/25 disabled:opacity-50"
    >
      <Heart size={14} className="fill-rose-300 text-rose-300" aria-hidden /> Send a heart back
    </button>
  );
}

export function ReadMarker({ read }: { read: boolean }) {
  if (!read) return null;
  return <span className="shrink-0 text-[11px] font-semibold text-emerald-200" data-testid="beloved-read">Read</span>;
}

/**
 * A spouse's note, prayer or idea is a preview until it is opened. The first
 * opening sends one metadata-only "read" signal for that record; closing and
 * reopening, refetches and tab switches never send another one in this session.
 */
export function ExpandableSpouseItem({
  token,
  kind,
  recordId,
  children,
  testId,
}: {
  token: string;
  kind: 'note' | 'prayer' | 'idea';
  recordId: string;
  children: ReactNode;
  testId?: string;
}) {
  const [expanded, setExpanded] = useState(false);
  if (expanded) return <span data-testid={testId}>{children}</span>;
  return (
    <span data-testid={testId}>
      <span className="line-clamp-2">{children}</span>
      <button
        type="button"
        onClick={() => {
          setExpanded(true);
          seenRecord(token, kind, recordId);
        }}
        data-testid="beloved-read-more"
        className="mt-1 flex min-h-11 items-center text-xs font-semibold text-rose-200 underline underline-offset-2 hover:text-rose-100"
      >
        Read more
      </button>
    </span>
  );
}
