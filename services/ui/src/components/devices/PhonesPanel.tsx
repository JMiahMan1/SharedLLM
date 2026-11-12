import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { AlarmClock, BatteryCharging, ChevronDown, ChevronRight, Loader2, Plus, Settings2, Smartphone, Trash2 } from 'lucide-react';
import { failureText } from '../../lib/discoveredDevices';
import {
  addJarvisPhone,
  getPhones,
  keySensors,
  lastSeenText,
  phoneStatus,
  removePhone,
  savePhoneSettings,
  sensorText,
  updatePhone,
} from '../../lib/phones';
import type { Phone, PhoneSensor, PhoneSettings } from '../../lib/phones';
import { confirmDialog } from '../ui/confirmDialog';

const QUERY_KEY = ['homehub-phones'];
const button = 'inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-white/10 px-3 text-xs text-slate-200 hover:bg-white/5 disabled:opacity-50';
const input = 'min-h-11 w-full rounded-lg border border-white/10 bg-black/20 px-3 text-sm text-white placeholder:text-slate-500';

function SourceChip({ source }: { source: string }) {
  const jarvis = source === 'jarvis';
  return (
    <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${jarvis ? 'bg-violet-500/15 text-violet-300' : 'bg-emerald-500/15 text-emerald-300'}`}>
      {jarvis ? 'Jarvis' : 'App'}
    </span>
  );
}

function SensorLine({ s }: { s: PhoneSensor }) {
  const value = sensorText(s);
  return (
    <li className="flex items-center justify-between gap-3 text-xs" data-testid="phone-sensor">
      <span className="min-w-0 truncate text-slate-300">{s.name}</span>
      <span className="flex shrink-0 items-center gap-2 text-slate-200" title={s.reason || undefined}>
        {value} <SourceChip source={s.source} />
      </span>
    </li>
  );
}

function PhoneRow({ phone, onChanged }: { phone: Phone; onChanged: () => void }) {
  const [open, setOpen] = useState(false);
  const [user, setUser] = useState(phone.jarvis_user || '');
  useEffect(() => setUser(phone.jarvis_user || ''), [phone.jarvis_user]);
  const update = useMutation({
    mutationFn: (patch: { jarvis_user?: string | null; jarvis_fallback?: boolean }) => updatePhone(phone.entry_id, patch),
    onSuccess: () => onChanged(),
    onError: (err) => toast.error(failureText(err)),
  });
  const remove = useMutation({
    mutationFn: () => removePhone(phone.entry_id),
    onSuccess: () => {
      toast.success(`${phone.name} was removed.`);
      onChanged();
    },
    onError: (err) => toast.error(failureText(err)),
  });
  const shown = keySensors(phone);
  const companion = phone.kind === 'companion';
  const status = phoneStatus(phone);

  return (
    <li className="space-y-2 px-4 py-3" data-testid="phone">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-white">{phone.name}</p>
          <p className="text-xs text-slate-400">
            {companion
              ? `Companion app ${phone.app.app_version || ''}${phone.app.model ? ` · ${phone.app.model}` : ''} · ${lastSeenText(phone.companion_last_seen)}`
              : 'Jarvis only (no companion app)'}
            {companion && (phone.push.local_channel || phone.push.relay) ? ' · push ready' : ''}
          </p>
          {(status.power || status.alarm) && (
            <p className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-slate-300">
              {status.power && (
                <span className="inline-flex items-center gap-1" data-testid="phone-power"
                  title={phone.sensors.find((x) => x.entity_id.endsWith('_battery_state'))?.reason || undefined}>
                  <BatteryCharging size={12} className="text-emerald-400" aria-hidden /> {status.power}
                </span>
              )}
              {status.alarm && (
                <span className="inline-flex items-center gap-1" data-testid="phone-alarm"
                  title={phone.sensors.find((x) => x.entity_id.endsWith('_next_alarm'))?.reason || undefined}>
                  <AlarmClock size={12} className="text-sky-400" aria-hidden /> {status.alarm}
                </span>
              )}
            </p>
          )}
          <p className="text-[11px] text-slate-500">{phone.notify_service}</p>
        </div>
        <button
          type="button"
          className={button}
          onClick={async () => {
            if (await confirmDialog({
              title: `Remove ${phone.name}?`,
              body: companion
                ? 'Its sensors and tracker go away. If the app is still installed, it will register again as a new phone.'
                : 'Its tracker and sensors go away.',
              confirmLabel: 'Remove',
              danger: true,
            })) remove.mutate();
          }}
          aria-label={`Remove ${phone.name}`}
        >
          <Trash2 size={14} /> Remove
        </button>
      </div>

      {shown.length > 0 && <ul className="space-y-1">{shown.map((s) => <SensorLine key={s.entity_id} s={s} />)}</ul>}

      <div className="flex flex-wrap items-end gap-3">
        <label className="min-w-[10rem] flex-1 text-xs text-slate-400">
          Jarvis user
          <input
            className={input}
            value={user}
            placeholder="username"
            onChange={(e) => setUser(e.target.value)}
            onBlur={() => {
              if (user.trim() !== (phone.jarvis_user || '')) update.mutate({ jarvis_user: user.trim() || null });
            }}
            aria-label={`Jarvis user for ${phone.name}`}
          />
        </label>
        {companion && (
          <label className="flex min-h-11 items-center gap-2 text-xs text-slate-300">
            <input
              type="checkbox"
              checked={phone.jarvis_fallback}
              onChange={(e) => update.mutate({ jarvis_fallback: e.target.checked })}
              aria-label={`Let Jarvis fill in for ${phone.name}`}
            />
            Jarvis fills in when the app is quiet
          </label>
        )}
        {update.isPending && <Loader2 size={14} className="animate-spin text-slate-400" />}
      </div>

      {phone.sensors.length > shown.length && (
        <div>
          <button type="button" className="inline-flex items-center gap-1 text-xs text-slate-400" onClick={() => setOpen((v) => !v)}>
            {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />} All sensors ({phone.sensors.length})
          </button>
          {open && <ul className="mt-2 space-y-1">{phone.sensors.map((s) => <SensorLine key={s.entity_id} s={s} />)}</ul>}
        </div>
      )}
    </li>
  );
}

function SettingsForm({ settings, onSaved }: { settings: PhoneSettings; onSaved: () => void }) {
  const [draft, setDraft] = useState(settings);
  const [relays, setRelays] = useState(settings.push_url_allowlist.join('\n'));
  useEffect(() => {
    setDraft(settings);
    setRelays(settings.push_url_allowlist.join('\n'));
  }, [settings]);
  const save = useMutation({
    mutationFn: () => savePhoneSettings({
      ...draft,
      push_url_allowlist: relays.split('\n').map((l) => l.trim()).filter(Boolean),
    }),
    onSuccess: () => {
      toast.success('Phone settings saved.');
      onSaved();
    },
    onError: (err) => toast.error(failureText(err)),
  });
  const set = <K extends keyof PhoneSettings>(key: K, value: PhoneSettings[K]) => setDraft((d) => ({ ...d, [key]: value }));

  return (
    <form
      className="space-y-3 border-t border-white/5 px-4 py-3"
      data-testid="phone-settings"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <label className="block text-xs text-slate-400">
        Address the app uses from outside (the reverse proxy serves this host)
        <input className={input} value={draft.external_url} placeholder="https://home.example.com"
          onChange={(e) => set('external_url', e.target.value)} aria-label="External URL" />
      </label>
      <label className="block text-xs text-slate-400">
        Address the app uses at home
        <input className={input} value={draft.internal_url} placeholder="http://hub.local:8123"
          onChange={(e) => set('internal_url', e.target.value)} aria-label="Internal URL" />
      </label>
      <label className="block text-xs text-slate-400">
        Minutes a companion app report counts as fresh
        <input className={input} type="number" min={1} value={draft.companion_fresh_minutes}
          onChange={(e) => set('companion_fresh_minutes', Number(e.target.value) || 1)} aria-label="Fresh minutes" />
      </label>
      <label className="flex min-h-11 items-center gap-2 text-xs text-slate-300">
        <input type="checkbox" checked={draft.jarvis_fallback} onChange={(e) => set('jarvis_fallback', e.target.checked)} />
        Jarvis fills in sensors when a phone's app is quiet
      </label>
      <label className="flex min-h-11 items-center gap-2 text-xs text-slate-300">
        <input type="checkbox" checked={draft.auto_jarvis_phones} onChange={(e) => set('auto_jarvis_phones', e.target.checked)} />
        Give every Jarvis user without a phone here a Jarvis phone
      </label>
      <label className="block text-xs text-slate-400">
        Name for a Jarvis phone ({'{name}'} is the person)
        <input className={input} value={draft.jarvis_phone_name}
          onChange={(e) => set('jarvis_phone_name', e.target.value)} aria-label="Jarvis phone name" />
      </label>
      <label className="block text-xs text-slate-400">
        Push relays a phone may use (one https:// prefix per line)
        <textarea className={`${input} min-h-20 py-2`} value={relays} onChange={(e) => setRelays(e.target.value)}
          aria-label="Push relays" />
      </label>
      <button type="submit" className={button} disabled={save.isPending}>
        {save.isPending ? <Loader2 size={14} className="animate-spin" /> : null} Save settings
      </button>
    </form>
  );
}

/**
 * Devices > Phones (admins): every phone the device hub knows, with its key
 * sensors and where each value comes from (the HA companion app or Jarvis),
 * the Jarvis user it belongs to, a Jarvis-only phone for someone without the
 * companion app, and the companion app settings.
 */
export default function PhonesPanel() {
  const queryClient = useQueryClient();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [newUser, setNewUser] = useState('');
  const list = useQuery({ queryKey: QUERY_KEY, queryFn: getPhones, retry: false, refetchInterval: 60_000 });
  const refresh = () => queryClient.invalidateQueries({ queryKey: QUERY_KEY });
  const add = useMutation({
    mutationFn: () => addJarvisPhone(newUser.trim()),
    onSuccess: (r) => {
      toast.success(`${r.name} was added.`);
      setNewUser('');
      void refresh();
    },
    onError: (err) => toast.error(failureText(err)),
  });
  const phones = list.data?.phones || [];

  return (
    <section className="glass-panel overflow-hidden rounded-2xl border border-white/10" data-testid="phones">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-white/5 bg-emerald-500/5 px-4 py-3">
        <span className="flex min-w-0 items-center gap-2 text-[10px] font-black uppercase tracking-widest text-emerald-400">
          <Smartphone size={14} /> Phones
        </span>
        <button type="button" className={button} onClick={() => setSettingsOpen((v) => !v)} aria-expanded={settingsOpen}>
          <Settings2 size={14} /> Settings
        </button>
      </header>

      {list.isLoading && <p className="p-4 text-sm text-slate-500">Loading phones…</p>}
      {list.error && <p className="p-4 text-sm text-amber-300/90" data-testid="phones-error">{failureText(list.error)}</p>}
      {list.data && phones.length === 0 && (
        <p className="p-4 text-sm text-slate-500">No phones yet. A phone appears here when its companion app registers.</p>
      )}

      <ul className="divide-y divide-white/5">
        {phones.map((p) => <PhoneRow key={p.entry_id} phone={p} onChanged={() => void refresh()} />)}
      </ul>

      <form
        className="flex flex-wrap items-end gap-2 border-t border-white/5 px-4 py-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (newUser.trim()) add.mutate();
        }}
      >
        <label className="min-w-[10rem] flex-1 text-xs text-slate-400">
          Add a phone fed by Jarvis (for someone without the companion app)
          <input className={input} value={newUser} placeholder="Jarvis username" onChange={(e) => setNewUser(e.target.value)}
            aria-label="Jarvis username for a new phone" />
        </label>
        <button type="submit" className={button} disabled={add.isPending || !newUser.trim()}>
          {add.isPending ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />} Add phone
        </button>
      </form>

      {settingsOpen && list.data && <SettingsForm settings={list.data.settings} onSaved={() => void refresh()} />}
    </section>
  );
}
