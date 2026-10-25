import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Globe } from 'lucide-react';
import { isAxiosError } from 'axios';
import { api } from '../../services/api';
import type { ScreenRules, WebFilterRules } from '../../types/api';

/**
 * A parent's web filter for one child (Phase 11 step 5). The child's phone
 * filters websites by name: adult and malware sites through a family-safe
 * resolver, safe search on Google, YouTube, Bing and DuckDuckGo, and the
 * parent's own block and allow lists (a listed site covers its subdomains).
 * It takes hold on the phone once the guard is on and Android's one-time VPN
 * prompt was accepted there.
 */

const lines = (text: string) =>
  text.split(/[\s,]+/).map((d) => d.trim().toLowerCase().replace(/^https?:\/\//, '').replace(/\/.*$/, '')).filter(Boolean);

export default function WebFilterControls({ username, name, rules }: { username: string; name: string; rules: ScreenRules }) {
  const queryClient = useQueryClient();
  const wf: WebFilterRules = rules.web_filter || {};
  const [block, setBlock] = useState<string | null>(null);
  const [allow, setAllow] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: (next: WebFilterRules) => {
      // The full rules go back each time: the server replaces them as a whole.
      const rest: ScreenRules = { ...rules };
      delete rest.paused;
      delete rest.paused_until;
      return api.setScreenRules(username, { ...rest, web_filter: next });
    },
    onSuccess: () => {
      setBlock(null);
      setAllow(null);
      void queryClient.invalidateQueries({ queryKey: ['screen-time', username] });
    },
  });
  const current: WebFilterRules = { enabled: false, adult: true, safe_search: true, block: [], allow: [], ...wf };
  const failure = isAxiosError(save.error) ? (save.error.response?.data as { detail?: string })?.detail || 'That did not save.' : save.error ? 'That did not save.' : null;

  const toggle = (key: 'enabled' | 'adult' | 'safe_search', label: string) => (
    <button
      type="button"
      role="switch"
      aria-checked={Boolean(current[key])}
      disabled={save.isPending || (key !== 'enabled' && !current.enabled)}
      onClick={() => save.mutate({ ...current, [key]: !current[key] })}
      className={`min-h-11 rounded-xl border px-3 text-left text-xs flex items-center justify-between gap-2 disabled:opacity-50 ${
        current[key] ? 'border-sky-400/40 bg-sky-400/10 text-slate-100' : 'border-white/10 bg-white/5 text-slate-400'
      }`}
      data-testid={`web-filter-${key}`}
    >
      <span>{label}</span>
      <span className="font-semibold">{current[key] ? 'On' : 'Off'}</span>
    </button>
  );

  return (
    <section className="glass-panel min-w-0 rounded-2xl p-4 sm:p-5 space-y-2" data-testid="web-filter-controls">
      <div className="flex items-center gap-2">
        <Globe size={14} className="text-sky-300" />
        <h3 className="text-sm font-semibold text-white">Websites on {name}'s phone</h3>
      </div>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
        {toggle('enabled', 'Web filter')}
        {toggle('adult', 'Block adult & malware sites')}
        {toggle('safe_search', 'Safe search')}
      </div>
      {current.enabled && (
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          <label className="block text-[11px] text-slate-400">
            Always block (one site per line)
            <textarea
              rows={3}
              value={block ?? (current.block || []).join('\n')}
              onChange={(e) => setBlock(e.target.value)}
              placeholder="tiktok.com"
              className="mt-1 w-full rounded-lg border border-white/10 bg-white/5 px-2 py-1.5 text-sm text-slate-100"
            />
          </label>
          <label className="block text-[11px] text-slate-400">
            Always allow
            <textarea
              rows={3}
              value={allow ?? (current.allow || []).join('\n')}
              onChange={(e) => setAllow(e.target.value)}
              placeholder="khanacademy.org"
              className="mt-1 w-full rounded-lg border border-white/10 bg-white/5 px-2 py-1.5 text-sm text-slate-100"
            />
          </label>
          {(block !== null || allow !== null) && (
            <button
              type="button"
              disabled={save.isPending}
              onClick={() => save.mutate({
                ...current,
                block: block !== null ? lines(block) : current.block,
                allow: allow !== null ? lines(allow) : current.allow,
              })}
              className="glass-button min-h-11 px-3 text-xs font-semibold sm:col-span-2"
              data-testid="web-filter-save"
            >
              Save sites
            </button>
          )}
        </div>
      )}
      <p className="text-[10px] text-slate-500">
        Filters by site name on the phone (Wi-Fi and mobile data). A browser set to its own secure DNS can get around it; the
        guard keeps the filter from being switched off.
      </p>
      {failure && <p className="text-xs text-amber-300">{failure}</p>}
    </section>
  );
}
