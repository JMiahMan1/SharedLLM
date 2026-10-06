import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { Lightbulb, Loader2, Mic, Plus, Search, Smartphone, Watch, X, Cpu } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { api } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import type { CompanionDevice, DiscoveredDevice } from '../../services/api';

/**
 * The signed-in user's companion devices, and adding new ones.
 *
 * Phones appear on their own (the app registers itself on login). Watches and
 * assistants are added here, as the user, and are linked to whoever adds them:
 *  - a device with a screen shows a 6-digit code, which proves you have it;
 *  - a device without one (a voice assistant, a relay) is simply adopted.
 * The server decides which by asking the device what it can do.
 */

const KIND_ICON: Record<string, LucideIcon> = {
  phone: Smartphone,
  watch: Watch,
  assistant: Mic,
  light: Lightbulb,
};

const HOW: Record<string, string> = {
  self: 'Signed in on this device',
  paired: 'Paired with its code',
  adopted: 'Linked (no screen to pair)',
  admin: 'Added by an admin',
};

type Step =
  | { at: 'closed' }
  | { at: 'find' }
  | { at: 'code' | 'adopt'; host: string; port: number; name: string }
  | { at: 'done'; name: string };

const touch = 'min-h-11 pointer-coarse:min-h-11';

function lastSeen(iso?: string | null): string {
  if (!iso) return '';
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return '';
  const mins = Math.round((Date.now() - t) / 60000);
  if (mins < 2) return 'active now';
  if (mins < 60) return `seen ${mins} min ago`;
  const hrs = Math.round(mins / 60);
  return hrs < 48 ? `seen ${hrs} h ago` : `seen ${Math.round(hrs / 24)} d ago`;
}

interface Props {
  /** mine: the signed-in user's devices (Identity page). all: everyone's, with
   * an owner picker on each (Admin -> Users & Devices; the server only returns
   * all to admins, and only an admin may reassign). */
  scope?: 'mine' | 'all';
}

