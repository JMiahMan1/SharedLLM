import { useMemo, useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { CircleOff, Power } from 'lucide-react';
import { getHistory, type HubEntity } from '../../../lib/dashboards';
import { numericHistory, readingText, words } from '../../../lib/deviceTiles';

/** What every control in the library takes. `e` is the entity with any pending change drawn on it. */
export interface ControlProps {
  e: HubEntity;
  /** The device's name, for labels ("Desk bulb brightness"). */
  name: string;
  /** Offline or turned off: drawn, not usable. */
  disabled: boolean;
  /** Run a service on this entity (drawn at once, rolled back if refused). */
  send: (service: string, data?: Record<string, unknown>) => Promise<boolean>;
  /** Run a service on another entity of the same device (its remote, its siren). */
  call: (domain: string, service: string, entityId: string, data?: Record<string, unknown>) => Promise<boolean>;
  /** The device's other entities, live. */
  siblings: HubEntity[];
}

export const ROUND = 'flex h-14 w-14 shrink-0 items-center justify-center rounded-full border border-white/10 bg-white/[0.06] text-slate-100 outline-none hover:bg-white/10 active:bg-white/15 focus-visible:ring-2 focus-visible:ring-sky-400 disabled:opacity-40';
export const WIDE = 'flex min-h-12 flex-1 items-center justify-center gap-2 rounded-2xl border border-white/10 bg-white/[0.06] px-3 text-sm font-semibold text-slate-100 outline-none hover:bg-white/10 active:bg-white/15 focus-visible:ring-2 focus-visible:ring-sky-400 disabled:opacity-40';
export const PANEL = 'rounded-2xl border border-white/10 bg-white/[0.04] p-4';
export const LABEL = 'text-xs font-medium text-slate-400';
export const SELECT = 'mt-1 min-h-12 w-full rounded-2xl border border-white/10 bg-black/30 px-3 text-sm text-white disabled:opacity-40';
export const AMBER = 'rgb(251, 191, 36)';
export const SKY = 'rgb(56, 189, 248)';
export const GREEN = 'rgb(52, 211, 153)';

/** The big on/off control: a full-width switch with the state in words. */
export function PowerRow({ on, name, disabled, accent = AMBER, onToggle, label, title, testId = 'main-power' }: {
  on: boolean; name: string; disabled?: boolean; accent?: string; onToggle: () => void; label?: string;
  /** What is switched, when it is not the device itself ("Floodlight"); the state goes under it. */
  title?: string; testId?: string;
}) {
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label ?? `${name} power`} disabled={disabled}
      onClick={onToggle} data-testid={testId}
      className="flex min-h-14 w-full items-center justify-between gap-3 rounded-2xl border border-white/10 bg-white/[0.06] px-4 text-left outline-none hover:bg-white/10 focus-visible:ring-2 focus-visible:ring-sky-400 disabled:cursor-not-allowed disabled:opacity-40">
      <span className="flex items-center gap-3">
        <Power size={20} style={on ? { color: accent } : undefined} className={on ? '' : 'text-slate-400'} aria-hidden />
        {title ? (
          <span className="min-w-0">
            <span className="block truncate text-base font-semibold text-white">{title}</span>
            <span className="block text-xs text-slate-400">{on ? 'On' : 'Off'}</span>
          </span>
        ) : <span className="text-base font-semibold text-white">{on ? 'On' : 'Off'}</span>}
      </span>
      <span className="relative h-8 w-14 shrink-0 rounded-full motion-safe:transition-colors"
        style={{ backgroundColor: on ? accent : 'rgba(148, 163, 184, 0.35)' }} aria-hidden>
        <span className={`absolute top-1 h-6 w-6 rounded-full bg-white shadow motion-safe:transition-transform ${on ? 'translate-x-7' : 'translate-x-1'}`} />
      </span>
    </button>
  );
}

/**
 * A choice among a few named options (fan speed, preset, effect): chips when there are up to six,
 * a dropdown when there are more. Options are shown in words ("slow_pulse" -> "Slow pulse").
 */
