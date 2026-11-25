import { useCallback, useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useLocation } from 'react-router-dom';
import { Capacitor } from '@capacitor/core';
import { CloudOff, Lock, Radio } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import { errorText } from '../../lib/errorText';
import Nearby, { type NearbyDeviceState } from '../../plugins/nearby';
import {
  NEARBY_DEFAULTS,
  NEARBY_LABELS,
  NEARBY_PARTS,
  NEARBY_QUERY_KEY,
  deviceLine,
  type NearbyPart,
  type NearbyParts,
} from '../../lib/nearbySettings';
import OfflineModePanel from '../wander/OfflineModePanel';

/** One part's switch: its name, a one-line description, and on or off. */
export function NearbySwitch({
  part,
  on,
  disabled,
  onChange,
  testPrefix = 'nearby',
}: {
  part: NearbyPart;
  on: boolean;
  disabled: boolean;
  onChange: (next: boolean) => void;
  testPrefix?: string;
}) {
  const { title, description } = NEARBY_LABELS[part];
  return (
    <div className="flex items-center justify-between gap-3 rounded-xl bg-white/5 p-3" data-testid={`${testPrefix}-row-${part}`}>
      <div className="min-w-0">
        <p className="text-sm font-medium text-white">{title}</p>
        <p className="text-xs text-slate-400">{description}</p>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={on}
        aria-label={title}
        disabled={disabled}
        onClick={() => onChange(!on)}
        className="flex min-h-11 min-w-14 shrink-0 items-center justify-center disabled:opacity-50"
        data-testid={`${testPrefix}-toggle-${part}`}
      >
        {/* The track is drawn small; the button around it is a full thumb's size. */}
        <span className={`relative inline-flex h-7 w-12 items-center rounded-full transition-colors ${on ? 'bg-emerald-500' : 'bg-white/10'}`}>
          <span className={`inline-block h-5 w-5 rounded-full bg-white transition-transform ${on ? 'translate-x-6' : 'translate-x-1'}`} />
        </span>
      </button>
    </div>
  );
}

/**
 * Settings > Nearby & offline: one switch per Bluetooth LE part (Find family
 * nearby, the offline family chat, offline mode, car detection). Grown members
 * choose; a child's are set by a parent in Guardian and shown here read-only,
 * with the parent link always on. Stored on the server; the Android app
 * applies them on the phone (and asks for Nearby devices only when a part that
 * needs it is turned on).
 */
export default function NearbyOfflinePanel() {
  const android = Capacitor.getPlatform() === 'android';
  const queryClient = useQueryClient();
  const location = useLocation();
  const offlineRef = useRef<HTMLDetailsElement | null>(null);
  const [device, setDevice] = useState<NearbyDeviceState | null>(null);
  const [busy, setBusy] = useState(false);

  const { data, isError } = useQuery({
    queryKey: NEARBY_QUERY_KEY,
    queryFn: () => api.getNearbySettings(),
    staleTime: 60_000,
    retry: false,
  });

  const readDevice = useCallback(() => {
    if (!android) return;
    Nearby.getState().then(setDevice).catch(() => setDevice(null));
  }, [android]);

  useEffect(() => {
    readDevice();
    document.addEventListener('visibilitychange', readDevice);
    return () => document.removeEventListener('visibilitychange', readDevice);
  }, [readDevice]);

  // The header's offline line links here: open offline mode and bring it into view.
  useEffect(() => {
    if (location.hash !== '#nearby-offline' && location.hash !== '#offline') return;
    if (location.hash === '#nearby-offline' || !offlineRef.current) {
      document.getElementById('nearby-offline')?.scrollIntoView?.({ block: 'start' });
      return;
    }
    offlineRef.current.open = true;
    offlineRef.current.scrollIntoView?.({ block: 'start' });
  }, [location.hash, data]);

  const parts: NearbyParts = data?.settings ?? NEARBY_DEFAULTS;
  const managed = data?.managed_by_parent === true;
  const parentLink = (data?.always_on ?? []).includes('parent_link');
  const locked = !data || !data.can_change;

  const change = async (part: NearbyPart, on: boolean) => {
    setBusy(true);
    try {
      const next = await api.setNearbySettings({ [part]: on });
      queryClient.setQueryData(NEARBY_QUERY_KEY, next);
      if (android) setDevice(await Nearby.apply({ ...next.settings, ask: on }));
    } catch (e) {
      toast.error(errorText(e, `Could not change ${NEARBY_LABELS[part].title}`));
    } finally {
      setBusy(false);
    }
  };

  const allow = async () => {
    setBusy(true);
    try {
      setDevice(await Nearby.requestPermissions());
    } catch (e) {
      toast.error(errorText(e, 'Could not ask for Nearby devices'));
    } finally {
      setBusy(false);
    }
  };

  const line = deviceLine(device, parts, parentLink);
  const needsPermission = device != null && (!device.scanPermission || !device.connectPermission) && line?.startsWith('Allow');

  return (
    <section id="nearby-offline" className="glass-panel scroll-mt-4 space-y-3 rounded-2xl p-4" aria-labelledby="nearby-offline-heading" data-testid="nearby-offline-panel">
      <div className="px-1">
        <h2 id="nearby-offline-heading" className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wider text-slate-400">
          <Radio size={16} className="text-purple-400" />
          Nearby & offline
        </h2>
        <p className="mt-1 text-xs text-slate-500">
          Bluetooth between family phones: no Wi-Fi or data needed, no pairing, nothing shown to strangers.
          {!android && ' These run on the Jarvis Android app.'}
        </p>
      </div>

      {isError && <p className="text-xs text-amber-300" role="alert">Could not load these settings.</p>}

      {managed && (
        <p className="flex items-start gap-2 rounded-xl bg-white/5 p-3 text-xs text-slate-300" data-testid="nearby-managed">
          <Lock size={14} className="mt-0.5 shrink-0 text-slate-400" aria-hidden />
          <span>A parent manages these on your phone.</span>
        </p>
      )}

      <div className="space-y-2">
        {NEARBY_PARTS.map((part) => (
          <NearbySwitch key={part} part={part} on={parts[part]} disabled={busy || locked} onChange={(on) => void change(part, on)} />
        ))}
      </div>

      {parentLink && (
        <p className="px-1 text-xs text-slate-400" data-testid="nearby-parent-link">
          Parent controls over Bluetooth stay on: a parent can lock, unlock or give more time without internet, and
          Bluetooth being turned off is reported.
        </p>
      )}

      {line && (
        <div className="flex flex-wrap items-center gap-2 px-1" data-testid="nearby-device-line">
          <p className="min-w-0 flex-1 text-xs text-amber-200">{line}</p>
          {needsPermission && (
            <button
              type="button"
              disabled={busy}
              onClick={() => void allow()}
              className="glass-button min-h-11 rounded-xl px-3 text-xs font-semibold disabled:opacity-50"
            >
              Allow Nearby devices
            </button>
          )}
        </div>
      )}

      {android && parts.offline && (
        <details id="offline" ref={offlineRef} className="scroll-mt-4 rounded-xl bg-white/5 p-2" data-testid="nearby-offline-mode">
          <summary className="flex min-h-11 cursor-pointer items-center gap-2 px-1 text-sm font-medium text-white">
            <CloudOff size={15} className="text-sky-300" aria-hidden /> Open offline mode
          </summary>
          <div className="pt-2">
            <OfflineModePanel />
          </div>
        </details>
      )}
    </section>
  );
}
