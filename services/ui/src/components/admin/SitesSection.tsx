import { useMemo, useState, type FC } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ArrowLeft, Globe, Loader2, Search } from 'lucide-react';
import { errorText } from '../../lib/errorText';
import { getDeviceSites, getSiteDevices, groupBySite, lastSeen, type SiteDevice } from '../../lib/networkSites';

const PERIODS = [{ h: 24, label: 'Day' }, { h: 168, label: 'Week' }, { h: 720, label: 'Month' }];

const DeviceSites: FC<{ device: SiteDevice; hours: number; onBack: () => void }> = ({ device, hours, onBack }) => {
  const [query, setQuery] = useState('');
  const sites = useQuery({
    queryKey: ['network-sites', device.device, hours],
    queryFn: () => getDeviceSites(hours, device.device),
  });
  const groups = useMemo(() => groupBySite(sites.data?.sites ?? []), [sites.data]);
  const needle = query.trim().toLowerCase();
  const shown = needle
    ? groups.filter((g) => g.site.includes(needle) || g.domains.some((d) => d.domain.includes(needle)))
    : groups;
  const top = groups[0]?.lookups ?? 1;
  return (
    <div className="space-y-3" data-testid="device-sites">
      <div className="flex items-center gap-2">
        <button type="button" onClick={onBack} aria-label="Back to all devices"
          className="min-h-11 min-w-11 flex items-center justify-center rounded-xl bg-white/5 border border-white/10 text-white hover:bg-white/10">
          <ArrowLeft size={16} />
        </button>
        <div className="min-w-0">
          <p className="break-words text-sm font-semibold text-white">{device.name}</p>
          <p className="text-[11px] text-slate-500">
            {[device.ip, device.network, `${groups.length} sites`].filter(Boolean).join(' · ')}
          </p>
        </div>
      </div>
      <label className="flex min-h-11 items-center gap-2 rounded-xl bg-white/5 border border-white/10 px-3">
        <Search size={14} className="text-slate-500" aria-hidden />
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Find a site"
          aria-label="Find a site" className="min-h-11 w-full bg-transparent text-sm text-white outline-none" />
      </label>
      {sites.isLoading ? (
        <div className="flex justify-center py-6" role="status" aria-label="Loading sites"><Loader2 className="animate-spin text-slate-500" /></div>
      ) : sites.isError ? (
        <p className="text-sm text-red-300">{errorText(sites.error, 'Couldn’t read this device’s sites')}</p>
      ) : shown.length === 0 ? (
        <p className="text-xs text-slate-500">{needle ? 'No site matches.' : 'No lookups in this period.'}</p>
      ) : (
        <ul className="space-y-1.5" aria-label={`Sites ${device.name} looked up`}>
          {shown.slice(0, 200).map((g) => (
            <li key={g.site}>
              <details className="group rounded-xl border border-white/5 bg-white/5">
                <summary className="min-h-11 cursor-pointer list-none px-3 py-2">
                  <span className="flex items-baseline justify-between gap-2 text-xs">
                    <span className="min-w-0 break-all text-slate-100">{g.site}</span>
                    <span className="shrink-0 font-mono text-slate-300">{g.lookups.toLocaleString()}</span>
                  </span>
                  <span className="mt-1 block h-1 overflow-hidden rounded-full bg-white/5">
                    <span className="block h-full rounded-full bg-cyan-500" style={{ width: `${Math.max(2, (100 * g.lookups) / top)}%` }} />
                  </span>
                  <span className="mt-1 block text-[11px] text-slate-500">
                    Last {lastSeen(g.last)}{g.domains.length > 1 ? ` · ${g.domains.length} names, tap to see` : ''}
                  </span>
                </summary>
                {g.domains.length > 1 && (
                  <ul className="space-y-0.5 px-3 pb-2">
                    {g.domains.map((d) => (
                      <li key={d.domain} className="flex justify-between gap-2 text-[11px] text-slate-400">
                        <span className="min-w-0 break-all">{d.domain}</span>
                        <span className="shrink-0 font-mono">{d.lookups.toLocaleString()}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </details>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};

/**
 * Admin > Network > Sites by device: every device that looked anything up in
 * the period, then the sites one device visited (lookups grouped by site, so
 * a site's many addresses count as one). From the router's DNS log, kept 30
 * days. A lookup is not a visit's length or content: apps also look names up
 * on their own.
 */
export const SitesSection: FC = () => {
  const [hours, setHours] = useState(24);
  const [selected, setSelected] = useState<SiteDevice | null>(null);
  const [query, setQuery] = useState('');
  const devices = useQuery({ queryKey: ['network-site-devices', hours], queryFn: () => getSiteDevices(hours) });
  const list = devices.data?.devices ?? [];
  const needle = query.trim().toLowerCase();
  const shown = needle
    ? list.filter((d) => [d.name, d.ip, d.device, d.network].some((v) => v?.toLowerCase().includes(needle)))
    : list;

  return (
    <section className="glass-panel p-4 sm:p-5 space-y-4" data-testid="network-sites">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="flex items-center gap-2 text-sm font-semibold text-white"><Globe size={15} aria-hidden />Sites by device</h3>
          <p className="text-[11px] text-slate-500">
            From the router’s lookups, kept 30 days. Apps look sites up on their own too, so a lookup is not always a visit.
          </p>
        </div>
        <div className="flex gap-1" role="group" aria-label="Sites period">
          {PERIODS.map((p) => (
            <button key={p.h} type="button" aria-pressed={hours === p.h} onClick={() => setHours(p.h)}
              className={`min-h-11 rounded-lg px-3 text-xs font-semibold ${hours === p.h ? 'bg-purple-500/20 text-purple-300' : 'text-slate-400 hover:text-white'}`}>
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {selected ? (
        <DeviceSites device={selected} hours={hours} onBack={() => setSelected(null)} />
      ) : devices.isLoading ? (
        <div className="flex justify-center py-6" role="status" aria-label="Loading devices"><Loader2 className="animate-spin text-slate-500" /></div>
      ) : devices.isError ? (
        <p className="text-sm text-red-300">{errorText(devices.error, 'Couldn’t read the router’s lookups')}</p>
      ) : !devices.data?.receiving ? (
        <p className="text-xs text-slate-500">The router is not sending its log to Jarvis yet.</p>
      ) : (
        <div className="space-y-3">
          <label className="flex min-h-11 items-center gap-2 rounded-xl bg-white/5 border border-white/10 px-3">
            <Search size={14} className="text-slate-500" aria-hidden />
            <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Find a device"
              aria-label="Find a device" className="min-h-11 w-full bg-transparent text-sm text-white outline-none" />
          </label>
          {shown.length === 0 ? (
            <p className="text-xs text-slate-500">{needle ? 'No device matches.' : 'No lookups in this period.'}</p>
          ) : (
            <ul className="space-y-1.5" aria-label="Devices">
              {shown.map((d) => (
                <li key={d.device}>
                  <button type="button" onClick={() => setSelected(d)} aria-label={`Sites ${d.name} looked up`}
                    className="min-h-12 w-full rounded-xl border border-white/5 bg-white/5 px-3 py-2 text-left hover:bg-white/10">
                    <span className="flex items-baseline justify-between gap-2 text-xs">
                      <span className="min-w-0 break-words text-slate-100">{d.name}</span>
                      <span className="shrink-0 font-mono text-slate-300">{d.lookups.toLocaleString()}</span>
                    </span>
                    <span className="block text-[11px] text-slate-500">
                      {[d.ip, d.network, `${d.domains.toLocaleString()} names`, `last ${lastSeen(d.last)}`].filter(Boolean).join(' · ')}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
};

export default SitesSection;