export function OptionPicker({ label, options, value, onPick, disabled, testId }: {
  label: string; options: string[]; value: string; onPick: (v: string) => void; disabled?: boolean; testId?: string;
}) {
  if (!options.length) return null;
  if (options.length > 6) {
    return (
      <label className={`block ${LABEL}`} data-testid={testId}>
        {label}
        <select className={SELECT} value={options.includes(value) ? value : ''} disabled={disabled}
          onChange={(ev) => onPick(ev.target.value)}>
          {!options.includes(value) && <option value="" disabled>Choose</option>}
          {options.map((o) => <option key={o} value={o}>{words(o)}</option>)}
        </select>
      </label>
    );
  }
  return (
    <div data-testid={testId}>
      <p className={LABEL}>{label}</p>
      <div role="group" aria-label={label} className="mt-1 flex flex-wrap gap-2">
        {options.map((o) => (
          <button key={o} type="button" aria-pressed={o === value} disabled={disabled} onClick={() => onPick(o)}
            className={`min-h-11 rounded-xl border px-3.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-sky-400 disabled:opacity-40 ${
              o === value ? 'border-sky-400/60 bg-sky-500/15 font-semibold text-sky-100' : 'border-white/10 bg-white/[0.04] text-slate-200 hover:bg-white/10'}`}>
            {words(o)}
          </button>
        ))}
      </div>
    </div>
  );
}

/** When the device has not given the data a part of its control needs. */
export function NotAvailable({ children, testId }: { children: ReactNode; testId?: string }) {
  return (
    <p className="flex items-center gap-2 rounded-2xl border border-dashed border-white/10 px-3 py-3 text-sm text-slate-400"
      data-testid={testId ?? 'not-available'}>
      <CircleOff size={16} className="shrink-0 text-slate-500" aria-hidden /> {children}
    </p>
  );
}

/** A labelled reading: "Power  12 W". */
export function Reading({ label, entity, big }: { label?: string; entity: HubEntity; big?: boolean }) {
  return (
    <div className="min-w-0" data-testid="reading" data-entity={entity.entity_id}>
      <p className={`tabular-nums text-white ${big ? 'text-2xl font-semibold' : 'text-base font-semibold'}`}>{readingText(entity)}</p>
      <p className="truncate text-xs text-slate-400">{label ?? (entity.device_class ? words(entity.device_class) : entity.name)}</p>
    </div>
  );
}

/** A small line chart of an entity's last day, from the hub's history; nothing when there is none. */
export function History({ entityId, unit, colour = SKY }: { entityId: string; unit: string | null; colour?: string }) {
  const [range] = useState(() => { const end = new Date(); return { start: new Date(end.getTime() - 24 * 3600_000), end }; });
  const hist = useQuery({
    queryKey: ['device-history', entityId], staleTime: 60_000, retry: false,
    queryFn: () => getHistory([entityId], range.start, range.end),
  });
  const pts = useMemo(() => numericHistory(hist.data?.[entityId] ?? []), [hist.data, entityId]);
  if (pts.length < 2) return null;
  const w = 300;
  const h = 64;
  const t0 = range.start.getTime();
  const t1 = range.end.getTime();
  const vs = pts.map((p) => p.v);
  const lo = Math.min(...vs);
  const hi = Math.max(...vs);
  const y = (v: number) => (hi === lo ? h / 2 : h - 4 - ((v - lo) / (hi - lo)) * (h - 8));
  const x = (t: number) => Math.max(0, Math.min(w, ((t - t0) / (t1 - t0)) * w));
  const d = pts.map((p, i) => `${i ? 'L' : 'M'}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join(' ')
    + ` L${w},${y(pts[pts.length - 1].v).toFixed(1)}`;
  const fmt = (v: number) => `${Math.round(v * 10) / 10}${unit ? ` ${unit}` : ''}`;
  return (
    <figure className="mt-3" data-testid="sensor-history">
      <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className="h-16 w-full" role="img"
        aria-label={`Last 24 hours: between ${fmt(lo)} and ${fmt(hi)}`}>
        <path d={`${d} L${w},${h} L0,${h} Z`} fill={colour} opacity="0.12" />
        <path d={d} fill="none" stroke={colour} strokeWidth="2" vectorEffect="non-scaling-stroke" />
      </svg>
      <figcaption className="mt-1 flex justify-between gap-2 text-[11px] text-slate-500">
        <span>24 h ago</span><span className="truncate">Low {fmt(lo)} · High {fmt(hi)}</span><span>Now</span>
      </figcaption>
    </figure>
  );
}
