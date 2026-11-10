import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Globe } from 'lucide-react';
import { isAxiosError } from 'axios';
import { api } from '../../services/api';
import type { ScreenRules, WebFilterCategory, WebFilterRules } from '../../types/api';
import { useMemberDeviceNoun } from '../../hooks/useDeviceNoun';
import MemberCard from '../member/MemberCard';
import Toggle from '../ui/Toggle';

const CATEGORIES: Array<{ id: WebFilterCategory; label: string }> = [
  { id: 'social', label: 'Social media' },
  { id: 'gaming', label: 'Online gaming' },
  { id: 'streaming', label: 'Video streaming' },
  { id: 'chat', label: 'Chat & forums' },
  { id: 'gambling', label: 'Gambling' },
  { id: 'vpn', label: 'VPNs & proxies' },
];

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
  const device = useMemberDeviceNoun(username);
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
  const current: WebFilterRules = { enabled: false, adult: true, safe_search: true, categories: [], block: [], allow: [], ...wf };
  const failure = isAxiosError(save.error) ? (save.error.response?.data as { detail?: string })?.detail || 'That did not save.' : save.error ? 'That did not save.' : null;
  const toggleCategory = (cat: WebFilterCategory) => {
    const selected = current.categories || [];
    const next = selected.includes(cat) ? selected.filter((c) => c !== cat) : [...selected, cat];
    save.mutate({ ...current, categories: next });
  };

  const toggle = (key: 'enabled' | 'adult' | 'safe_search', label: string, hint: string) => (
    <div className={`flex items-center justify-between gap-3 px-3 py-2.5 ${key !== 'enabled' && !current.enabled ? 'opacity-60' : ''}`}>
      <div className="min-w-0 text-xs">
        <p className="font-medium text-slate-100">{label}</p>
        <p className="text-[11px] text-slate-500">{hint}</p>
      </div>
      <Toggle
        checked={Boolean(current[key])}
        ariaLabel={label}
        testId={`web-filter-${key}`}
        disabled={save.isPending || (key !== 'enabled' && !current.enabled)}
        onChange={() => save.mutate({ ...current, [key]: !current[key] })}
      />
    </div>
  );

  return (
    <MemberCard
      title={`Websites on ${name}'s ${device}`}
      icon={Globe}
      testId="web-filter-controls"
      aside={current.enabled ? 'Filter on' : 'Filter off'}
    >
      <div className="divide-y divide-white/5 rounded-xl border border-white/10 bg-white/[0.03]">
        {toggle('enabled', 'Web filter', `Filters websites on the ${device}`)}
        {toggle('adult', 'Block adult & malware sites', current.enabled ? 'Through a family-safe resolver' : 'Turn the web filter on first')}
        {toggle('safe_search', 'Safe search', current.enabled ? 'Google, YouTube, Bing and DuckDuckGo' : 'Turn the web filter on first')}
      </div>
      {current.enabled && (
        <div className="space-y-2">
          <p className="text-[11px] text-slate-400">Block whole kinds of sites</p>
          <div className="grid grid-cols-1 gap-x-4 divide-y divide-white/5 rounded-xl border border-white/10 bg-white/[0.03] sm:grid-cols-2 sm:divide-y-0" data-testid="web-filter-categories">
            {CATEGORIES.map((cat) => {
              const on = (current.categories || []).includes(cat.id);
              return (
                <div key={cat.id} className="flex min-h-11 items-center justify-between gap-3 px-3 py-1.5 text-xs">
                  <span className="text-slate-200">{cat.label}</span>
                  <span className="flex items-center gap-2">
                    <span className={`text-[10px] ${on ? 'text-amber-300' : 'text-slate-500'}`}>{on ? 'Blocked' : 'Allowed'}</span>
                    <Toggle
                      checked={on}
                      ariaLabel={`Block ${cat.label}`}
                      testId={`web-filter-category-${cat.id}`}
                      disabled={save.isPending}
                      onChange={() => toggleCategory(cat.id)}
                    />
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}
      {current.enabled && (
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          <label className="block text-[11px] text-slate-400">
            Always block (one site per line)
            <textarea
              rows={3}
              value={block ?? (current.block || []).join('\n')}
              onChange={(e) => setBlock(e.target.value)}
              placeholder="tiktok.com"
              className="mt-1 w-full rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500 focus:border-sky-400/50 focus:outline-none"
            />
          </label>
          <label className="block text-[11px] text-slate-400">
            Always allow
            <textarea
              rows={3}
              value={allow ?? (current.allow || []).join('\n')}
              onChange={(e) => setAllow(e.target.value)}
              placeholder="khanacademy.org"
              className="mt-1 w-full rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500 focus:border-sky-400/50 focus:outline-none"
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
        Filters by site name on the {device} (Wi-Fi and mobile data). A browser set to its own secure DNS can get around it; the
        guard keeps the filter from being switched off.
      </p>
      {failure && <p className="text-xs text-amber-300" role="alert">{failure}</p>}
    </MemberCard>
  );
}
