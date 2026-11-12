import { useId, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Bluetooth, Network } from 'lucide-react';
import AdoptDeviceDialog from './AdoptDeviceDialog';
import MatterWifiSettings, { BluetoothState } from './MatterWifiSettings';
import { commissionMatter } from '../../lib/discoveredDevices';
import type { AdoptResult, DiscoveryInput, HubArea } from '../../lib/discoveredDevices';
import { MATTER_SETTINGS_KEY, getMatterSettings } from '../../lib/matterCommissioning';

const MATTER_CODE: DiscoveryInput = {
  key: 'code',
  label: 'Setup code',
  type: 'text',
  help: 'The QR code text (MT:...) or the 11 or 21 digit pairing code on the device or its box.',
};

type Method = 'network' | 'ble';

const METHODS: { value: Method; label: string; detail: string; icon: typeof Network }[] = [
  { value: 'network', label: 'On the network', detail: 'Already on Wi-Fi, or shared from another app', icon: Network },
  { value: 'ble', label: 'New, over Bluetooth', detail: 'New or reset; not on Wi-Fi yet', icon: Bluetooth },
];

const INTRO: Record<Method, string> = {
  network: 'Put the device in pairing mode on the home network (or share it from the app it is set up in), then enter its setup code. Adding it can take a minute or two.',
  ble: 'The hub finds the device over Bluetooth and gives it the Wi-Fi for new devices. Keep it powered and close to the hub; adding it can take a few minutes.',
};

const input = 'mt-1 min-h-11 w-full rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm text-white';

/**
 * Devices > New devices > Add Matter device: a device already on the network
 * (a share code from another controller, or one in pairing mode on the LAN), or
 * a new Wi-Fi device added over Bluetooth with the household Wi-Fi (or a
 * network for this device only). The hub reports its progress while it runs.
 */
export default function MatterAddDialog({ areas, onDone, onClose }: {
  areas: HubArea[];
  onDone: (result: AdoptResult) => void;
  onClose: () => void;
}) {
  const id = useId();
  const [method, setMethod] = useState<Method>('network');
  const [other, setOther] = useState(false);
  const [ssid, setSsid] = useState('');
  const [password, setPassword] = useState('');
  const ble = method === 'ble';
  const settings = useQuery({ queryKey: MATTER_SETTINGS_KEY, queryFn: getMatterSettings, retry: false, enabled: ble });
  const wifiSet = Boolean(settings.data?.wifi.configured);

  async function submit({ name, areaId, input: values }: { name?: string; areaId?: string; input: Record<string, string> }) {
    if (!ble) return commissionMatter(values.code || '', name, areaId);
    // While it runs the dialog polls with the session; the password is sent once, to start.
    const network = !values.session && other ? { wifi_ssid: ssid, wifi_password: password } : {};
    return commissionMatter(values.code || '', name, areaId, {
      method: 'ble', ...(values.session ? { session: values.session } : {}), ...network,
    });
  }

  const extra = (
    <div className="space-y-3" data-testid="matter-method">
      <div role="radiogroup" aria-label="How is the device connected?" className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {METHODS.map((m) => {
          const Icon = m.icon;
          const on = method === m.value;
          return (
            <button
              key={m.value}
              type="button"
              role="radio"
              aria-checked={on}
              onClick={() => setMethod(m.value)}
              className={`flex min-h-11 items-start gap-2 rounded-xl border px-3 py-2 text-left ${on ? 'border-sky-500/60 bg-sky-500/10' : 'border-white/10 hover:bg-white/5'}`}
            >
              <Icon size={16} className={`mt-0.5 shrink-0 ${on ? 'text-sky-300' : 'text-slate-400'}`} />
              <span className="min-w-0">
                <span className="block text-sm font-semibold text-white">{m.label}</span>
                <span className="block text-xs text-slate-400">{m.detail}</span>
              </span>
            </button>
          );
        })}
      </div>
      {ble && (
        <div className="space-y-2" data-testid="matter-ble">
          <BluetoothState settings={settings.data} />
          {settings.data && !wifiSet && !other && <MatterWifiSettings embedded />}
          {wifiSet && !other && (
            <p className="text-xs text-slate-400" data-testid="matter-ble-network">
              It will join <span className="break-all font-semibold text-slate-200">{settings.data?.wifi.ssid}</span>.
            </p>
          )}
          <label className="flex min-h-11 items-center gap-2 text-xs text-slate-300">
            <input type="checkbox" className="h-5 w-5" checked={other} onChange={(e) => setOther(e.target.checked)} />
            Use another network for this device
          </label>
          {other && (
            <div className="space-y-2">
              <label htmlFor={`${id}-ssid`} className="block text-xs text-slate-400">
                Network name
                <input id={`${id}-ssid`} className={input} value={ssid} onChange={(e) => setSsid(e.target.value)}
                  autoComplete="off" spellCheck={false} />
              </label>
              <label htmlFor={`${id}-password`} className="block text-xs text-slate-400">
                Network password
                <input id={`${id}-password`} className={input} type="password" value={password}
                  onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" />
              </label>
            </div>
          )}
        </div>
      )}
    </div>
  );

  return (
    <AdoptDeviceDialog
      title="Add a Matter device"
      intro={INTRO[method]}
      needs={[MATTER_CODE]}
      submitLabel={ble ? 'Add over Bluetooth' : 'Add'}
      extra={extra}
      areas={areas}
      onSubmit={submit}
      onDone={onDone}
      onClose={onClose}
    />
  );
}
