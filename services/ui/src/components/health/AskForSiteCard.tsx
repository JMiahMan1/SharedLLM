import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Globe } from 'lucide-react';
import { isAxiosError } from 'axios';
import { api } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import { siteOf } from '../../lib/guardRequests';

/**
 * "Ask a parent" for a website (Phase 11 step 5), for a child whose phone
 * filters websites: type the site, say what it is for, and both parents are
 * asked. The phone offers the same from its "site is blocked" notification.
 * Only the child's own requests are shown, never the browsing behind them.
 */
export default function AskForSiteCard() {
  const me = useAuth().user?.username || '';
  const queryClient = useQueryClient();
  const [site, setSite] = useState('');
  const [note, setNote] = useState('');
  const { data: status } = useQuery({
    queryKey: ['screen-time', me],
    queryFn: () => api.getScreenTimeStatus(me),
    enabled: Boolean(me),
    refetchInterval: 60 * 1000,
    retry: false,
  });
  const filtered = Boolean(status?.protected && status.rules?.web_filter?.enabled);
  const { data: pending } = useQuery({
    queryKey: ['guard-requests', 'pending', me],
    queryFn: () => api.getGuardRequests('pending'),
    enabled: filtered,
    retry: false,
  });
  const ask = useMutation({
    mutationFn: () => api.askAParent('site', siteOf(site), note.trim() || undefined),
    onSuccess: () => {
      setSite('');
      setNote('');
      void queryClient.invalidateQueries({ queryKey: ['guard-requests', 'pending', me] });
    },
  });

  if (!filtered) return null;
  const waiting = (pending?.requests || []).filter((r) => r.username === me && r.kind === 'site');
  const failure = isAxiosError(ask.error) ? (ask.error.response?.data as { detail?: string })?.detail || 'That did not send.' : ask.error ? 'That did not send.' : null;
  const valid = /^[a-z0-9-]+(\.[a-z0-9-]+)+$/.test(siteOf(site));

  return (
    <section className="glass-panel rounded-2xl p-4 sm:p-5 space-y-2" data-testid="ask-for-site">
      <h3 className="flex items-center gap-2 text-sm font-semibold text-white">
        <Globe size={16} className="text-sky-300" /> Need a website?
      </h3>
      <p className="text-xs text-slate-400">If a site is blocked, ask a parent. They can allow it for a while or for good.</p>
      {waiting.length > 0 && (
        <ul className="space-y-0.5 text-xs text-sky-300" data-testid="ask-for-site-waiting">
          {waiting.map((r) => <li key={r.id}>Asked about {r.target}. A parent will answer soon.</li>)}
        </ul>
      )}
      <input
        value={site}
        onChange={(e) => setSite(e.target.value)}
        maxLength={253}
        inputMode="url"
        placeholder="example.com"
        aria-label="Which website?"
        className="w-full rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500"
      />
      <input
        value={note}
        onChange={(e) => setNote(e.target.value)}
        maxLength={200}
        placeholder="What is it for? (optional)"
        aria-label="What is the website for?"
        className="w-full rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500"
      />
      <button
        type="button"
        disabled={!valid || ask.isPending}
        onClick={() => ask.mutate()}
        className="glass-button px-3 py-1.5 text-xs min-h-11 disabled:opacity-50"
      >
        Ask a parent
      </button>
      {ask.isSuccess && <p className="text-xs text-emerald-300">Asked. You will hear when a parent answers.</p>}
      {failure && <p className="text-xs text-amber-300">{failure}</p>}
    </section>
  );
}
