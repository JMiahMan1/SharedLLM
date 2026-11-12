import { useId, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { Bluetooth, BluetoothOff, Loader2, Wifi } from 'lucide-react';
import { failureText } from '../../lib/discoveredDevices';
import {
  MATTER_SETTINGS_KEY,
  clearMatterWifi,
  getMatterSettings,
  setMatterWifi,
  wifiProblem,
} from '../../lib/matterCommissioning';
import type { MatterSettings } from '../../lib/matterCommissioning';
import { confirmDialog } from '../ui/confirmDialog';

const button = 'inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-white/10 px-3 text-xs text-slate-200 hover:bg-white/5 disabled:opacity-50';
const input = 'mt-1 min-h-11 w-full rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm text-white';

export function BluetoothState({ settings }: { settings?: MatterSettings }) {
  const enabled = settings?.bluetooth.enabled;
  if (enabled === true) {
    return (
      <p className="flex items-center gap-1.5 text-xs text-emerald-300" data-testid="matter-bluetooth">
        <Bluetooth size={14} /> The Matter Server's Bluetooth is ready.
      </p>
    );
  }
  if (enabled === false) {
    return (
      <p className="flex items-start gap-1.5 text-xs text-amber-300" data-testid="matter-bluetooth">
        <BluetoothOff size={14} className="mt-0.5 shrink-0" />
        <span>
          The Matter Server has no Bluetooth adapter, so new devices cannot be added over Bluetooth. Set{' '}
          <code className="break-all">{settings?.bluetooth.setting || 'MATTER_BLUETOOTH_ADAPTER'}</code> on the server.
        </span>
      </p>
    );
  }
  return (
    <p className="text-xs text-slate-400" data-testid="matter-bluetooth">
      {settings ? 'The Matter Server is not connected, so its Bluetooth is unknown.' : 'Checking Bluetooth…'}
    </p>
  );
}

/**
 * Devices > New devices > Wi-Fi for new devices (admins): the network a new
 * Matter device is given when it is added over Bluetooth. The password is
 * write-only: the hub says whether one is saved, and the network name.
 */
export default function MatterWifiSettings({ embedded = false }: { embedded?: boolean }) {
  const id = useId();
  const queryClient = useQueryClient();
  const settings = useQuery({ queryKey: MATTER_SETTINGS_KEY, queryFn: getMatterSettings, retry: false });
  const wifi = settings.data?.wifi;
  const [editing, setEditing] = useState(false);
  const [ssid, setSsid] = useState('');
  const [password, setPassword] = useState('');
  const [problem, setProblem] = useState<string | null>(null);
  const showForm = editing || (settings.data && !wifi?.configured);

  const save = useMutation({
    mutationFn: () => setMatterWifi(ssid, password),
    onSuccess: (status) => {
      queryClient.setQueryData<MatterSettings>(MATTER_SETTINGS_KEY, (old) => (old ? { ...old, wifi: status } : old));
      setPassword('');
      setEditing(false);
      toast.success(`New devices will join ${status.ssid}.`);
    },
    onError: (err) => setProblem(failureText(err)),
  });
  const forget = useMutation({
    mutationFn: clearMatterWifi,
    onSuccess: (status) => {
      queryClient.setQueryData<MatterSettings>(MATTER_SETTINGS_KEY, (old) => (old ? { ...old, wifi: status } : old));
      toast.success('The Wi-Fi for new devices was forgotten.');
    },
    onError: (err) => toast.error(failureText(err)),
  });

  function submit() {
    const wrong = wifiProblem(ssid, password);
    setProblem(wrong);
    if (!wrong) save.mutate();
  }

  async function confirmForget() {
    if (await confirmDialog({
      title: 'Forget the Wi-Fi for new devices?',
      body: 'Devices already added keep working. New devices cannot be added over Bluetooth until a network is set again.',
      confirmLabel: 'Forget',
      danger: true,
    })) forget.mutate();
  }

  return (
    <div
      className={embedded ? 'space-y-2 rounded-xl border border-white/10 p-3' : 'space-y-3 border-b border-white/5 px-4 py-3'}
      data-testid="matter-wifi"
    >
      <p className="flex items-center gap-1.5 text-xs font-semibold text-slate-200">
        <Wifi size={14} /> Wi-Fi for new devices
      </p>
      {!embedded && (
        <p className="text-xs text-slate-400">
          A new Matter device added over Bluetooth is given this network to join. Use the household 2.4 GHz Wi-Fi.
        </p>
      )}
      {!embedded && <BluetoothState settings={settings.data} />}
      {settings.isLoading && <p className="text-xs text-slate-500">Loading…</p>}
      {settings.error && <p className="text-xs text-amber-300/90" role="alert">{failureText(settings.error)}</p>}

      {wifi?.configured && !editing && (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="min-w-0 text-sm text-slate-200" data-testid="matter-wifi-current">
            <span className="break-all font-semibold">{wifi.ssid}</span>
            <span className="block text-xs text-slate-400">Password saved</span>
          </p>
          <span className="flex gap-2">
            <button type="button" className={button} onClick={() => { setSsid(wifi.ssid || ''); setPassword(''); setProblem(null); setEditing(true); }}>
              Change
            </button>
            <button type="button" className={button} onClick={() => void confirmForget()} disabled={forget.isPending}>
              Forget
            </button>
          </span>
        </div>
      )}

      {showForm && (
        // Not a <form>: this also sits inside the Add dialog's form, and forms cannot nest.
        <div className="space-y-2" data-testid="matter-wifi-form">
          {!wifi?.configured && <p className="text-xs text-slate-400">No network is set yet.</p>}
          <label htmlFor={`${id}-ssid`} className="block text-xs text-slate-400">
            Network name
            <input id={`${id}-ssid`} className={input} value={ssid} onChange={(e) => setSsid(e.target.value)}
              autoComplete="off" spellCheck={false} />
          </label>
          <label htmlFor={`${id}-password`} className="block text-xs text-slate-400">
            Password
            <input id={`${id}-password`} className={input} type="password" value={password}
              onChange={(e) => setPassword(e.target.value)} autoComplete="new-password"
              onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); submit(); } }} />
          </label>
          {problem && <p className="text-xs text-amber-300" role="alert">{problem}</p>}
          <span className="flex flex-wrap justify-end gap-2">
            {wifi?.configured && (
              <button type="button" className={button} onClick={() => { setEditing(false); setPassword(''); setProblem(null); }}>
                Cancel
              </button>
            )}
            <button
              type="button"
              onClick={submit}
              disabled={save.isPending}
              className="inline-flex min-h-11 items-center gap-1.5 rounded-lg bg-sky-600 px-3 text-xs font-semibold text-white disabled:opacity-50"
            >
              {save.isPending && <Loader2 size={14} className="animate-spin" />} Save Wi-Fi
            </button>
          </span>
        </div>
      )}
    </div>
  );
}
