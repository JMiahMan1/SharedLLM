import { useState, type FC, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, ArrowDown, ArrowUp, Globe, RefreshCw, Router, ScrollText, Wifi, WifiOff } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import { formatBytes } from '../chat/chatModel';
import { relativeTime } from '../../lib/wanderTrips';
import {
  busyLevel,
  formatUptime,
  signalLabel,
  type NetworkRadio,
  type NetworkStatus,
  type NetworkUsage,
} from '../../lib/network';

/**
 * Admin > Network: the home router as Jarvis sees it (docs/NETWORK.md).
 * Status refreshes every 30 seconds; the router itself is checked every
 * minute by the control plane, which also sends the alerts listed here.
 */

const BUSY_STYLE = {
  calm: { bar: 'bg-emerald-500', text: 'text-emerald-300', label: 'Calm' },
  busy: { bar: 'bg-amber-500', text: 'text-amber-300', label: 'Busy' },
  congested: { bar: 'bg-rose-500', text: 'text-rose-300', label: 'Congested' },
  unknown: { bar: 'bg-slate-500', text: 'text-slate-400', label: 'Measuring' },
} as const;

const LOG_LEVELS = [
  { id: '', label: 'All' },
  { id: 'warning', label: 'Warnings' },
  { id: 'err', label: 'Errors' },
] as const;

