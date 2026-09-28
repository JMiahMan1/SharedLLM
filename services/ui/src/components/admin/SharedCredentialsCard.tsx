import { useState, type FC } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Users } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import type { CredentialShares, ShareableService } from '../../types/api';

/**
 * Per-user credential sharing.
 *
 * A user's own credentials always win. When they have none, Identity hands
 * them the system default user's credentials for any service listed here — the
 * grant is per service, admin-only, revocable and recorded. See
 * docs/PER_USER_CREDENTIALS.md.
 */
const SHAREABLE_SERVICES: { key: ShareableService; label: string }[] = [
  { key: 'home_assistant', label: 'Home Assistant' },
  { key: 'music_assistant', label: 'Music Assistant' },
  { key: 'audiobookshelf', label: 'Audiobookshelf' },
  { key: 'nextcloud', label: 'Nextcloud' },
];

export const SharedCredentialsCard: FC<{ username: string; isAdmin: boolean; isSystemDefault: boolean }> = ({
  username,
  isAdmin,
  isSystemDefault,
}) => {
  const queryClient = useQueryClient();
  const [note, setNote] = useState('');

  const { data, isLoading, error } = useQuery({
    queryKey: ['credential-shares', username],
    queryFn: () => api.getCredentialShares(username),
    enabled: !!username,
  });

  const granted = new Set<ShareableService>(data?.services ?? []);

  const mutation = useMutation({
    mutationFn: (services: ShareableService[]) => api.putCredentialShares(username, services, note || undefined),
    onSuccess: (updated: CredentialShares) => {
      setNote('');
      queryClient.setQueryData(['credential-shares', username], updated);
      queryClient.invalidateQueries({ queryKey: ['me'] });
      toast.success('Shared service access updated');
    },
    onError: (err: unknown) => {
      toast.error(err instanceof Error ? err.message : 'Update failed');
    },
  });

  const toggle = (key: ShareableService) => {
    if (!isAdmin) return;
    const next = granted.has(key)
      ? (data?.services ?? []).filter((s) => s !== key)
      : [...(data?.services ?? []), key];
    mutation.mutate(next);
  };

  if (isLoading) {
    return (
      <div className="p-4 glass-card border-white/5 bg-white/5 rounded-xl text-xs text-slate-400">
        Loading shared service access…
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-4 glass-card border-red-500/20 bg-red-500/10 rounded-xl text-xs text-red-300">
        Could not load shared service access: {error instanceof Error ? error.message : 'unknown error'}
      </div>
    );
  }

  return (
    <div className="p-4 glass-card border-white/5 bg-white/5 rounded-xl space-y-3" data-testid="shared-credentials">
      <div className="flex items-center gap-2">
        <Users size={16} className="text-purple-400" />
        <p className="text-xs font-bold text-white">Shared Service Access</p>
      </div>
      <p className="text-[10px] text-slate-500 leading-relaxed">
        Your own credentials always win. If you have none for a service, Jarvis can use the system default
        user's credentials for it
        {data?.shared_owner ? (
          <>
            {' '}
            (<span className="text-slate-400 font-mono">{data.shared_owner}</span>)
          </>
        ) : null}
        {isSystemDefault ? ' — you are the system default user, so these are your own credentials.' : '.'}
      </p>

      <ul className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        {SHAREABLE_SERVICES.map(({ key, label }) => {
          const on = granted.has(key);
          return (
            <li key={key}>
              <label className="flex items-center justify-between gap-3 p-2 rounded-lg bg-black/20 border border-white/5">
                <span className={`text-xs ${on ? 'text-emerald-300' : 'text-slate-400'}`}>{label}</span>
                <input
                  type="checkbox"
                  aria-label={`Use shared ${label} credentials`}
                  checked={on}
                  disabled={!isAdmin || mutation.isPending}
                  onChange={() => toggle(key)}
                  className="h-4 w-4 accent-purple-500 disabled:opacity-40"
                />
              </label>
            </li>
          );
        })}
      </ul>

      {data?.granted_by ? (
        <p className="text-[9px] text-slate-500">
          Last changed by <span className="font-mono">{data.granted_by}</span>
          {data.granted_at ? ` on ${new Date(data.granted_at).toLocaleString()}` : ''}
          {data.note ? ` — “${data.note}”` : ''}
        </p>
      ) : null}

      {isAdmin ? (
        <div className="flex items-center gap-2">
          <input
            type="text"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Note (optional, e.g. guest tablet)"
            aria-label="Grant note"
            className="glass-input flex-1 text-xs py-2 bg-black/20"
          />
        </div>
      ) : (
        <p className="text-[9px] text-slate-500">An admin grants or revokes shared access.</p>
      )}
    </div>
  );
};
