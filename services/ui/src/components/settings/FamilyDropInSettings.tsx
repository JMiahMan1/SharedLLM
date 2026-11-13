import { useCallback, useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { AlertTriangle, CheckCircle2, PhoneIncoming } from 'lucide-react';
import Toggle from '../ui/Toggle';
import { api } from '../../services/api';
import { getDeviceKey } from '../../lib/deviceRegistration';
import { storeDeviceDropIn } from '../../lib/callSettings';
import { nativeDropInStatus, openOverlaySettings } from '../../lib/dropIn';
import type { DropInPerson, DropInSetting, DropInSettings } from '../../types/api';

/**
 * Family drop-in, per account and per device.
 *
 * Per account: who may drop in on a person. A ring from them is answered by
 * that person's devices by themselves; anyone else's call just rings. Adults
 * choose their own; a parent chooses for each child and teen.
 *
 * Per device: whether each of the person's phones and tablets answers an
 * allowed drop-in by itself. A parent can switch a child's devices.
 */
/** An answer without the per-person list is a failed load, not a page crash. */
async function loadDropInSettings(): Promise<DropInSettings> {
  const d = await api.getDropInSettings();
  if (!d || !Array.isArray(d.settings)) throw new Error('Unexpected drop-in settings answer');
  return d;
}

export default function FamilyDropInSettings() {
  const [data, setData] = useState<DropInSettings | null>(null);
  const [error, setError] = useState(false);
  const [thisDevice, setThisDevice] = useState('');
  // This phone's own Drop In setup (Android): null off Android or on an APK too old to say.
  const [overOtherApps, setOverOtherApps] = useState<boolean | null>(null);
  const checkPhone = useCallback(() => {
    void nativeDropInStatus().then((s) => setOverOtherApps(s ? s.overOtherApps : null));
  }, []);
  useEffect(() => {
    checkPhone();
    // Coming back from Android's settings page: read it again.
    const again = () => { if (!document.hidden) checkPhone(); };
    document.addEventListener('visibilitychange', again);
    return () => document.removeEventListener('visibilitychange', again);
  }, [checkPhone]);

  const load = useCallback(async () => {
    try {
      setData(await loadDropInSettings());
      setError(false);
    } catch {
      setError(true);
    }
  }, []);

  useEffect(() => {
    let active = true;
    loadDropInSettings()
      .then((d) => {
        if (active) setData(d);
      })
      .catch(() => {
        if (active) setError(true);
      });
    getDeviceKey()
      .then((key) => {
        if (active) setThisDevice(key);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, []);

  const saveList = async (setting: DropInSetting, allowFrom: string[]) => {
    const before = data;
    setData((d) => d && {
      ...d,
      settings: d.settings.map((s) => (s.username === setting.username ? { ...s, allow_from: allowFrom, configured: true } : s)),
    });
    try {
      await api.setDropInList(setting.username, allowFrom);
    } catch (err) {
      setData(before);
      toast.error(errorText(err, 'Could not save who can drop in.'));
    }
  };

  const saveDevice = async (setting: DropInSetting, deviceKey: string, on: boolean) => {
    const before = data;
    setData((d) => d && {
      ...d,
      settings: d.settings.map((s) => (s.username === setting.username
        ? { ...s, devices: s.devices.map((dev) => (dev.device_key === deviceKey ? { ...dev, drop_in: on } : dev)) }
        : s)),
    });
    try {
      await api.setDeviceDropIn(setting.username, deviceKey, on);
      if (deviceKey === thisDevice) await storeDeviceDropIn(on);
    } catch (err) {
      setData(before);
      toast.error(errorText(err, 'Could not save the device setting.'));
    }
  };

  const mine = data?.settings.find((s) => s.username === data.me);
  const kids = data?.settings.filter((s) => s.username !== data.me) ?? [];

  return (
    <div className="glass-panel rounded-2xl p-4 space-y-3" data-testid="family-drop-in">
      <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider px-3 pt-1 flex items-center gap-2">
        <PhoneIncoming size={16} className="text-emerald-400" />
        Family drop-in
      </h2>
      <p className="px-1 text-xs text-slate-400">
        A call from someone you let drop in is answered by your phone by itself after a short chime,
        microphone and speaker on, like a walkie-talkie. Anyone else&apos;s call rings, and you answer or decline.
      </p>

      {error && (
        <div className="p-3 rounded-xl bg-white/5 text-xs text-slate-300 flex items-center justify-between gap-2">
          <span>Could not load the drop-in settings.</span>
          <button type="button" onClick={() => void load()} className="text-emerald-300 font-semibold pointer-coarse:min-h-11">
            Try again
          </button>
        </div>
      )}
      {!data && !error && <p className="px-1 text-xs text-slate-500">Loading…</p>}

      {overOtherApps === false && (
        <div className="rounded-xl border border-amber-500/40 bg-amber-500/10 p-3 space-y-2" data-testid="drop-in-setup">
          <p className="flex items-start gap-2 text-sm font-semibold text-amber-100">
            <AlertTriangle size={16} className="mt-0.5 shrink-0" aria-hidden />
            Finish setting up Drop In on this phone
          </p>
          <p className="text-xs text-amber-100/90">
            Android only lets Jarvis open a Drop In by itself while you are using another app when Jarvis may
            &ldquo;Display over other apps&rdquo;. Until then, a Drop In that arrives while you use the phone rings instead.
            Jarvis never draws anything over your apps with it.
          </p>
          <button type="button" onClick={() => void openOverlaySettings().then((ok) => {
            if (!ok) toast.error('Open Settings > Apps > Jarvis OS > Display over other apps');
          })}
            className="min-h-11 rounded-xl bg-amber-500/25 border border-amber-500/50 px-4 text-sm font-semibold text-amber-50">
            Allow &ldquo;Display over other apps&rdquo;
          </button>
        </div>
      )}
      {overOtherApps === true && (
        <p className="flex items-center gap-2 px-1 text-xs text-emerald-300" data-testid="drop-in-ready">
          <CheckCircle2 size={14} aria-hidden />This phone is set up to answer a Drop In by itself, even while you use another app.
        </p>
      )}

      {data && mine && (
        <PersonDropIn
          title="Who can drop in on you"
          setting={mine}
          people={data.people}
          thisDevice={thisDevice}
          note={mine.can_edit ? undefined : 'A parent chooses who can drop in on you.'}
          onList={(allow) => void saveList(mine, allow)}
          onDevice={(key, on) => void saveDevice(mine, key, on)}
        />
      )}
      {data && kids.map((kid) => (
        <PersonDropIn
          key={kid.username}
          title={`Who can drop in on ${kid.name}`}
          setting={kid}
          people={data.people}
          thisDevice={thisDevice}
          onList={(allow) => void saveList(kid, allow)}
          onDevice={(key, on) => void saveDevice(kid, key, on)}
        />
      ))}
      <p className="px-1 text-[11px] leading-relaxed text-slate-500">
        The first time, the phone may ask for microphone permission. In the background this needs
        the Jarvis OS Android app; elsewhere a drop-in shows a Join banner instead.
      </p>
    </div>
  );
}

function PersonDropIn({
  title,
  setting,
  people,
  thisDevice,
  note,
  onList,
  onDevice,
}: {
  title: string;
  setting: DropInSetting;
  people: DropInPerson[];
  thisDevice: string;
  note?: string;
  onList: (allow: string[]) => void;
  onDevice: (deviceKey: string, on: boolean) => void;
}) {
  const others = people.filter((p) => p.username !== setting.username);
  const allowed = new Set(setting.allow_from);
  const toggle = (username: string, on: boolean) => {
    const next = new Set(allowed);
    if (on) next.add(username);
    else next.delete(username);
    onList(others.map((p) => p.username).filter((u) => next.has(u)));
  };

  return (
    <div className="rounded-xl bg-white/5 p-3 space-y-2" data-testid={`drop-in-${setting.username}`}>
      <p className="text-white text-sm font-medium">{title}</p>
      {note && <p className="text-xs text-amber-200/80">{note}</p>}
      <div className="flex flex-wrap gap-2">
        {others.map((person) => {
          const on = allowed.has(person.username);
          return (
            <button
              key={person.username}
              type="button"
              disabled={!setting.can_edit}
              aria-pressed={on}
              onClick={() => toggle(person.username, !on)}
              className={`px-3 py-1.5 rounded-full text-xs font-semibold border pointer-coarse:min-h-11 disabled:opacity-60 ${
                on
                  ? 'bg-emerald-500/25 text-emerald-100 border-emerald-500/50'
                  : 'bg-white/5 text-slate-300 border-white/10'
              }`}
            >
              {person.name}
            </button>
          );
        })}
      </div>
      {allowed.size === 0 && <p className="text-xs text-slate-500">Nobody: every call rings.</p>}

      {setting.devices.length > 0 && (
        <div className="pt-1 space-y-1">
          <p className="text-[11px] uppercase tracking-wider text-slate-500">Devices that answer by themselves</p>
          {setting.devices.map((device) => (
            <div key={device.device_key} className="flex items-center justify-between gap-3 py-1">
              <span className="min-w-0 truncate text-sm text-slate-200">
                {device.label}
                {device.device_key === thisDevice && <span className="text-slate-500"> (this device)</span>}
              </span>
              <Toggle
                checked={device.drop_in}
                disabled={!setting.can_edit}
                onChange={(next) => onDevice(device.device_key, next)}
                ariaLabel={`${device.label} answers drop-ins`}
                testId={`drop-in-device-${device.device_key}`}
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function errorText(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof detail === 'string' && detail ? detail : fallback;
}
