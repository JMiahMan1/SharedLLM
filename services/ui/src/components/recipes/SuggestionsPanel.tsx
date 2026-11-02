import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { Check, X } from 'lucide-react';
import { api } from '../../services/api';
import type { CookbookSuggestion } from '../../types/api';

/** Changes someone suggested to a recipe: the owner (or an admin) approves or turns each down; the sender can withdraw it. */
export default function SuggestionsPanel({ suggestions, onApproved }: { suggestions: CookbookSuggestion[]; onApproved: (recipePath: string) => void }) {
  const queryClient = useQueryClient();
  const [resolving, setResolving] = useState<string | null>(null);

  const resolve = async (suggestion: CookbookSuggestion, decision: 'approve' | 'reject' | 'withdraw') => {
    setResolving(suggestion.path);
    try {
      const res = await api.cookbookResolve(suggestion.path, decision);
      if (res.status === 'SUCCESS') {
        toast.success(res.message || 'Done');
        void queryClient.invalidateQueries({ queryKey: ['recipe-suggestions'] });
        if (decision === 'approve') onApproved(suggestion.recipe);
      } else {
        toast.error(res.message || 'Could not do that');
      }
    } catch {
      toast.error('Could not do that');
    } finally {
      setResolving(null);
    }
  };

  return (
    <div className="glass-panel space-y-3 rounded-3xl border border-white/5 p-4" data-testid="recipe-suggestions">
      <h2 className="text-sm font-semibold text-slate-200">Suggested changes</h2>
      {suggestions.map((suggestion) => (
        <div key={suggestion.path} className="space-y-2 rounded-2xl border border-white/10 bg-black/20 p-3">
          <p className="text-xs text-slate-300">
            <span className="font-semibold text-slate-100">{suggestion.recipe_title || suggestion.recipe}</span>
            {' · '}
            {suggestion.mine ? `you suggested this; waiting for ${suggestion.owner_label}` : `from ${suggestion.by}`}
          </p>
          {suggestion.message && <p className="text-xs italic text-slate-400">“{suggestion.message}”</p>}
          <div className="grid gap-2 sm:grid-cols-2">
            <details className="rounded-lg border border-white/10 p-2" open={!suggestion.current}>
              <summary className="cursor-pointer text-[11px] text-slate-400">Now</summary>
              <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap font-sans text-[11px] text-slate-400">{suggestion.current ?? 'This recipe has since been deleted.'}</pre>
            </details>
            <details className="rounded-lg border border-emerald-400/30 p-2" open>
              <summary className="cursor-pointer text-[11px] text-emerald-200">Suggested</summary>
              <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap font-sans text-[11px] text-slate-200">{suggestion.content}</pre>
            </details>
          </div>
          <div className="flex flex-wrap gap-2">
            {suggestion.can_review && suggestion.current !== null && (
              <button type="button" onClick={() => void resolve(suggestion, 'approve')} disabled={resolving === suggestion.path} className="glass-button min-h-11 px-3 text-xs disabled:opacity-60">
                <Check size={14} /> Approve
              </button>
            )}
            {suggestion.can_review && (
              <button type="button" onClick={() => void resolve(suggestion, 'reject')} disabled={resolving === suggestion.path} className="glass-button min-h-11 px-3 text-xs disabled:opacity-60">
                <X size={14} /> Turn down
              </button>
            )}
            {suggestion.mine && !suggestion.can_review && (
              <button type="button" onClick={() => void resolve(suggestion, 'withdraw')} disabled={resolving === suggestion.path} className="glass-button min-h-11 px-3 text-xs disabled:opacity-60">
                <X size={14} /> Withdraw
              </button>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
