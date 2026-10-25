import { useQuery } from '@tanstack/react-query';
import { isAxiosError } from 'axios';
import { BatteryMedium, Check, Cpu, KeyRound, Lightbulb, Mic, ShieldAlert, Smartphone, Tablet, Watch, X } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { api } from '../../../services/api';
import MemberCard from '../MemberCard';
import { relativeTime } from '../../../lib/wanderTrips';
import { deviceKindLabel } from '../../../lib/deviceKind';
import type { MemberDevice, MemberGuardState } from '../../../types/api';
import { tamperText } from '../../../lib/guardTamper';

const KIND_ICON: Record<string, LucideIcon> = {
  phone: Smartphone,
  tablet: Tablet,
  watch: Watch,
  assistant: Mic,
  light: Lightbulb,
};

const GUARD_ROWS: Array<{ key: keyof MemberGuardState; label: string }> = [
  { key: 'adminActive', label: 'Device admin (no plain uninstall)' },
  { key: 'deviceOwner', label: 'Device owner' },
  { key: 'guardEnabled', label: 'Accessibility screen guard' },
  { key: 'webFilterAllowed', label: 'Web filter VPN allowed' },
  { key: 'webFilterActive', label: 'Web filter running' },
  { key: 'usageAccess', label: 'Usage access' },
];

function State({ on }: { on: boolean | undefined }) {
  if (on === undefined) return <span className="text-slate-500">not reported</span>;
  return on ? (
    <span className="inline-flex items-center gap-1 text-emerald-300"><Check size={12} /> on</span>
  ) : (
    <span className="inline-flex items-center gap-1 text-amber-300"><X size={12} /> off</span>
  );
}

function DeviceCard({ device }: { device: MemberDevice }) {
  const guard = device.capabilities?.guard;
  const facts: Array<[string, string]> = [
    ['Model', [device.manufacturer, device.model].filter(Boolean).join(' ') || 'unknown'],
    ['Android', [device.os_version, device.os_build].filter(Boolean).join(' · ') || 'unknown'],
    ['Jarvis app', device.app_version ? `${device.app_version}${device.app_build ? ` (build ${device.app_build})` : ''}` : 'unknown'],
    ['Last seen', device.last_seen_at ? relativeTime(device.last_seen_at) : 'never'],
    ['Address', device.last_ip_address || 'unknown'],
    ['First seen', device.first_seen_at ? new Date(device.first_seen_at).toLocaleDateString() : 'unknown'],
  ];
  return (
    <MemberCard
      title={device.label || device.model || deviceKindLabel(device.kind)}
      icon={KIND_ICON[device.kind] ?? Cpu}
      testId={`member-device-${device.device_key}`}
      aside={
        device.battery?.pct !== null && device.battery?.pct !== undefined ? (
          <span className="inline-flex items-center gap-1" title={`Reported ${relativeTime(device.battery.at)}`}>
            <BatteryMedium size={14} /> {Math.round(device.battery.pct)}%{device.battery.usb ? ' charging' : ''}
          </span>
        ) : (
          'no battery report'
        )
      }
    >
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        {facts.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-slate-500">{k}</dt>
            <dd className="min-w-0 break-words text-slate-200">{v}</dd>
          </div>
        ))}
      </dl>
      <div className="space-y-1 border-t border-white/5 pt-2 text-xs">
        <p className="text-[10px] uppercase tracking-wider text-slate-500">
          Protection {guard?.reportedAt ? `· reported ${relativeTime(guard.reportedAt)}` : ''}
        </p>
        {guard ? (
          <ul className="space-y-1" data-testid="member-device-guard">
            {GUARD_ROWS.map((row) => (
              <li key={row.key} className="flex justify-between gap-2">
                <span className="text-slate-300">{row.label}</span>
                <State on={guard[row.key] as boolean | undefined} />
              </li>
            ))}
            {guard.privateDnsHost !== undefined && (
              <li className="flex justify-between gap-2">
                <span className="text-slate-300">Private DNS provider</span>
                {guard.privateDnsHost ? (
                  <span className="min-w-0 break-words text-amber-300">{guard.privateDnsHost} (gets around the filter)</span>
                ) : (
                  <span className="text-emerald-300">none</span>
                )}
              </li>
            )}
            {guard.bypassApps !== undefined && (
              <li className="flex justify-between gap-2" data-testid="member-device-bypass">
                <span className="text-slate-300">Apps that get around the filter</span>
                {guard.bypassApps.length ? (
                  <span className="min-w-0 break-words text-right text-amber-300">{guard.bypassApps.join(', ')}</span>
                ) : (
                  <span className="text-emerald-300">none</span>
                )}
              </li>
            )}
          </ul>
        ) : (
          <p className="text-slate-500">This device has not reported its protection yet (it does from the next app update).</p>
        )}
      </div>
    </MemberCard>
  );
}

/**
 * Every device of the child with its technical side (the father's and an
 * admin's only; the server refuses anyone else, the mother included).
 */
export default function DevicesSection({ username, name }: { username: string; name: string }) {
  const { data, isLoading, error } = useQuery({ queryKey: ['member-devices', username], queryFn: () => api.getMemberDevices(username), retry: false });
  const refused = isAxiosError(error) && error.response?.status === 403;
  if (isLoading || error || !data || data.devices.length === 0) {
    return (
      <MemberCard
        title="Devices"
        icon={Smartphone}
        testId="member-devices"
        wide
        isLoading={isLoading}
        error={refused ? 'Device details are for the father and admins.' : error ? 'Devices could not be loaded.' : null}
        empty={data && data.devices.length === 0 ? `${name} has no registered devices yet. A phone or tablet registers when they sign in to the Jarvis app.` : null}
      />
    );
  }
  const parents = Object.entries(data.offline_code);
  return (
    <>
      {data.devices.map((d) => (
        <DeviceCard key={d.device_key} device={d} />
      ))}
      <MemberCard
        title="Tamper reports"
        icon={ShieldAlert}
        tone="text-red-300"
        testId="member-tamper"
        empty={data.tamper.length === 0 ? 'No protection changes reported.' : null}
      >
        <ul className="space-y-1.5 text-xs">
          {data.tamper.map((t, i) => (
            <li key={`${t.at}-${i}`} className="flex justify-between gap-2">
              <span className="min-w-0 break-words text-slate-200">{tamperText(t.extra)}</span>
              <span className="shrink-0 text-[10px] text-slate-500">{relativeTime(t.at)}</span>
            </li>
          ))}
        </ul>
      </MemberCard>
      <MemberCard title="Offline unlock codes" icon={KeyRound} tone="text-amber-300" testId="member-offline-codes" empty={parents.length === 0 ? 'No parents linked.' : null}>
        <ul className="space-y-1.5 text-xs">
          {parents.map(([parent, set]) => (
            <li key={parent} className="flex justify-between gap-2">
              <span className="capitalize text-slate-200">{parent}</span>
              <State on={set} />
            </li>
          ))}
        </ul>
      </MemberCard>
    </>
  );
}
