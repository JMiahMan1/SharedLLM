import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { HandHeart, PenLine } from 'lucide-react';
import { api } from '../../services/api';
import ShareToChatSheet from '../bible/ShareToChatSheet';
import type { GuardianPrompt } from '../../types/api';

/**
 * Prayer and guidance (Phase 11 step 7): for each thing that happened in the
 * child's week, a prayer, a verse and a way to start talking. A parent can
 * turn a starter into a note, edit it, and send it themselves; nothing is
 * ever sent on its own.
 */
export default function GuidanceCard({ username, name }: { username: string; name: string }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['guardian-guidance', username],
    queryFn: () => api.getGuardianGuidance(username),
    retry: false,
  });
  const [draft, setDraft] = useState<{ prompt: GuardianPrompt; text: string } | null>(null);
  const [sharing, setSharing] = useState(false);

  return (
    <section className="glass-panel min-w-0 rounded-2xl border border-white/10 p-4 sm:p-5 space-y-3.5" data-testid="guardian-guidance">
      <div className="flex items-center justify-between gap-2 border-b border-white/[0.06] pb-2.5">
        <h2 className="flex items-center gap-2.5 text-sm font-semibold tracking-tight text-white">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border border-rose-400/20 bg-rose-400/10">
            <HandHeart size={16} className="text-rose-300" />
          </span>
          <span>Praying for {name}</span>
        </h2>
      </div>
      {isLoading ? (
        <div className="h-24 skeleton rounded-xl" aria-hidden />
      ) : isError || !data ? (
        <p className="rounded-xl border border-amber-400/20 bg-amber-400/5 p-3 text-xs text-amber-300">Guidance could not be loaded.</p>
      ) : data.prompts.length === 0 ? (
        <p className="rounded-xl border border-white/5 bg-white/[0.02] p-3 text-xs text-slate-400">
          No specific prayer prompts for {name} this week.
        </p>
      ) : (
        <ul className="space-y-3">
          {data.prompts.map((p) => (
            <li key={p.signal} className="rounded-xl border border-white/10 bg-white/[0.03] p-3.5 space-y-2.5" data-testid={`guidance-${p.signal}`}>
              <p className="text-xs font-semibold text-white">{p.title}</p>
              <p className="text-xs leading-relaxed text-slate-300">{p.prayer}</p>
              <blockquote className="rounded-r-lg border-l-2 border-amber-400/50 bg-amber-400/[0.06] px-3 py-1.5 text-[11px] italic text-amber-200/95">
                “{p.verse}” - <span className="not-italic font-medium text-amber-300/90">{p.ref}</span>
              </blockquote>
              <p className="rounded-lg bg-white/[0.04] px-2.5 py-2 text-[11px] text-slate-300">
                <span className="font-semibold text-slate-200">Try:</span> “{p.starter}”
              </p>
              <button
                type="button"
                onClick={() => setDraft({ prompt: p, text: p.starter })}
                className="glass-button min-h-11 px-3.5 text-xs font-semibold inline-flex items-center gap-1.5"
              >
                <PenLine size={13} /> Write {name} a note
              </button>
            </li>
          ))}
        </ul>
      )}

      {draft && (
        <div className="space-y-2.5 rounded-xl border border-sky-400/30 bg-sky-400/5 p-3.5" data-testid="guidance-draft">
          <label className="block text-xs text-slate-300">
            Your note to {name} (say it your way; it is only sent when you send it)
            <textarea
              rows={4}
              value={draft.text}
              onChange={(e) => setDraft({ ...draft, text: e.target.value })}
              className="mt-1.5 w-full rounded-xl border border-white/10 bg-slate-950/60 px-3 py-2 text-sm text-slate-100 focus:border-sky-400/50 focus:outline-none"
            />
          </label>
          <div className="flex flex-wrap gap-2">
            <button type="button" disabled={!draft.text.trim()} onClick={() => setSharing(true)} className="glass-button min-h-11 px-3.5 text-xs font-semibold disabled:opacity-50">
              Send in chat…
            </button>
            <button type="button" onClick={() => setDraft(null)} className="min-h-11 rounded-xl px-3.5 text-xs text-slate-400 hover:bg-white/5 hover:text-white">
              Discard
            </button>
          </div>
        </div>
      )}
      {draft && sharing && (
        <ShareToChatSheet
          reference={`Note to ${name}`}
          text={draft.text}
          heading={`Send your note to ${name}`}
          previewIcon="💌"
          buildMessage={(extra) => (extra.trim() ? `${draft.text}\n\n${extra.trim()}` : draft.text)}
          onClose={() => setSharing(false)}
          onSent={() => setDraft(null)}
        />
      )}
    </section>
  );
}
