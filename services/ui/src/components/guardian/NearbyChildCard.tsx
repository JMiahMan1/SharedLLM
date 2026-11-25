import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Radio } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import { errorText } from '../../lib/errorText';
import { NEARBY_DEFAULTS, NEARBY_LABELS, NEARBY_PARTS, type NearbyPart } from '../../lib/nearbySettings';
import { NearbySwitch } from '../settings/NearbyOfflinePanel';

/**
 * Guardian > Safety & Devices: a child's Settings > Nearby & offline, set by a
 * parent (the child sees them read-only). The parent link (offline lock,
 * unlock and extra time, and the Bluetooth-off reports) is always on.
 */
export default function NearbyChildCard({ username, name }: { username: string; name: string }) {
  const queryClient = useQueryClient();
  const key = ['nearby-settings', username];
  const [busy, setBusy] = useState(false);
  const { data } = useQuery({
    queryKey: key,
    queryFn: () => api.getNearbySettings(username),
    retry: false,
    staleTime: 60_000,
  });
  if (!data) return null;

  const change = async (part: NearbyPart, on: boolean) => {
    setBusy(true);
    try {
      queryClient.setQueryData(key, await api.setNearbySettings({ [part]: on }, username));
      toast.success(`${NEARBY_LABELS[part].title} ${on ? 'on' : 'off'} for ${name}`);
    } catch (e) {
      toast.error(errorText(e, `Could not change ${name}'s ${NEARBY_LABELS[part].title}`));
    } finally {
      setBusy(false);
    }
  };

  const parts = data.settings ?? NEARBY_DEFAULTS;
  return (
    <section className="glass-panel min-w-0 space-y-3 rounded-2xl border border-white/10 p-4" aria-labelledby="guardian-nearby-heading" data-testid="guardian-nearby">
      <div>
        <h3 id="guardian-nearby-heading" className="flex items-center gap-2 text-sm font-semibold text-white">
          <Radio size={15} className="text-purple-300" aria-hidden /> Nearby &amp; offline
        </h3>
        <p className="mt-1 text-xs text-slate-400">
          What {name}&apos;s phone does over Bluetooth with family phones. {name} sees these but cannot change them.
        </p>
      </div>
      <div className="space-y-2">
        {NEARBY_PARTS.map((part) => (
          <NearbySwitch key={part} part={part} on={parts[part]} disabled={busy || !data.can_change}
            onChange={(on) => void change(part, on)} testPrefix="guardian-nearby" />
        ))}
      </div>
      <p className="text-xs text-slate-400" data-testid="guardian-nearby-parent-link">
        Always on: your offline lock, unlock and extra time reach {name}&apos;s phone through nearby family phones, and
        Bluetooth being turned off is reported to you.
      </p>
    </section>
  );
}
