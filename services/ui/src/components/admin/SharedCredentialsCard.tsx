import { useState, type FC } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { KeyRound, Users } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import { useIntegrations } from '../../hooks/useIntegrations';
import type { CredentialShares, ShareableService } from '../../types/api';

/**
 * Per-user credential sharing.
 *
 * A user's own credentials always win. When they have none, Identity hands
 * them the system default user's credentials for any service listed here. The
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
  const [tokenResult, setTokenResult] = useState<string>('');

  const { data, isLoading, error } = useQuery({
    queryKey: ['credential-shares', username],
    queryFn: () => api.getCredentialShares(username),
    enabled: !!username,
  });

  const granted = new Set<ShareableService>(data?.services ?? []);
  // With the Home Assistant integration off nobody's HA login is used, shared or
  // not, so the grant is not offered (an existing grant is kept for when it is on).
  const { homeAssistant } = useIntegrations();
  const services = SHAREABLE_SERVICES.filter(({ key }) => key !== 'home_assistant' || homeAssistant);

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

  // Audiobookshelf issues one token per account and shows it to an administrator:
  // there is no API to mint another (POST /api/users/{id}/token is a 404 on 2.33).
  // So each member's own token is copied from the ABS account of the same name,
  // which is what stops the household sharing one login.
  const tokens = useMutation({
    mutationFn: () => api.issueAudiobookshelfTokens(),
    onSuccess: (result) => {
      const issued = result.issued.length;
      const skipped = result.skipped;
      setTokenResult(
        `${issued} given their own token` +
          (skipped.length
            ? `; ${skipped.length} not: ` + skipped.map((s) => `${s.username} (${s.reason})`).join(', ')
            : ''),
      );
      queryClient.invalidateQueries({ queryKey: ['me'] });
      toast.success(skipped.length ? `Filed ${issued} token(s); ${skipped.length} left alone` : `Filed ${issued} token(s)`);
    },
    onError: (err: unknown) => {
      toast.error(err instanceof Error ? err.message : 'Could not file the tokens');
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
        {isSystemDefault ? '. You are the system default user, so these are your own credentials.' : '.'}
      </p>

      <ul className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        {services.map(({ key, label }) => {
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
          {data.note ? `: “${data.note}”` : ''}
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

      {isAdmin ? (
        <div className="pt-1 border-t border-white/5 space-y-2">
          <button
            type="button"
            data-testid="abs-issue-tokens"
            onClick={() => tokens.mutate()}
            disabled={tokens.isPending}
            className="glass-button min-h-11 px-3 text-[10px] font-black uppercase tracking-widest flex items-center gap-2 disabled:opacity-40"
          >
            <KeyRound size={14} />
            {tokens.isPending ? 'Filing tokens…' : 'Give everyone their own Audiobookshelf token'}
          </button>
          <p className="text-[10px] text-slate-500 leading-relaxed">
            Audiobookshelf keeps one token per account. This copies each account&apos;s own token onto the
            member of the same name, so everyone reads and listens as themselves instead of through the
            household login.
          </p>
          {tokenResult ? (
            <p role="status" className="text-[10px] text-slate-300">
              {tokenResult}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
};
