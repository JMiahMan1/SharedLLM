import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { History, KeyRound, Loader2, RefreshCcw } from 'lucide-react';
import { api } from '../../services/api';
import { describeChange, describeEvent } from '../../lib/accountAudit';

/**
 * The account trail: every change to a login, password, permission or key,
 * with who did it, from where, and when -- and never the values themselves.
 */
export default function AccountAuditPanel({ users }: { users: string[] }) {
  const [target, setTarget] = useState('');
  const { data: events = [], isFetching, refetch } = useQuery({
    queryKey: ['account-audit', target],
    queryFn: () => api.getAccountAudit({ target: target || undefined, limit: 300 }),
  });

  return (
    <div className="glass-panel p-6" data-testid="account-audit">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="flex items-center gap-3 text-xl font-bold text-white">
            <History size={20} className="text-amber-300" />
            Account activity
          </h3>
          <p className="mt-1 text-sm text-slate-400">
            Who changed which account, login or permission, and when. Values are never recorded.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={target}
            onChange={(event) => setTarget(event.target.value)}
            aria-label="Filter by account"
            className="glass-input text-sm"
          >
            <option value="">All accounts</option>
            {users.map((u) => (
              <option key={u} value={u}>
                {u}
              </option>
            ))}
          </select>
          <button type="button" className="glass-button p-2.5" aria-label="Refresh activity" onClick={() => void refetch()}>
            <RefreshCcw size={14} className={isFetching ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      {isFetching && events.length === 0 ? (
        <p className="flex items-center gap-2 text-sm text-slate-400">
          <Loader2 size={14} className="animate-spin" /> Loading…
        </p>
      ) : events.length === 0 ? (
        <p className="text-sm text-slate-500">No account activity recorded yet. Changes from now on will appear here.</p>
      ) : (
        <ol className="space-y-2">
          {events.map((event) => {
            const secretTouched = event.changes.some((c) => c.secret);
            return (
              <li key={event.id} className="glass-card p-3">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <p className="text-sm text-slate-100">
                    {secretTouched && <KeyRound size={13} className="mr-1.5 inline text-amber-300" aria-label="touched a credential" />}
                    {describeEvent(event)}
                  </p>
                  <time className="text-[11px] text-slate-500" dateTime={event.at}>
                    {new Date(event.at).toLocaleString()}
                  </time>
                </div>
                {event.changes.length > 0 ? (
                  <p className="mt-1 text-xs text-slate-300">{event.changes.map(describeChange).join(' · ')}</p>
                ) : (
                  event.action.startsWith('user.') && <p className="mt-1 text-xs text-slate-500">Saved with no changes</p>
                )}
                <p className="mt-1 font-mono text-[10px] text-slate-500">
                  {[event.source, event.client, event.note].filter(Boolean).join(' · ')}
                </p>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}