const CompanionDevicesPanel: React.FC<Props> = ({ scope = 'mine' }) => {
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const me = (user?.username || '').toLowerCase();
  const [step, setStep] = useState<Step>({ at: 'closed' });
  const [address, setAddress] = useState('');
  const [code, setCode] = useState('');
  const [found, setFound] = useState<DiscoveredDevice[] | null>(null);

  const { data: allDevices = [], isLoading } = useQuery({
    queryKey: ['companion-devices'],
    queryFn: () => api.getCompanionDevices(),
  });
  const devices = scope === 'mine' ? allDevices.filter((d) => (d.owner_username || '').toLowerCase() === me) : allDevices;
  const { data: users = [] } = useQuery({
    queryKey: ['users'],
    queryFn: () => api.getUsers(),
    enabled: scope === 'all',
  });

  const assign = useMutation({
    mutationFn: (v: { deviceKey: string; owner: string }) => api.assignCompanionDevice(v.deviceKey, v.owner),
    onSuccess: (d) => {
      toast.success(d.owner_username ? `Assigned to @${d.owner_username}` : 'Unassigned');
      queryClient.invalidateQueries({ queryKey: ['companion-devices'] });
    },
    onError: (e: Error) => toast.error(e.message || 'Could not assign the device'),
  });

  const discover = useMutation({
    mutationFn: () => api.pairDevice({ step: 'discover' }),
    onSuccess: (r) => {
      const list = ((r.detail as { devices?: DiscoveredDevice[] } | undefined)?.devices ?? []);
      setFound(list);
    },
    onError: () => setFound([]),
  });

  const start = useMutation({
    mutationFn: (target: { host: string; port: number }) => api.pairDevice({ step: 'start', ...target }),
    onSuccess: (r, target) => {
      if (r.status !== 'SUCCESS') {
        toast.error(r.message || 'The device did not answer');
        return;
      }
      const d = r.detail as { method: 'code' | 'adopt'; friendly_name: string };
      setCode('');
      setStep({ at: d.method, host: target.host, port: target.port, name: d.friendly_name });
    },
    onError: (e: Error) => toast.error(e.message || 'The device did not answer'),
  });

  const finish = useMutation({
    mutationFn: (target: { host: string; port: number; code?: string }) => api.pairDevice({ step: 'finish', ...target }),
    onSuccess: (r) => {
      if (r.status !== 'SUCCESS') {
        toast.error(r.message || 'Pairing failed');
        return;
      }
      const d = r.detail as { friendly_name: string };
      setStep({ at: 'done', name: d.friendly_name });
      queryClient.invalidateQueries({ queryKey: ['companion-devices'] });
    },
    onError: (e: Error) => toast.error(e.message || 'Pairing failed'),
  });

  const open = () => {
    setStep({ at: 'find' });
    setFound(null);
    setAddress('');
    discover.mutate();
  };
  const close = () => setStep({ at: 'closed' });
  const busy = start.isPending || finish.isPending;

  return (
    <div className="glass-panel overflow-hidden border-sky-500/10">
      <div className="p-4 sm:p-6 border-b border-white/5 flex items-center justify-between gap-3 bg-sky-500/5">
        <span className="text-[10px] font-black uppercase tracking-widest text-sky-400">
          {scope === 'mine' ? 'Your devices' : 'Companion devices'}
        </span>
        {step.at === 'closed' && (
          <button onClick={open} className={`glass-button text-xs px-4 bg-sky-600/20 text-sky-300 border-sky-500/20 ${touch}`}>
            <Plus size={14} /> Add device
          </button>
        )}
      </div>

      {step.at !== 'closed' && (
        <div className="p-4 sm:p-6 border-b border-white/5 space-y-4" aria-label="Add a device">
          <div className="flex items-center justify-between">
            <h4 className="font-bold text-white">
              {step.at === 'find' && 'Add a device'}
              {step.at === 'code' && `Pair ${step.name}`}
              {step.at === 'adopt' && `Link ${step.name}`}
              {step.at === 'done' && 'Added'}
            </h4>
            <button onClick={close} aria-label="Close" className={`p-2 rounded-xl text-slate-400 hover:text-white ${touch}`}>
              <X size={18} />
            </button>
          </div>

          {step.at === 'find' && (
            <>
              <div className="space-y-2">
                {discover.isPending && (
                  <p className="flex items-center gap-2 text-sm text-slate-400">
                    <Loader2 size={16} className="animate-spin" /> Looking for devices on your network…
                  </p>
                )}
                {found && found.length === 0 && !discover.isPending && (
                  <p className="text-sm text-slate-400">No new devices found. Enter its address below.</p>
                )}
                {found?.map((d) => (
                  <button
                    key={d.host}
                    disabled={busy}
                    onClick={() => start.mutate({ host: d.host, port: d.port })}
                    className={`w-full flex items-center gap-3 rounded-xl bg-white/5 hover:bg-white/10 px-4 text-left ${touch}`}
                  >
                    <Cpu size={18} className="text-sky-400 shrink-0" />
                    <span className="flex-1 min-w-0">
                      <span className="block text-sm text-white truncate">{d.friendly_name}</span>
                      <span className="block text-xs text-slate-500 truncate">{d.host}</span>
                    </span>
                    {start.isPending && start.variables?.host === d.host && <Loader2 size={16} className="animate-spin" />}
                  </button>
                ))}
              </div>
              <form
                className="flex flex-col sm:flex-row gap-2"
                onSubmit={(e) => {
                  e.preventDefault();
                  const host = address.trim();
                  if (host) start.mutate({ host, port: 6053 });
                }}
              >
                <label className="sr-only" htmlFor="pair-address">Device address</label>
                <input
                  id="pair-address"
                  value={address}
                  onChange={(e) => setAddress(e.target.value)}
                  placeholder="Address, e.g. jarvis-watch.local or 192.168.2.105"
                  autoCapitalize="none"
                  autoCorrect="off"
                  spellCheck={false}
                  className={`flex-1 rounded-xl bg-black/30 border border-white/10 px-4 text-sm text-white ${touch}`}
                />
                <button type="submit" disabled={busy || !address.trim()} className={`glass-button px-5 text-sm ${touch}`}>
                  {start.isPending && !found?.some((d) => d.host === start.variables?.host) ? <Loader2 size={16} className="animate-spin" /> : <Search size={16} />} Find
                </button>
              </form>
            </>
          )}

          {step.at === 'code' && (
            <form
              className="space-y-3"
              onSubmit={(e) => {
                e.preventDefault();
                finish.mutate({ host: step.host, port: step.port, code });
              }}
            >
              <p className="text-sm text-slate-300">Enter the 6-digit code on {step.name}'s screen. It is shown for 2 minutes.</p>
              <label className="sr-only" htmlFor="pair-code">Pairing code</label>
              <input
                id="pair-code"
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                inputMode="numeric"
                autoComplete="one-time-code"
                autoFocus
                placeholder="000000"
                className="w-full sm:w-64 h-14 rounded-xl bg-black/30 border border-white/10 px-4 text-center text-2xl tracking-[0.4em] text-white font-mono"
              />
              <button type="submit" disabled={busy || code.length !== 6} className={`w-full sm:w-auto glass-button px-6 bg-sky-600/30 text-white ${touch}`}>
                {finish.isPending ? <Loader2 size={16} className="animate-spin" /> : null} Pair
              </button>
            </form>
          )}

          {step.at === 'adopt' && (
            <div className="space-y-3">
              <p className="text-sm text-slate-300">
                {step.name} has no screen to show a pairing code. Link it to your account as it is?
              </p>
              <button
                disabled={busy}
                onClick={() => finish.mutate({ host: step.host, port: step.port })}
                className={`w-full sm:w-auto glass-button px-6 bg-sky-600/30 text-white ${touch}`}
              >
                {finish.isPending ? <Loader2 size={16} className="animate-spin" /> : null} Link to me
              </button>
            </div>
          )}

          {step.at === 'done' && (
            <div className="space-y-3">
              <p className="text-sm text-emerald-300">{step.name} is now linked to you.</p>
              <button onClick={close} className={`w-full sm:w-auto glass-button px-6 ${touch}`}>Done</button>
            </div>
          )}
        </div>
      )}

      <ul className="divide-y divide-white/5">
        {isLoading && <li className="p-4 sm:p-6 text-sm text-slate-500">Loading…</li>}
        {!isLoading && devices.length === 0 && (
          <li className="p-4 sm:p-6 text-sm text-slate-500">No devices yet. Signing in on your phone adds it here.</li>
        )}
        {devices.map((d: CompanionDevice) => {
          const Icon = KIND_ICON[d.kind] ?? Cpu;
          const name = d.label || [d.manufacturer, d.model].filter(Boolean).join(' ') || d.device_key;
          const sub = [
            d.app_version ? `v${d.app_version}` : '',
            HOW[d.registered_by] ?? d.registered_by,
            lastSeen(d.last_seen_at),
          ].filter(Boolean).join(' · ');
          return (
            <li key={d.device_key} className={`flex flex-wrap items-center gap-3 px-4 sm:px-6 py-3 ${touch}`}>
              <Icon size={20} className="text-sky-400 shrink-0" />
              <span className="flex-1 min-w-0">
                <span className="block text-sm text-white truncate">{name}</span>
                <span className="block text-xs text-slate-500 truncate">{sub}</span>
              </span>
              <span className="text-[10px] uppercase tracking-widest text-slate-500">{d.kind}</span>
              {scope === 'all' && (
                <select
                  aria-label={`Owner of ${name}`}
                  value={(d.owner_username || '').toLowerCase()}
                  disabled={assign.isPending}
                  onChange={(e) => assign.mutate({ deviceKey: d.device_key, owner: e.target.value })}
                  className={`w-full sm:w-44 glass-input bg-black/30 text-sm ${touch}`}
                >
                  <option value="">Unassigned</option>
                  {users.map((u) => (
                    <option key={u.username} value={u.username.toLowerCase()}>@{u.username}</option>
                  ))}
                  {d.owner_username && !users.some((u) => u.username.toLowerCase() === d.owner_username!.toLowerCase()) && (
                    <option value={d.owner_username.toLowerCase()}>@{d.owner_username}</option>
                  )}
                </select>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
};

export default CompanionDevicesPanel;
