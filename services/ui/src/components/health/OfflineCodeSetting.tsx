import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { KeyRound } from 'lucide-react';
import { isAxiosError } from 'axios';
import { api } from '../../services/api';

/**
 * A parent's offline unlock code (Phase 11, tamper resistance): 6 to 12
 * digits that open Jarvis's protection on their children's phones with no
 * connection. The server keeps only a verifier; a phone learns it at the next
 * online parent sign-in on it, and an offline unlock is reported to both
 * parents once the phone is back online.
 */
export default function OfflineCodeSetting() {
  const queryClient = useQueryClient();
  const { data } = useQuery({ queryKey: ['guard-offline-code'], queryFn: () => api.getOfflineCode(), retry: false });
  const [code, setCode] = useState('');
  const done = () => {
    setCode('');
    void queryClient.invalidateQueries({ queryKey: ['guard-offline-code'] });
  };
  const save = useMutation({ mutationFn: () => api.setOfflineCode(code), onSuccess: done });
  const clear = useMutation({ mutationFn: () => api.clearOfflineCode(), onSuccess: done });

  if (!data?.can_set) return null;
  const valid = /^\d{6,12}$/.test(code);
  const failure = [save.error, clear.error].find(Boolean);
  const failureText = isAxiosError(failure) ? (failure.response?.data as { detail?: string })?.detail || 'That did not save.' : failure ? 'That did not save.' : null;

  return (
    <div className="space-y-2 border-t border-white/5 pt-3" data-testid="offline-code">
      <div className="flex items-center gap-2">
        <KeyRound size={14} className="text-amber-300" />
        <h4 className="text-xs font-semibold text-white">Your offline unlock code</h4>
      </div>
      <p className="text-[11px] text-slate-400">
        {data.set
          ? 'Set. It works on your children’s phones when they have no connection; a phone picks up a new code the next time you sign in on it while online.'
          : 'For when a child’s phone has no connection: 6 to 12 digits, yours only. Sign in once on each child’s phone while online so it learns the code.'}
      </p>
      <div className="flex items-center gap-2">
        <input
          type="password"
          inputMode="numeric"
          autoComplete="new-password"
          aria-label="New offline unlock code"
          placeholder={data.set ? 'New code' : '6–12 digits'}
          value={code}
          onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 12))}
          className="w-32 rounded-lg border border-white/10 bg-white/5 px-2 py-1.5 text-sm text-slate-100"
        />
        <button
          type="button"
          disabled={!valid || save.isPending}
          onClick={() => save.mutate()}
          className="glass-button min-h-11 px-3 text-xs font-semibold disabled:opacity-50"
        >
          {data.set ? 'Change code' : 'Set code'}
        </button>
        {data.set && (
          <button type="button" disabled={clear.isPending} onClick={() => clear.mutate()} className="min-h-11 px-2 text-xs text-slate-400 hover:text-white">
            Remove
          </button>
        )}
      </div>
      {failureText && <p className="text-xs text-amber-300">{failureText}</p>}
    </div>
  );
}
