import { useState } from 'react';
import { BarChart3, ChevronDown, ChevronUp } from 'lucide-react';

export interface TalkPollOption {
  id: number;
  label: string;
  numVotes?: number;
}

export interface TalkPoll {
  id: number;
  question: string;
  options?: TalkPollOption[];
  status?: number;
}

/**
 * Open polls pinned above the feed, each option a tappable bar that fills with
 * its share of the votes -- the WhatsApp/Telegram poll card.
 */
export default function PollStrip({ polls, onVote }: { polls: TalkPoll[]; onVote: (pollId: number, optionId: number) => void }) {
  // One slim pinned line, like a pinned message; open it to vote.
  const [open, setOpen] = useState(false);
  if (polls.length === 0) return null;
  const shown = polls.slice(0, 3);

  return (
    <div className="shrink-0 border-b border-white/5 bg-slate-900/80" data-testid="poll-list">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full min-h-11 items-center gap-2 px-3 text-left text-[13px] font-semibold text-slate-200"
      >
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-fuchsia-500/20">
          <BarChart3 size={14} className="text-fuchsia-300" />
        </span>
        <span className="min-w-0 flex-1 truncate">
          {polls.length === 1 ? shown[0].question : `${polls.length} open polls · ${shown[0].question}`}
        </span>
        <span className="shrink-0 rounded-full bg-fuchsia-500/20 px-2 py-0.5 text-[11px] text-fuchsia-200">{open ? 'Hide' : 'Vote'}</span>
        <span className="text-slate-500">{open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}</span>
      </button>
      {open && (
        <div className="max-h-56 space-y-2 overflow-y-auto px-3 pb-3">
          {shown.map((poll) => {
            const total = (poll.options || []).reduce((sum, o) => sum + (o.numVotes ?? 0), 0);
            return (
              <div key={poll.id} className="rounded-2xl border border-white/10 bg-white/5 p-3">
                <p className="text-sm font-semibold text-slate-100">{poll.question}</p>
                <div className="mt-2 space-y-1.5">
                  {(poll.options || []).map((option) => {
                    const votes = option.numVotes ?? 0;
                    const pct = total > 0 ? Math.round((votes / total) * 100) : 0;
                    return (
                      <button
                        key={option.id}
                        type="button"
                        onClick={() => onVote(poll.id, option.id)}
                        className="relative flex min-h-10 w-full items-center overflow-hidden rounded-xl border border-white/10 px-3 text-left text-[13px] text-slate-100"
                      >
                        <span
                          aria-hidden
                          className="absolute inset-y-0 left-0 bg-purple-500/25 transition-all"
                          style={{ width: `${pct}%` }}
                        />
                        <span className="relative flex-1 truncate">{option.label}</span>
                        <span className="relative ml-2 text-xs text-slate-300">
                          {votes} · {pct}%
                        </span>
                      </button>
                    );
                  })}
                </div>
                <p className="mt-1.5 text-[11px] text-slate-500">
                  {total} vote{total === 1 ? '' : 's'}
                </p>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
