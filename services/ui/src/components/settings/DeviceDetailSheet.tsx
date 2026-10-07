import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { Activity, BatteryMedium, Footprints, X } from 'lucide-react';
import { api } from '../../services/api';
import type { CompanionDevice } from '../../types/api';

const TOUCH = 'min-h-11 pointer-coarse:min-h-11';

/**
 * Which step source a device's numbers arrive under, when it sends any.
 *
 * A phone reports as "phone" and a watch as "watch". A light sends nothing,
 * and an assistant only if it happens to run a step counter, so an unmapped
 * kind simply has no steps section rather than an empty one.
 */
const STEP_SOURCE: Record<string, string> = { phone: 'phone', watch: 'watch' };

export interface SparklinePoint {
  /** Position on the x-axis, in epoch milliseconds. */
  at: number;
  value: number;
}

/**
 * A small line chart of values over time.
 *
 * Shared by the battery history and any other series a device reports, so
 * there is one place that decides how a series is drawn. `max` is passed in
 * rather than derived so a battery line always reads against a full 0-100
 * scale; a series that fills the box whatever its values would suggest a
 * healthy battery at 8%.
 */
export function Sparkline({
  points,
  max = 100,
  ariaLabel,
}: {
  points: SparklinePoint[];
  max?: number;
  ariaLabel: string;
}) {
  const t0 = points[0]?.at ?? 0;
  const span = Math.max(1, (points[points.length - 1]?.at ?? 0) - t0);
  const path = points
    .map(
      (p, i) =>
        `${i ? 'L' : 'M'}${(((p.at - t0) / span) * 100).toFixed(1)},${(
          40 - (p.value / max) * 36
        ).toFixed(1)}`,
    )
    .join(' ');
  return (
    <figure className="w-full" aria-label={ariaLabel}>
      <svg
        viewBox="0 0 100 40"
        preserveAspectRatio="none"
        className="w-full h-16 rounded-lg bg-black/30"
        aria-hidden="true"
      >
        <path
          d={path}
          fill="none"
          stroke="currentColor"
          strokeWidth="1.2"
          vectorEffect="non-scaling-stroke"
          className="text-sky-400"
        />
      </svg>
    </figure>
  );
}

/** One line of the device's own record, omitted when the device never said. */
function Fact({ label, value }: { label: string; value?: string | null }) {
  if (!value) return null;
  return (
    <div className="min-w-0">
      <dt className="text-[10px] uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="text-xs text-slate-200 break-words" data-testid={`device-fact-${label}`}>
        {value}
      </dd>
    </div>
  );
}

/** Small scalars only: a nested object would be unreadable in one line. */
function summariseExtra(extra: Record<string, unknown>): string {
  return Object.entries(extra)
    .filter(([, v]) => typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean')
    .map(([k, v]) => `${k}: ${v}`)
    .join(' · ');
}

function StepsSection({ source, userId }: { source: string; userId: string }) {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['step-sources', userId, 7],
    queryFn: () => api.getStepSources(7, false, userId),
    retry: false,
  });
  const days = data?.sources?.[source];
  if (isLoading) return <p className="text-xs text-slate-500">Loading steps…</p>;
  if (isError) {
    return (
      <p className="text-xs text-amber-300/90" data-testid="device-steps-error">
        {error instanceof Error ? error.message : 'Could not read steps for this device.'}
      </p>
    );
  }
  if (!days || Object.keys(days).length === 0) {
    return (
      <p className="text-xs text-slate-500" data-testid="device-steps-empty">
        Nothing reported by this device yet. Step counts appear here after it syncs.
      </p>
    );
  }
  const entries = Object.entries(days).sort(([a], [b]) => (a < b ? -1 : 1));
  const peak = Math.max(1, ...entries.map(([, n]) => n));
  const today = entries[entries.length - 1];
  return (
    <div data-testid="device-steps">
      <div className="flex items-end gap-1.5 h-20">
        {entries.map(([day, steps]) => (
          <div key={day} className="flex-1 flex flex-col items-center gap-1 min-w-0">
            <div
              className="w-full rounded-t bg-emerald-400/70"
              style={{ height: `${Math.max(4, (steps / peak) * 64)}px` }}
              title={`${day}: ${steps.toLocaleString()} steps`}
            />
            <span className="text-[9px] text-slate-500">{day.slice(5)}</span>
          </div>
        ))}
      </div>
      <p className="mt-1 text-[11px] text-slate-400">
        {today[1].toLocaleString()} steps on {today[0]}
        {data?.last_synced ? ' · last sync ' + new Date(data.last_synced * 1000).toLocaleString() : ''}
      </p>
    </div>
  );
}

