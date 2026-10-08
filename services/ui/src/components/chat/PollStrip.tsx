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
  const [open, setOpen] = useState(true);
  if (polls.length === 0) return null;
  const shown = polls.slice(0, 3);

  return (
    <div className="shrink-0 border-b border-white/5 bg-white/[0.02]" data-testid="poll-list">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-4 py-2 text-left text-xs font-semibold text-slate-300"
      >
        <BarChart3 size={13} className="text-fuchsia-300" />
        {polls.length === 1 ? 'Poll' : `${polls.length} polls`}
        {!open && <span className="truncate font-normal text-slate-400">· {shown[0].question}</span>}
        <span className="ml-auto text-slate-500">{open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}</span>
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
