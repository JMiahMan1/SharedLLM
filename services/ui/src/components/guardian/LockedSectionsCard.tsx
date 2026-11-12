import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Lock } from 'lucide-react';
import { isAxiosError } from 'axios';
import { api } from '../../services/api';
import { statusQuery } from '../../lib/memberPage';
import MemberCard from '../member/MemberCard';
import type { ScreenRules } from '../../types/api';

/**
 * "While locked, Jarvis can use": the parts of Jarvis a child can still open
 * from the lock screen's "Open Jarvis" button while the rest of the phone is
 * locked. The choices, their wording and the defaults are the server's
 * (``locked_section_choices``); what is chosen now is ``locked_sections``.
 * Chat is always on: it is what the lock's own button opens.
 */
export default function LockedSectionsCard({ username, name }: { username: string; name: string }) {
  const queryClient = useQueryClient();
  const { data: status, isLoading, isError } = useQuery(statusQuery(username));
  const [edited, setEdited] = useState<string[] | null>(null);
  const choices = status?.locked_section_choices || [];
  const chosen = edited ?? status?.locked_sections ?? [];
  const save = useMutation({
    mutationFn: (rules: ScreenRules) => api.setScreenRules(username, rules),
    onSuccess: () => {
      setEdited(null);
      void queryClient.invalidateQueries({ queryKey: ['screen-time', username] });
    },
  });
  const failure = isAxiosError(save.error)
    ? (save.error.response?.data as { detail?: string })?.detail || 'That did not save.'
    : save.error ? 'That did not save.' : null;

  const toggle = (id: string, on: boolean) => {
    const next = on ? [...chosen, id] : chosen.filter((c) => c !== id);
    // Keep the server's order, so the child's navigation reads the same way.
    setEdited(choices.map((c) => c.id).filter((c) => next.includes(c)));
  };

  const submit = () => {
    if (!status || !edited) return;
    // The full rules go back each time: the server replaces them as a whole
    // (a pause is kept there on its own).
    const rest: ScreenRules = { ...status.rules };
    delete rest.paused;
    delete rest.paused_until;
    save.mutate({ ...rest, locked_sections: edited });
  };

  return (
    <MemberCard
      title="While locked, Jarvis can use"
      icon={Lock}
      tone="text-sky-300"
      testId="locked-sections-card"
      isLoading={isLoading}
      error={isError ? 'This could not be loaded.' : null}
      empty={status && !status.can_manage ? 'Only their parents can change this.' : status && choices.length === 0 ? 'This needs a newer server.' : null}
    >
      {status?.can_manage && choices.length > 0 && (
        <div className="space-y-3 text-sm text-slate-200">
          <p className="text-xs leading-relaxed text-slate-400">
            When {name}'s phone is locked, "Open Jarvis" on the lock screen opens only these. Everything else, and every
            other app, stays locked. Their devices keep their usual permissions.
          </p>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {choices.map((c) => (
              <label
                key={c.id}
                className="flex min-h-11 cursor-pointer items-center gap-3 rounded-xl border border-white/10 bg-white/5 px-3 py-2"
              >
                <input
                  type="checkbox"
                  checked={c.core || chosen.includes(c.id)}
                  disabled={c.core}
                  onChange={(e) => toggle(c.id, e.target.checked)}
                  data-testid={`locked-section-${c.id}`}
                />
                <span className="flex-1">{c.label}</span>
                {c.core && <span className="text-[11px] text-slate-400">Always on</span>}
              </label>
            ))}
          </div>
          {failure && <p className="text-xs text-rose-300" role="alert">{failure}</p>}
          <button
            type="button"
            onClick={submit}
            disabled={!edited || save.isPending}
            className="glass-button min-h-11 rounded-xl px-4 text-sm font-semibold text-sky-100 disabled:opacity-40"
            data-testid="locked-sections-save"
          >
            {save.isPending ? 'Saving...' : 'Save'}
          </button>
        </div>
      )}
    </MemberCard>
  );
}
