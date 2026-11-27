import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { Radio } from 'lucide-react';
import { failureText } from '../../lib/discoveredDevices';
import { getListeners, listenerLabel, updateListener, type BluetoothListener } from '../../lib/roomMap';

const KEY = ['bluetooth-listeners'];

/**
 * Devices > Room detection > Bluetooth listeners: which room each of the hub's listeners counts for.
 * The server's own adapter has no device page, so this is the only place its room is set; an
 * ESPHome proxy takes its device's room unless one is chosen here.
 */
export default function BluetoothListeners({ areas }: { areas: Array<{ area_id: string; name: string }> }) {
  const queryClient = useQueryClient();
  const listeners = useQuery({ queryKey: KEY, queryFn: getListeners, retry: false });
  const save = useMutation({
    mutationFn: ({ id, area }: { id: string; area: string | null }) => updateListener(id, { area_id: area }),
    onSuccess: (_r, { area }) => {
      toast.success(area ? `Counts for ${areas.find((a) => a.area_id === area)?.name ?? 'that room'} now.` : 'Uses its own room again.');
      void queryClient.invalidateQueries({ queryKey: KEY });
      void queryClient.invalidateQueries({ queryKey: ['room-map'] });
    },
    onError: (err) => toast.error(failureText(err)),
  });

  const rows: BluetoothListener[] = listeners.data?.scanners ?? [];
  return (
    <div data-testid="bluetooth-listeners">
      <h3 className="flex items-center gap-2 text-sm font-semibold text-white"><Radio size={16} className="text-sky-400" /> Bluetooth listeners</h3>
      <p className="mt-1 text-xs text-slate-400">
        Each room you want told apart needs a listener. Give each one the room it is in.
      </p>
      {listeners.error ? (
        <p className="mt-2 text-xs text-amber-300/90">{failureText(listeners.error)}</p>
      ) : listeners.isPending ? (
        <p className="mt-2 text-xs text-slate-500">Reading the hub's listeners…</p>
      ) : rows.length === 0 ? (
        <p className="mt-2 text-xs text-slate-400">The hub has no Bluetooth listeners yet.</p>
      ) : (
        <ul className="mt-2 divide-y divide-white/5 rounded-xl bg-white/5 px-3">
          {rows.map((l) => {
            const label = listenerLabel(l);
            return (
              <li key={l.id} className="flex flex-col gap-2 py-2.5 sm:flex-row sm:items-center sm:justify-between" data-testid="bluetooth-listener">
                <span className="min-w-0">
                  <span className="block truncate text-sm text-slate-200">{label}</span>
                  <span className="block text-xs text-slate-500">
                    {l.online ? 'Listening' : 'Not listening'}
                    {typeof l.advertisements === 'number' ? ` · ${l.advertisements.toLocaleString()} heard` : ''}
                    {!l.area_id ? ' · no room yet' : ''}
                  </span>
                </span>
                <select
                  className="min-h-11 rounded-lg border border-white/10 bg-slate-900/60 px-2 text-sm text-white sm:w-48"
                  aria-label={`Room of ${label}`}
                  value={l.area_id ?? ''}
                  disabled={save.isPending}
                  onChange={(e) => save.mutate({ id: l.id, area: e.target.value || null })}
                >
                  <option value="">No room</option>
                  {areas.map((a) => <option key={a.area_id} value={a.area_id}>{a.name}</option>)}
                </select>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