export const NetworkPanel: FC = () => {
  const queryClient = useQueryClient();
  const [showLogin, setShowLogin] = useState(false);
  const [usageHours, setUsageHours] = useState(24);
  const [logLevel, setLogLevel] = useState('');
  const [logQuery, setLogQuery] = useState('');

  const { data: status, isPending } = useQuery<NetworkStatus>({
    queryKey: ['network-status'],
    queryFn: () => api.getNetworkStatus(),
    refetchInterval: 30_000,
  });
  const { data: usage } = useQuery({
    queryKey: ['network-usage', usageHours],
    queryFn: () => api.getNetworkUsage(usageHours),
    enabled: !!status?.configured,
    refetchInterval: 60_000,
  });
  const { data: log } = useQuery({
    queryKey: ['network-log', logLevel, logQuery],
    queryFn: () => api.getRouterLog({ limit: 200, level: logLevel || undefined, q: logQuery || undefined }),
    enabled: !!status?.configured,
    refetchInterval: 60_000,
  });

  const check = useMutation({
    mutationFn: () => api.checkNetwork(),
    onSuccess: (next) => queryClient.setQueryData(['network-status'], next),
    onError: () => toast.error('Could not check the network'),
  });

  if (isPending) return <p className="text-sm text-slate-400 p-4">Loading the network…</p>;

  const s = status ?? { configured: false };
  const radios = s.radios ?? [];
  const pill = !s.configured
    ? { text: 'Not set up', cls: 'text-slate-300 border-white/10 bg-white/5' }
    : s.connected
      ? { text: 'Connected', cls: 'text-emerald-300 border-emerald-500/40 bg-emerald-500/10' }
      : { text: "Can't reach the router", cls: 'text-rose-300 border-rose-500/40 bg-rose-500/10' };

  return (
    <div className="space-y-4" data-testid="network-panel">
      <section className="glass-panel p-4 sm:p-5 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3 min-w-0">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-purple-500/15">
              <Router size={18} className="text-purple-300" />
            </div>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-base font-bold text-white">Home network</h2>
                <span className={`rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider ${pill.cls}`}>
                  {pill.text}
                </span>
              </div>
              <p className="text-xs text-slate-400 truncate">
                {s.router?.model ? `${s.router.model} · OpenWrt ${s.router.version ?? ''} · up ${formatUptime(s.router.uptime)}` : 'Router monitoring'}
                {s.checked_at ? ` · checked ${relativeTime(s.checked_at)}` : ''}
              </p>
            </div>
          </div>
          <div className="flex gap-2">
            {s.configured && (
              <button
                type="button"
                onClick={() => check.mutate()}
                disabled={check.isPending}
                className="glass-button flex min-h-11 items-center gap-1.5 px-3 text-xs font-semibold"
              >
                <RefreshCw size={14} className={check.isPending ? 'animate-spin' : ''} /> Check now
              </button>
            )}
            <button
              type="button"
              onClick={() => setShowLogin((v) => !v)}
              aria-expanded={showLogin}
              className="glass-button min-h-11 px-3 text-xs font-semibold"
            >
              {s.configured ? 'Router login' : 'Set up'}
            </button>
          </div>
        </div>

        {s.error && <p className="text-xs text-rose-300">{s.error}</p>}

        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <Stat icon={Globe} label="Internet" value={s.internet == null ? '…' : s.internet ? 'Up' : 'Down'} good={s.internet} />
          <Stat icon={Router} label="Router memory free" value={s.router?.memory_free_pct != null ? `${s.router.memory_free_pct}%` : 'No data'} />
          <Stat icon={Wifi} label="Wi-Fi devices" value={String(radios.reduce((n, r) => n + r.clients.length, 0))} />
          <Stat
            icon={ScrollText}
            label="Connections"
            value={s.conntrack?.max ? `${s.conntrack.count ?? 0} / ${s.conntrack.max}` : 'No data'}
          />
        </div>

        {showLogin && <RouterLoginForm status={s} onSaved={(next) => {
          queryClient.setQueryData(['network-status'], next);
          setShowLogin(false);
          if (next.connected) toast.success('Connected to the router');
          else toast.error(next.error || "Saved, but Jarvis can't reach the router yet");
        }} />}
      </section>

      {(s.events?.length ?? 0) > 0 && (
        <section className="glass-panel p-4 sm:p-5">
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-white">
            <AlertTriangle size={15} className="text-amber-300" /> Recent alerts
          </h3>
          <ul className="space-y-2">
            {s.events!.slice(0, 8).map((e) => (
              <li key={`${e.time}-${e.tag}`} className="rounded-xl border border-white/5 bg-white/5 p-3">
                <p className="text-sm font-medium text-white">{e.title}</p>
                <p className="mt-0.5 text-xs text-slate-400">{e.body}</p>
                <p className="mt-1 text-[10px] text-slate-500">{relativeTime(e.time)}</p>
              </li>
            ))}
          </ul>
        </section>
      )}

      {radios.map((r) => <RadioCard key={r.device} radio={r} />)}

      {s.configured && <UsageSection hours={usageHours} onHours={setUsageHours} usage={usage} />}

      {s.configured && <OutagesSection />}

      {s.configured && (
        <section className="glass-panel p-4 sm:p-5">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-sm font-semibold text-white">Router log</h3>
            <div className="flex gap-1" role="group" aria-label="Level">
              {LOG_LEVELS.map((l) => (
                <button
                  key={l.id}
                  type="button"
                  aria-pressed={logLevel === l.id}
                  onClick={() => setLogLevel(l.id)}
                  className={`min-h-9 rounded-lg px-2.5 text-xs font-semibold ${logLevel === l.id ? 'bg-purple-500/20 text-purple-300' : 'text-slate-400 hover:text-white'}`}
                >
                  {l.label}
                </button>
              ))}
            </div>
          </div>
          <input
            type="search"
            value={logQuery}
            onChange={(e) => setLogQuery(e.target.value)}
            placeholder="Search the log"
            className="glass-input mb-3 w-full min-h-11 px-3 text-sm"
          />
          <div className="max-h-80 overflow-y-auto rounded-xl bg-black/20 p-2 font-mono text-[11px] leading-relaxed">
            {log?.lines.length ? log.lines.map((l) => (
              <div key={l.id} className={l.level === 'err' || l.level === 'crit' ? 'text-rose-300' : l.level === 'warning' ? 'text-amber-300' : 'text-slate-300'}>
                <span className="text-slate-500">{new Date(l.time * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</span>{' '}
                {l.msg}
              </div>
            )) : <p className="text-slate-500">No lines yet.</p>}
          </div>
        </section>
      )}
    </div>
  );
};

const Stat: FC<{ icon: React.ElementType; label: string; value: string; good?: boolean }> = ({ icon: Icon, label, value, good }) => (
  <div className="rounded-xl border border-white/5 bg-white/5 p-3">
    <p className="flex items-center gap-1.5 text-[11px] text-slate-400"><Icon size={12} /> {label}</p>
    <p className={`mt-1 text-sm font-semibold ${good === false ? 'text-rose-300' : 'text-white'}`}>{value}</p>
  </div>
);

const RadioCard: FC<{ radio: NetworkRadio }> = ({ radio }) => {
  const level = busyLevel(radio.busy_pct);
  const style = BUSY_STYLE[level];
  return (
    <section className="glass-panel p-4 sm:p-5" data-testid="network-radio">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-white">
          <Wifi size={15} className="text-purple-300" /> {radio.ssid || radio.device}
          <span className="text-xs font-normal text-slate-400">{radio.band}{radio.channel ? ` · channel ${radio.channel}` : ''}</span>
        </h3>
        <span className={`text-xs font-semibold ${style.text}`}>
          {style.label}{radio.busy_pct != null ? ` · ${radio.busy_pct.toFixed(0)}% busy` : ''}
        </span>
      </div>
      <div className="mb-3 h-2 overflow-hidden rounded-full bg-white/5" aria-hidden>
        <div className={`h-full rounded-full ${style.bar}`} style={{ width: `${Math.min(100, radio.busy_pct ?? 0)}%` }} />
      </div>
      {radio.clients.length === 0 ? (
        <p className="text-xs text-slate-500">No devices connected.</p>
      ) : (
        <ul className="divide-y divide-white/5">
          {radio.clients.map((c) => (
            <li key={c.mac} className="flex items-center justify-between gap-3 py-2">
              <div className="min-w-0">
                <p className="truncate text-sm text-white">{c.name}</p>
                <p className="truncate text-[11px] text-slate-500">
                  {[c.ip, c.signal != null ? `${signalLabel(c.signal)} (${c.signal} dBm)` : null, `${c.tx_mbps} Mbit/s`].filter(Boolean).join(' · ')}
                </p>
              </div>
              {c.airtime_pct != null && c.airtime_pct >= 1 && (
                <span className={`shrink-0 text-xs font-semibold ${c.airtime_pct >= 35 ? 'text-rose-300' : 'text-slate-300'}`}>
                  {c.airtime_pct.toFixed(0)}% airtime
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
};

const RouterLoginForm: FC<{ status: NetworkStatus; onSaved: (s: NetworkStatus) => void }> = ({ status, onSaved }) => {
  const [url, setUrl] = useState('https://192.168.2.1');
  const [username, setUsername] = useState('jarvis');
  const [password, setPassword] = useState('');
  const save = useMutation({
    mutationFn: () => api.saveRouterSettings({ url, username, password: password || undefined }),
    onSuccess: onSaved,
    onError: (err: unknown) => {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail || 'Could not save the router login');
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate();
  };
  return (
    <form onSubmit={submit} className="space-y-2 rounded-xl border border-white/5 bg-white/5 p-3">
      <p className="text-xs text-slate-400">
        Use a read-only router account (scripts/router_setup.sh creates one). The password is stored encrypted and never shown again.
      </p>
      <div className="grid gap-2 sm:grid-cols-3">
        <input className="glass-input min-h-11 px-3 text-sm" aria-label="Router address" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://192.168.2.1" />
        <input className="glass-input min-h-11 px-3 text-sm" aria-label="Router username" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="off" />
        <input
          className="glass-input min-h-11 px-3 text-sm"
          aria-label="Router password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder={status.configured ? 'Unchanged' : 'Password'}
          autoComplete="new-password"
        />
      </div>
      <button type="submit" disabled={save.isPending} className="glass-button min-h-11 px-4 text-xs font-semibold">
        {save.isPending ? 'Checking…' : 'Save and check'}
      </button>
    </form>
  );
};

const PERIODS = [{ h: 24, label: 'Day' }, { h: 168, label: 'Week' }, { h: 720, label: 'Month' }];

const UsageSection: FC<{ hours: number; onHours: (h: number) => void; usage: NetworkUsage | undefined }> = ({ hours, onHours, usage }) => {
  const total = (usage?.devices ?? []).reduce((n, d) => n + d.bytes, 0);
  return (
    <section className="glass-panel p-4 sm:p-5 space-y-4" data-testid="network-usage">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold text-white">Data use</h3>
          {usage && (
            <p className="text-[11px] text-slate-500">
              {formatBytes(total) || '0 B'} in all ·{' '}
              {usage.source === 'nlbw' ? 'every device, wired and Wi-Fi' : "Wi-Fi devices only (the router's traffic counter is not set up)"}
            </p>
          )}
        </div>
        <div className="flex gap-1" role="group" aria-label="Period">
          {PERIODS.map((p) => (
            <button
              key={p.h}
              type="button"
              aria-pressed={hours === p.h}
              onClick={() => onHours(p.h)}
              className={`min-h-9 rounded-lg px-2.5 text-xs font-semibold ${hours === p.h ? 'bg-purple-500/20 text-purple-300' : 'text-slate-400 hover:text-white'}`}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {!usage || usage.devices.length === 0 ? (
        <p className="text-xs text-slate-500">Collecting; data use appears after a few minutes.</p>
      ) : (
        <>
          <TrafficChart series={usage.series} bucket={usage.bucket} />

          {usage.networks.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {usage.networks.map((n) => (
                <div key={n.name} className="rounded-xl border border-white/5 bg-white/5 px-3 py-2 text-xs">
                  <p className="font-semibold text-white capitalize">{n.name}</p>
                  <p className="text-slate-400">
                    <ArrowDown size={11} className="inline" /> {formatBytes(n.down) || '0 B'}{' '}
                    <ArrowUp size={11} className="inline" /> {formatBytes(n.up) || '0 B'}
                  </p>
                </div>
              ))}
            </div>
          )}

          <ul className="space-y-2">
            {usage.devices.slice(0, 15).map((d) => (
              <li key={d.mac}>
                <div className="flex items-baseline justify-between gap-2 text-xs">
                  <span className="min-w-0 truncate text-slate-200">
                    {d.name}
                    <span className="text-slate-500">{[d.ip, d.network].filter(Boolean).map((x) => ` · ${x}`).join('')}</span>
                  </span>
                  <span className="shrink-0 font-mono text-slate-300">
                    <ArrowDown size={11} className="inline" /> {formatBytes(d.down) || '0 B'}{' '}
                    <ArrowUp size={11} className="inline text-slate-500" /> {formatBytes(d.up) || '0 B'}
                  </span>
                </div>
                <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-white/5">
                  <div className="h-full rounded-full bg-purple-500" style={{ width: `${Math.max(2, (100 * d.bytes) / usage.devices[0].bytes)}%` }} />
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
};

/** Household traffic per hour or day: download and upload stacked. */
const TrafficChart: FC<{ series: NetworkUsage['series']; bucket: NetworkUsage['bucket'] }> = ({ series, bucket }) => {
  const max = Math.max(1, ...series.map((p) => p.down + p.up));
  const peak = series.reduce((best, p) => (p.down + p.up > best.down + best.up ? p : best), series[0]);
  const label = (t: string) =>
    bucket === 'hour'
      ? new Date(`${t}:00`).toLocaleTimeString([], { hour: 'numeric' })
      : new Date(`${t}T12:00`).toLocaleDateString([], { month: 'short', day: 'numeric' });
  return (
    <figure data-testid="traffic-chart">
      <div className="flex h-24 items-end gap-px" role="img" aria-label={`Traffic by ${bucket}; busiest ${peak ? label(peak.t) : ''}`}>
        {series.map((p) => (
          <div key={p.t} className="flex h-full flex-1 flex-col justify-end" title={`${label(p.t)}: ${formatBytes(p.down) || '0 B'} down, ${formatBytes(p.up) || '0 B'} up`}>
            <div className="rounded-t-sm bg-purple-300/60" style={{ height: `${(100 * p.up) / max}%` }} />
            <div className="bg-purple-500" style={{ height: `${(100 * p.down) / max}%` }} />
          </div>
        ))}
      </div>
      <figcaption className="mt-1 flex justify-between text-[10px] text-slate-500">
        <span>{series[0] ? label(series[0].t) : ''}</span>
        <span>
          <span className="inline-block h-2 w-2 rounded-sm bg-purple-500" /> download{' '}
          <span className="inline-block h-2 w-2 rounded-sm bg-purple-300/60" /> upload
        </span>
        <span>{series.length ? label(series[series.length - 1].t) : ''}</span>
      </figcaption>
    </figure>
  );
};

const OutagesSection: FC = () => {
  const { data } = useQuery({
    queryKey: ['network-outages'],
    queryFn: () => api.getInternetOutages(30),
    refetchInterval: 60_000,
  });
  const outages = data?.outages ?? [];
  const minutes = (m: number) => (m < 120 ? `${Math.max(1, m)} min` : `${(m / 60).toFixed(1)} hrs`);
  return (
    <section className="glass-panel p-4 sm:p-5" data-testid="network-outages">
      <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-white">
        <WifiOff size={15} className="text-purple-300" /> Internet outages
      </h3>
      {outages.length === 0 ? (
        <p className="text-xs text-slate-500">None in the last 30 days.</p>
      ) : (
        <ul className="divide-y divide-white/5">
          {outages.map((o) => (
            <li key={o.start} className="flex items-center justify-between gap-3 py-2 text-xs">
              <span className="text-slate-200">
                {new Date(o.start * 1000).toLocaleString([], { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}
              </span>
              <span className={o.end ? 'text-slate-400' : 'font-semibold text-rose-300'}>
                {o.end ? minutes(o.minutes) : `down now · ${minutes(o.minutes)}`}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
};

export default NetworkPanel;