function BatterySection({ deviceKey }: { deviceKey: string }) {
  const { data = [], isLoading } = useQuery({
    queryKey: ['device-battery', deviceKey],
    queryFn: () => api.getDeviceBattery(deviceKey, 24),
  });
  const points = data
    .filter((r) => r.pct != null)
    .map((r) => ({ at: Date.parse(r.at), value: r.pct as number }));
  if (isLoading) return <p className="text-xs text-slate-500">Loading battery history…</p>;
  if (points.length < 2) {
    return (
      <p className="text-xs text-slate-500" data-testid="device-battery-empty">
        Not enough battery reports in the last day yet.
      </p>
    );
  }
  const last = points[points.length - 1];
  const charging = data.filter((r) => r.usb).length;
  return (
    <div data-testid="device-battery">
      <Sparkline points={points} ariaLabel="Battery, last 24 hours" />
      <p className="mt-1 text-[11px] text-slate-500">
        {points.length} readings · {Math.round(last.value)}% now
        {charging ? ` · ${charging} while charging (no % on USB)` : ''}
      </p>
    </div>
  );
}

function ActivitySection({ deviceKey }: { deviceKey: string }) {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['device-activity', deviceKey],
    queryFn: () => api.getDeviceActivity(deviceKey, 24),
    retry: false,
  });
  if (isLoading) return <p className="text-xs text-slate-500">Loading activity…</p>;
  if (isError || !data) {
    return (
      <p className="text-xs text-amber-300/90" data-testid="device-activity-error">
        {error instanceof Error ? error.message : 'Could not read this device’s activity.'}
      </p>
    );
  }
  const counts = Object.entries(data.counts).sort(([, a], [, b]) => b - a);
  if (counts.length === 0) {
    return (
      <p className="text-xs text-slate-500" data-testid="device-activity-empty">
        No activity recorded in the last day.
      </p>
    );
  }
  return (
    <div data-testid="device-activity">
      <div className="flex flex-wrap gap-1.5">
        {counts.map(([event, n]) => (
          <span
            key={event}
            className="rounded-full border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] text-slate-300"
            data-testid={`device-count-${event}`}
          >
            {event.replace(/_/g, ' ')} · {n}
          </span>
        ))}
      </div>
      <ul className="mt-2 space-y-1">
        {data.events.slice(0, 12).map((e, i) => {
          const extra = summariseExtra(e.extra);
          return (
            <li key={`${e.at}-${i}`} className="text-[11px] text-slate-400 flex flex-wrap gap-x-2">
              <span className="text-slate-300">{e.event.replace(/_/g, ' ')}</span>
              <span className="text-slate-500">{new Date(e.at).toLocaleString()}</span>
              {extra && <span className="text-slate-500">{extra}</span>}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

interface DeviceDetailSheetProps {
  device: CompanionDevice;
  onClose: () => void;
}

/**
 * Everything one device has reported, opened from its row.
 *
 * A row can only show a name and a battery percentage; this is where the rest
 * of what the device syncs lives — its record, its own step counts, its
 * battery over the day, and how it has been used. It stops short of the full
 * picture on purpose and links out to Health and Wander, which are built for
 * reading history across every device rather than one.
 *
 * A sheet rather than a page: the reader is looking up one fact about one
 * device, and closing it should put them back where they were.
 */
export function DeviceDetailSheet({ device, onClose }: DeviceDetailSheetProps) {
  const name =
    device.label ||
    [device.manufacturer, device.model].filter(Boolean).join(' ') ||
    device.device_key;
  const source = STEP_SOURCE[device.kind];
  // Steps belong to a person, not a device, so they are only shown once we know
  // whose they are: an unowned device has no step owner to read, and guessing
  // the caller would attribute their own walk to someone else's watch.
  const stepOwner = device.owner_username || '';
  const caps = Object.keys(device.capabilities ?? {});

  return (
    <div
      className="fixed inset-0 z-50 flex items-end sm:items-center sm:justify-center"
      role="dialog"
      aria-modal="true"
      aria-label={`Details for ${name}`}
    >
      <button
        type="button"
        aria-label="Close device details"
        onClick={onClose}
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        data-testid="device-detail-scrim"
      />
      <div
        className="relative w-full sm:max-w-lg bg-slate-950/97 backdrop-blur-xl border border-white/10 border-b-0 sm:border-b sm:rounded-2xl p-4 pb-[max(1rem,env(safe-area-inset-bottom))] max-h-[85vh] overflow-y-auto"
        data-testid="device-detail-sheet"
      >
        <div className="flex items-start justify-between gap-3 mb-3">
          <div className="min-w-0">
            <p className="flex items-center gap-2 text-sm font-semibold text-slate-100">
              <Activity size={15} className="text-indigo-300" />
              <span className="truncate" data-testid="device-detail-name">
                {name}
              </span>
            </p>
            <p className="text-[11px] text-slate-500 mt-0.5 uppercase tracking-wide">
              {device.kind}
              {device.owner_username ? ` · ${device.owner_username}` : ''}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close device details"
            className={`${TOUCH} min-w-11 shrink-0 flex items-center justify-center rounded-xl text-slate-400 hover:text-white`}
          >
            <X size={16} />
          </button>
        </div>

        <dl className="grid grid-cols-2 gap-x-3 gap-y-2" data-testid="device-detail-facts">
          <Fact label="Model" value={[device.manufacturer, device.model].filter(Boolean).join(' ')} />
          <Fact label="OS" value={device.os_version} />
          <Fact
            label="App"
            value={device.app_version ? `v${device.app_version}${device.app_build ? ` (${device.app_build})` : ''}` : null}
          />
          <Fact label="Added" value={device.registered_by} />
          <Fact
            label="Last seen"
            value={device.last_seen_at ? new Date(device.last_seen_at).toLocaleString() : null}
          />
          <Fact
            label="First seen"
            value={device.first_seen_at ? new Date(device.first_seen_at).toLocaleString() : null}
          />
          <Fact label="IP" value={device.last_ip_address} />
          <Fact label="Firmware" value={device.esphome_version ?? device.hardware} />
        </dl>

        {caps.length > 0 && (
          <p className="mt-2 text-[11px] text-slate-500" data-testid="device-detail-capabilities">
            Reports: {caps.join(', ')}
          </p>
        )}

        {source && stepOwner && (
          <section className="mt-4">
            <h3 className="flex items-center gap-1.5 text-xs font-semibold text-emerald-300 mb-1.5">
              <Footprints size={13} />
              Steps from this device
            </h3>
            <StepsSection source={source} userId={stepOwner} />
          </section>
        )}

        <section className="mt-4">
          <h3 className="flex items-center gap-1.5 text-xs font-semibold text-sky-300 mb-1.5">
            <BatteryMedium size={13} />
            Battery
          </h3>
          <BatterySection deviceKey={device.device_key} />
        </section>

        <section className="mt-4">
          <h3 className="flex items-center gap-1.5 text-xs font-semibold text-indigo-300 mb-1.5">
            <Activity size={13} />
            Activity, last 24 hours
          </h3>
          <ActivitySection deviceKey={device.device_key} />
        </section>

        <div className="mt-4 flex flex-wrap gap-2 border-t border-white/10 pt-3">
          <Link
            to="/fitness"
            onClick={onClose}
            className={`${TOUCH} flex flex-1 items-center justify-center rounded-xl border border-white/10 bg-white/5 px-3 text-xs text-slate-200`}
            data-testid="device-link-health"
          >
            All health data
          </Link>
          <Link
            to="/wander"
            onClick={onClose}
            className={`${TOUCH} flex flex-1 items-center justify-center rounded-xl border border-white/10 bg-white/5 px-3 text-xs text-slate-200`}
            data-testid="device-link-wander"
          >
            Open Wander
          </Link>
        </div>
      </div>
    </div>
  );
}
