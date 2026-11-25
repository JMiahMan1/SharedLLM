import { useState, type FC } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Mic, RefreshCw, Speaker, Tv } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import type { HubDevice, UserProfile } from '../../types/api';
import { errorText } from '../../lib/errorText';

/**
 * Admin > Users & Devices > Voice satellites, speakers & TVs.
 *
 * Every one HomeHub knows appears here by itself (identity hub_devices.py,
 * keyed by its hardware, so a rename keeps the assignment). Assigning one to a
 * person means a spoken request there that Jarvis does not recognise by voice
 * is answered as that person; "Shared" falls through to voice recognition and
 * then the default user. A room is optional.
 */

const KIND_WORDS: Record<HubDevice['kind'], string> = { voice_satellite: 'Voice satellite', speaker: 'Speaker', tv: 'TV' };

function KindIcon({ kind }: { kind: HubDevice['kind'] }) {
  if (kind === 'voice_satellite') return <Mic size={16} className="text-pink-300" aria-hidden />;
  if (kind === 'tv') return <Tv size={16} className="text-sky-300" aria-hidden />;
  return <Speaker size={16} className="text-emerald-300" aria-hidden />;
}

function RoomField({ device, onSave, busy }: { device: HubDevice; onSave: (room: string | null) => void; busy: boolean }) {
  const [room, setRoom] = useState(device.room ?? '');
  const changed = room.trim() !== (device.room ?? '');
  return (
    <form
      className="flex min-w-0 gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (changed) onSave(room.trim() || null);
      }}
    >
      <input
        value={room}
        onChange={(e) => setRoom(e.target.value)}
        placeholder={device.hub_area ? `Room (${device.hub_area})` : 'Room (optional)'}
        aria-label={`Room for ${device.label}`}
        maxLength={60}
        className="glass-input min-h-11 min-w-0 flex-1 bg-black/30 px-3 text-sm"
      />
      {changed && (
        <button type="submit" disabled={busy} className="glass-button min-h-11 shrink-0 px-3 text-xs font-bold uppercase tracking-widest disabled:opacity-50">
          Save
        </button>
      )}
    </form>
  );
}

export const HubDevicesPanel: FC<{ users: UserProfile[] }> = ({ users }) => {
  const queryClient = useQueryClient();
  const [kind, setKind] = useState<'all' | HubDevice['kind']>('voice_satellite');
  const { data: devices = [], isPending, isError, error } = useQuery({ queryKey: ['hub-devices'], queryFn: () => api.getHubDevices() });
  const people = users.filter((u) => !u.is_system_default && !u.is_test_account);

  const refresh = useMutation({
    mutationFn: () => api.refreshHubDevices(),
    onSuccess: (list) => {
      queryClient.setQueryData(['hub-devices'], list);
      toast.success(`HomeHub reported ${list.length} device${list.length === 1 ? '' : 's'}`);
    },
    onError: (e) => toast.error(errorText(e, 'Could not reach HomeHub')),
  });
  const assign = useMutation({
    mutationFn: ({ key, change }: { key: string; change: { owner_username?: string | null; room?: string | null } }) =>
      api.assignHubDevice(key, change),
    onSuccess: (row) => {
      queryClient.setQueryData<HubDevice[]>(['hub-devices'], (old) => (old ?? []).map((d) => (d.device_key === row.device_key ? row : d)));
      toast.success(row.owner_name ? `${row.label} is ${row.owner_name}'s` : `${row.label} is shared`);
    },
    onError: (e) => toast.error(errorText(e, 'Could not save that')),
  });

  const shown = devices.filter((d) => !d.archived_at && (kind === 'all' || d.kind === kind));
  const counts = (k: HubDevice['kind']) => devices.filter((d) => !d.archived_at && d.kind === k).length;

  return (
    <section className="glass-panel min-w-0 overflow-hidden p-4 sm:p-6" data-testid="hub-devices-panel">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="flex items-center gap-3 text-xl font-bold text-white">
            <Mic size={20} className="text-pink-300" />
            Voice satellites, speakers &amp; TVs
          </h3>
          <p className="mt-1 text-sm text-slate-400">
            Assign a device to the person who uses it (a bedroom satellite to that child). When Jarvis does not
            recognise the voice, it answers as that person. Shared devices answer as whoever Jarvis recognises,
            otherwise the default user.
          </p>
        </div>
        <button
          type="button"
          onClick={() => refresh.mutate()}
          disabled={refresh.isPending}
          className="glass-button flex min-h-11 shrink-0 items-center gap-2 px-3 text-[10px] font-black uppercase tracking-widest disabled:opacity-50"
        >
          <RefreshCw size={14} className={refresh.isPending ? 'animate-spin' : ''} /> Refresh from HomeHub
        </button>
      </div>

      <div className="mb-4 flex flex-wrap gap-2" role="tablist" aria-label="Device kind">
        {(['voice_satellite', 'speaker', 'tv', 'all'] as const).map((k) => (
          <button
            key={k}
            type="button"
            role="tab"
            aria-selected={kind === k}
            onClick={() => setKind(k)}
            className={`min-h-11 rounded-full border px-3 text-xs ${kind === k ? 'border-indigo-400/60 bg-indigo-500/20 text-white' : 'border-white/10 text-slate-300'}`}
          >
            {k === 'all' ? `All (${devices.filter((d) => !d.archived_at).length})` : `${KIND_WORDS[k]}s (${counts(k)})`}
          </button>
        ))}
      </div>

      {isError && <p className="rounded-xl border border-rose-500/30 bg-rose-500/10 p-3 text-sm text-rose-200">{errorText(error, 'Could not load the devices')}</p>}
      {isPending && <p className="text-sm text-slate-400">Loading…</p>}
      {!isPending && !isError && !shown.length && (
        <p className="rounded-2xl border border-white/5 bg-white/5 px-4 py-6 text-center text-sm text-slate-500">
          {devices.length ? 'None of this kind.' : 'No devices from HomeHub yet. Refresh to read them now.'}
        </p>
      )}

      <ul className="space-y-3">
        {shown.map((d) => (
          <li key={d.device_key} className="glass-card flex min-w-0 flex-col gap-3 p-4 lg:flex-row lg:items-center" data-testid="hub-device">
            <div className="min-w-0 flex-1">
              <p className="flex items-center gap-2 font-semibold text-white">
                <KindIcon kind={d.kind} />
                <span className="truncate">{d.label}</span>
              </p>
              <p className="mt-0.5 truncate text-xs text-slate-400">
                {KIND_WORDS[d.kind]}
                {d.hub_area ? ` · ${d.hub_area}` : ''}
                {d.model ? ` · ${d.model}` : ''}
              </p>
            </div>
            <div className="grid min-w-0 grid-cols-1 gap-2 sm:grid-cols-2 lg:w-[28rem]">
              <select
                value={d.owner_username ?? ''}
                aria-label={`Who uses ${d.label}`}
                disabled={assign.isPending}
                onChange={(e) => assign.mutate({ key: d.device_key, change: { owner_username: e.target.value || null } })}
                className="glass-input min-h-11 min-w-0 bg-black/30 px-3 text-sm"
              >
                <option value="">Shared</option>
                {people.map((u) => (
                  <option key={u.username} value={u.username}>
                    {u.display_name || u.full_name || u.username}
                  </option>
                ))}
              </select>
              <RoomField key={`${d.device_key}:${d.room ?? ''}`} device={d} busy={assign.isPending}
                onSave={(room) => assign.mutate({ key: d.device_key, change: { room } })} />
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
};

export default HubDevicesPanel;
