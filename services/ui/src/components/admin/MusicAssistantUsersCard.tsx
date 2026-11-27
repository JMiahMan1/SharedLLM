import { useState, type FC } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ExternalLink, KeyRound, Users } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import PasswordField from '../ui/PasswordField';

/** Music Assistant refuses a password shorter than this, so the button does too. */
const MIN_PASSWORD = 8;

/**
 * The logins people open Music Assistant itself with.
 *
 * An account Jarvis makes for a person is given a random password nobody knows,
 * because the app signs in with a long-lived token instead. That leaves the
 * person able to listen through Jarvis but unable to open Music Assistant's own
 * web interface. An administrator sets a password here, and nothing about who
 * exists is written down: the accounts are read from Music Assistant when this
 * card loads. Music Assistant keeps only a hash, so a password is sent once and
 * never read back.
 */
export const MusicAssistantUsersCard: FC = () => {
  const queryClient = useQueryClient();
  const [drafts, setDrafts] = useState<Record<string, string>>({});

  const { data, isLoading, error } = useQuery({
    queryKey: ['music-assistant-users'],
    queryFn: () => api.musicAssistantUsers(),
    retry: false,
  });

  const setPassword = useMutation({
    mutationFn: (vars: { username: string; password: string }) =>
      api.setMusicAssistantPassword(vars.username, vars.password),
    onSuccess: (result) => {
      setDrafts((current) => ({ ...current, [result.username.toLowerCase()]: '' }));
      toast.success(`Music Assistant password set for ${result.username}`);
      queryClient.invalidateQueries({ queryKey: ['music-assistant-users'] });
    },
    onError: (err: unknown) => {
      toast.error(err instanceof Error ? err.message : 'Could not set the password');
    },
  });

  if (isLoading) {
    return (
      <div className="p-4 glass-card border-white/5 bg-white/5 rounded-xl text-xs text-slate-400">
        Loading Music Assistant accounts…
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-4 glass-card border-red-500/20 bg-red-500/10 rounded-xl text-xs text-red-300">
        Could not load the Music Assistant accounts: {error instanceof Error ? error.message : 'unknown error'}
      </div>
    );
  }

  const users = data?.users ?? [];
  const pendingFor = setPassword.isPending ? setPassword.variables?.username.toLowerCase() : undefined;

  return (
    <div className="p-4 glass-card border-white/5 bg-white/5 rounded-xl space-y-3" data-testid="ma-users">
      <div className="flex items-center gap-2">
        <Users size={16} className="text-purple-400" />
        <p className="text-xs font-bold text-white">Music Assistant accounts</p>
      </div>
      <p className="text-[10px] text-slate-500 leading-relaxed">
        Accounts Jarvis made are signed in with a token, so their password is a random one nobody knows.
        Set one here and that person can open Music Assistant&apos;s own web interface as themselves.
        Music Assistant keeps only a hash: a password set here cannot be read back, only replaced.
      </p>
      {data?.server ? (
        <a
          href={data.server}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 text-[10px] text-sky-300 hover:text-sky-200"
        >
          Open Music Assistant <ExternalLink size={11} aria-hidden />
        </a>
      ) : null}

      <ul className="space-y-2">
        {users.map((user) => {
          const key = user.username.toLowerCase();
          const draft = drafts[key] ?? '';
          const pending = pendingFor === key;
          return (
            <li key={key} className="p-2 rounded-lg bg-black/20 border border-white/5 space-y-2">
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs text-white font-mono">{user.username}</span>
                <span
                  className={`text-[9px] uppercase tracking-widest ${
                    user.role === 'admin' ? 'text-purple-300' : 'text-slate-500'
                  }`}
                >
                  {user.role}
                  {user.enabled ? '' : ' · disabled'}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <PasswordField
                  aria-label={`Music Assistant password for ${user.username}`}
                  autoComplete="new-password"
                  placeholder={`Password (${MIN_PASSWORD}+ characters)`}
                  value={draft}
                  onChange={(e) => setDrafts((current) => ({ ...current, [key]: e.target.value }))}
                  className="glass-input flex-1 text-xs py-2 bg-black/20"
                />
                <button
                  type="button"
                  data-testid={`ma-password-${key}`}
                  onClick={() => setPassword.mutate({ username: user.username, password: draft })}
                  disabled={draft.length < MIN_PASSWORD || pending}
                  className="glass-button min-h-11 px-3 text-[10px] font-black uppercase tracking-widest flex items-center gap-2 disabled:opacity-40"
                >
                  <KeyRound size={14} />
                  {pending ? 'Setting…' : 'Set'}
                </button>
              </div>
            </li>
          );
        })}
      </ul>
      {users.length === 0 ? (
        <p className="text-[10px] text-slate-500">Music Assistant has no accounts yet.</p>
      ) : null}
    </div>
  );
};
