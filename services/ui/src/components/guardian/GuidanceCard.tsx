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
    <section className="glass-panel rounded-2xl p-4 sm:p-5 space-y-3" data-testid="guardian-guidance">
      <h2 className="flex items-center gap-2 text-sm font-semibold text-white">
        <HandHeart size={16} className="text-rose-300" /> Praying for {name}
      </h2>
      {isLoading ? (
        <div className="h-20 skeleton rounded-xl" aria-hidden />
      ) : isError || !data ? (
        <p className="text-xs text-amber-300">Guidance could not be loaded.</p>
      ) : (
        <ul className="space-y-3">
          {data.prompts.map((p) => (
            <li key={p.signal} className="rounded-xl border border-white/5 bg-white/[0.03] p-3 space-y-1.5" data-testid={`guidance-${p.signal}`}>
              <p className="text-xs font-semibold text-white">{p.title}</p>
              <p className="text-xs text-slate-300">{p.prayer}</p>
              <p className="text-[11px] italic text-amber-200/90">“{p.verse}” - {p.ref}</p>
              <p className="text-[11px] text-slate-400">Try: “{p.starter}”</p>
              <button
                type="button"
                onClick={() => setDraft({ prompt: p, text: p.starter })}
                className="glass-button min-h-11 px-3 text-[11px] font-semibold flex items-center gap-1.5"
              >
                <PenLine size={12} /> Write {name} a note
              </button>
            </li>
          ))}
        </ul>
      )}

      {draft && (
        <div className="space-y-2 rounded-xl border border-sky-400/30 bg-sky-400/5 p-3" data-testid="guidance-draft">
          <label className="block text-[11px] text-slate-400">
            Your note to {name} (say it your way; it is only sent when you send it)
            <textarea
              rows={4}
              value={draft.text}
              onChange={(e) => setDraft({ ...draft, text: e.target.value })}
              className="mt-1 w-full rounded-lg border border-white/10 bg-white/5 px-2 py-1.5 text-sm text-slate-100"
            />
          </label>
          <div className="flex gap-2">
            <button type="button" disabled={!draft.text.trim()} onClick={() => setSharing(true)} className="glass-button min-h-11 px-3 text-xs font-semibold disabled:opacity-50">
              Send in chat…
            </button>
            <button type="button" onClick={() => setDraft(null)} className="min-h-11 px-3 text-xs text-slate-400 hover:text-white">
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
