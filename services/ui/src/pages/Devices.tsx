import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { isAxiosError } from 'axios';
import toast from 'react-hot-toast';
import {
  BookOpen, Check, GraduationCap, Loader2, Lock, RefreshCw, ShieldAlert, Smartphone, Trash2, Unlock, Upload, X,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { api } from '../services/api';
import { useAuth } from '../context/AuthContext';
import { relativeTime } from '../lib/wanderTrips';
import { deviceKindLabel } from '../lib/deviceKind';
import type { MdmDevice, MdmFolder, MdmMember, MemberGuardState } from '../types/api';

/**
 * Device management (MDM, HANDOVER.md Phase 12).
 *
 * One page for every family device: its owner, kind, model, OS, app version and
 * build, last seen, guard state and capabilities, with the MDM actions a parent
 * has. Two of the actions are the existing mechanisms, not new ones: **lock** is
 * the screen-time pause and **app control** is the screen rules
 * (`install_approval` / `blocked_apps`). **Wipe** is the destructive one and is
 * confirmation-gated; the server only queues it for a device-owner phone.
 *
 * The shared School and Library folders are pushed to selected children; a child
 * opening this page sees only the folders pushed to them.
 */

const PAGE = 'mx-auto w-full min-w-0 max-w-6xl space-y-3 px-1 pb-28 sm:space-y-4 sm:px-0';
const TOUCH = 'min-h-11 pointer-coarse:min-h-11';

const GUARD_ROWS: Array<{ key: keyof MemberGuardState; label: string }> = [
  { key: 'adminActive', label: 'Device admin' },
  { key: 'deviceOwner', label: 'Device owner' },
  { key: 'guardEnabled', label: 'Screen guard' },
  { key: 'webFilterActive', label: 'Web filter' },
  { key: 'usageAccess', label: 'Usage access' },
];

function State({ on }: { on: boolean | undefined }) {
  if (on === undefined) return <span className="text-slate-500">not reported</span>;
  return on
    ? <span className="inline-flex items-center gap-1 text-emerald-300"><Check size={12} /> on</span>
    : <span className="inline-flex items-center gap-1 text-amber-300"><X size={12} /> off</span>;
}

function Panel({ title, icon: Icon, children, aside }: { title: string; icon: LucideIcon; children: React.ReactNode; aside?: React.ReactNode }) {
  return (
    <section className="glass-panel overflow-hidden rounded-2xl border border-white/10">
      <header className="flex items-center justify-between gap-3 border-b border-white/5 bg-sky-500/5 px-4 py-3">
        <span className="flex items-center gap-2 text-[10px] font-black uppercase tracking-widest text-sky-400">
          <Icon size={14} /> {title}
        </span>
        {aside}
      </header>
      {children}
    </section>
  );
}

/** App install/uninstall control: the existing screen rules, exposed per device. */
function AppControls({ owner }: { owner: string }) {
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ['screen-status', owner],
    queryFn: () => api.getScreenTimeStatus(owner),
    retry: false,
  });
  const [blocked, setBlocked] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (rules: { install_approval?: boolean; blocked_apps?: string[] }) => api.setScreenRules(owner, rules),
    onSuccess: () => {
      toast.success('App control saved');
      queryClient.invalidateQueries({ queryKey: ['screen-status', owner] });
    },
    onError: (e: Error) => toast.error(e.message || 'Could not save app control'),
  });
  if (isLoading) return <p className="text-xs text-slate-500">Loading app control…</p>;
  if (error || !data) return <p className="text-xs text-amber-300/90">App control could not be read for @{owner}.</p>;
  const installApproval = data.install_approval ?? false;
  const currentBlocked = blocked ?? (data.blocked_apps ?? []).join(', ');
  return (
    <div className="space-y-2 border-t border-white/5 pt-2" data-testid={`mdm-app-controls-${owner}`}>
      <label className="flex items-center justify-between gap-2 text-xs text-slate-200">
        <span>Ask a parent before installing new apps</span>
        <input
          type="checkbox"
          checked={installApproval}
          disabled={save.isPending}
          onChange={(e) => save.mutate({ install_approval: e.target.checked })}
          data-testid={`mdm-install-approval-${owner}`}
        />
      </label>
      <label className="block text-xs text-slate-300">
        Blocked app packages (comma separated)
        <input
          className="glass-input mt-1 w-full bg-black/30 text-sm"
          value={currentBlocked}
          placeholder="com.example.game, com.example.chat"
          onChange={(e) => setBlocked(e.target.value)}
          data-testid={`mdm-blocked-apps-${owner}`}
        />
      </label>
      <button
        type="button"
        disabled={save.isPending || blocked === null}
        onClick={() => save.mutate({ blocked_apps: currentBlocked.split(',').map((p) => p.trim()).filter(Boolean) })}
        className={`glass-button px-4 text-xs ${TOUCH}`}
        data-testid={`mdm-blocked-save-${owner}`}
      >
        {save.isPending ? <Loader2 size={14} className="animate-spin" /> : null} Save blocked apps
      </button>
    </div>
  );
}

function DeviceCard({ device, onRefresh }: { device: MdmDevice; onRefresh: () => void }) {
  const [showApps, setShowApps] = useState(false);
  const guard = device.capabilities?.guard;
  const facts: Array<[string, string]> = [
    ['Owner', device.owner_display_name || device.owner_username || 'unknown'],
    ['Model', [device.manufacturer, device.model].filter(Boolean).join(' ') || 'unknown'],
    ['Android', [device.os_version, device.os_build].filter(Boolean).join(' · ') || 'unknown'],
    ['Jarvis app', device.app_version ? `${device.app_version}${device.app_build ? ` (build ${device.app_build})` : ''}` : 'unknown'],
    ['Last seen', device.last_seen_at ? relativeTime(device.last_seen_at) : 'never'],
  ];

  const update = useMutation({
    mutationFn: () => api.pushMdmAppUpdate({ device_keys: [device.device_key] }),
    onSuccess: (r) => toast.success(`Update notice sent to ${r.owners.join(', ')}`),
    onError: (e: Error) => toast.error(e.message || 'Could not push the update'),
  });
  const lock = useMutation({
    mutationFn: (resume: boolean) => api.pauseScreenTime(device.owner_username, resume ? { resume: true } : {}),
    onSuccess: (_r, resume) => toast.success(resume ? 'Unlocked' : 'Locked'),
    onError: (e: Error) => toast.error(e.message || 'Could not change the lock'),
  });
  const wipe = useMutation({
    mutationFn: () => api.wipeMdmDevice(device.device_key),
    onSuccess: (r) => {
      toast.success(`Wipe queued. ${r.delivery}`);
      onRefresh();
    },
    onError: (e: Error) => toast.error(e.message || 'Could not queue the wipe'),
  });

  return (
    <li className="space-y-2 px-4 py-3 sm:px-6" data-testid={`mdm-device-${device.device_key}`}>
      <div className="flex flex-wrap items-center gap-2">
        <Smartphone size={18} className="shrink-0 text-sky-400" />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm text-white">{device.label || device.model || deviceKindLabel(device.kind)}</span>
          <span className="block text-xs text-slate-500">{deviceKindLabel(device.kind)} · {device.device_key}</span>
        </span>
      </div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        {facts.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-slate-500">{k}</dt>
            <dd className="min-w-0 break-words text-slate-200">{v}</dd>
          </div>
        ))}
      </dl>
      <div className="rounded-xl border border-white/5 bg-black/20 p-2 text-xs">
        <p className="text-[10px] uppercase tracking-wider text-slate-500">
          Protection {guard?.reportedAt ? `· reported ${relativeTime(guard.reportedAt)}` : ''}
        </p>
        {guard ? (
          <ul className="mt-1 grid grid-cols-2 gap-x-3 gap-y-1" data-testid={`mdm-guard-${device.device_key}`}>
            {GUARD_ROWS.map((row) => (
              <li key={row.key} className="flex justify-between gap-2">
                <span className="text-slate-300">{row.label}</span>
                <State on={guard[row.key] as boolean | undefined} />
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-1 text-slate-500">This device has not reported its protection yet.</p>
        )}
      </div>
      <div className="flex flex-wrap gap-2">
        <button type="button" disabled={update.isPending} onClick={() => update.mutate()}
          className={`glass-button px-3 text-xs ${TOUCH}`} data-testid={`mdm-update-${device.device_key}`}>
          {update.isPending ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />} Push update
        </button>
        <button type="button" disabled={lock.isPending} onClick={() => lock.mutate(false)}
          className={`glass-button px-3 text-xs ${TOUCH}`} data-testid={`mdm-lock-${device.device_key}`}>
          <Lock size={14} /> Lock
        </button>
        <button type="button" disabled={lock.isPending} onClick={() => lock.mutate(true)}
          className={`glass-button px-3 text-xs ${TOUCH}`} data-testid={`mdm-unlock-${device.device_key}`}>
          <Unlock size={14} /> Unlock
        </button>
        <button type="button" onClick={() => setShowApps((v) => !v)} aria-expanded={showApps}
          className={`glass-button px-3 text-xs ${TOUCH}`} data-testid={`mdm-apps-toggle-${device.device_key}`}>
          App control
        </button>
        <button
          type="button"
          disabled={wipe.isPending}
          onClick={() => {
            if (window.confirm(`Erase EVERYTHING on ${device.label || device.device_key}? This cannot be undone.`)) wipe.mutate();
          }}
          className={`glass-button border-red-500/30 bg-red-600/20 px-3 text-xs text-red-200 ${TOUCH}`}
          data-testid={`mdm-wipe-${device.device_key}`}
        >
          <ShieldAlert size={14} /> Wipe…
        </button>
      </div>
      {showApps && <AppControls owner={device.owner_username} />}
    </li>
  );
}

function FolderCard({ folder, members }: { folder: MdmFolder; members: MdmMember[] }) {
  const queryClient = useQueryClient();
  const label = folder.folder === 'school' ? 'School' : 'Library';
  const Icon = folder.folder === 'school' ? GraduationCap : BookOpen;
  const [selected, setSelected] = useState<string[]>(folder.pushed_to);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['mdm-files'] });

  const upload = useMutation({
    mutationFn: (file: File) => api.uploadMdmFile(folder.folder, file),
    onSuccess: (r) => {
      toast.success(`Uploaded ${r.file.name}`);
      refresh();
    },
    onError: (e: Error) => toast.error(e.message || 'Upload failed'),
  });
  const remove = useMutation({
    mutationFn: (name: string) => api.deleteMdmFile(folder.folder, name),
    onSuccess: refresh,
    onError: (e: Error) => toast.error(e.message || 'Could not remove the file'),
  });
  const push = useMutation({
    mutationFn: () => api.pushMdmFiles(folder.folder, selected),
    onSuccess: (r) => {
      toast.success(`Pushed to ${r.pushed_to.length} device owner(s)`);
      refresh();
    },
    onError: (e: Error) => toast.error(e.message || 'Could not push the folder'),
  });

  return (
    <Panel title={`${label} folder`} icon={Icon} aside={<span className="text-xs text-slate-500">{folder.files.length} file(s)</span>}>
      <div className="space-y-3 p-4" data-testid={`mdm-folder-${folder.folder}`}>
        <ul className="space-y-1 text-xs">
          {folder.files.length === 0 && <li className="text-slate-500">No files in this folder yet.</li>}
          {folder.files.map((f) => (
            <li key={f.name} className="flex items-center justify-between gap-2">
              <a className="min-w-0 truncate text-slate-200 hover:text-white"
                href={`/api/mdm/files/${folder.folder}/${encodeURIComponent(f.name)}`}>{f.name}</a>
              <button type="button" onClick={() => remove.mutate(f.name)} aria-label={`Remove ${f.name}`}
                className={`shrink-0 rounded-lg p-2 text-slate-400 hover:text-red-300 ${TOUCH}`}>
                <Trash2 size={14} />
              </button>
            </li>
          ))}
        </ul>
        <label className={`glass-button inline-flex w-fit cursor-pointer items-center gap-2 px-3 text-xs ${TOUCH}`}>
          {upload.isPending ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />} Upload file
          <input
            type="file"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) upload.mutate(file);
              e.target.value = '';
            }}
          />
        </label>
        <div className="space-y-1 border-t border-white/5 pt-2">
          <p className="text-[10px] uppercase tracking-wider text-slate-500">Push to</p>
          <div className="flex flex-wrap gap-1.5">
            {members.length === 0 && <span className="text-xs text-slate-500">No family members found.</span>}
            {members.map((m) => {
              const on = selected.includes(m.username);
              return (
                <button
                  key={m.username}
                  type="button"
                  aria-pressed={on}
                  onClick={() => setSelected(on ? selected.filter((u) => u !== m.username) : [...selected, m.username])}
                  className={`rounded-full border px-3 py-1 text-xs ${on ? 'border-sky-500/40 bg-sky-500/20 text-sky-200' : 'border-white/10 bg-white/5 text-slate-300'} ${TOUCH}`}
                >
                  {m.display_name}
                </button>
              );
            })}
          </div>
          <button type="button" disabled={push.isPending || selected.length === 0} onClick={() => push.mutate()}
            className={`glass-button px-4 text-xs ${TOUCH}`} data-testid={`mdm-push-${folder.folder}`}>
            {push.isPending ? <Loader2 size={14} className="animate-spin" /> : null} Push {label}
          </button>
        </div>
      </div>
    </Panel>
  );
}

/** A child's own view: only the folders pushed to them. */
function SharedFilesForMe() {
  const { data, isLoading, error } = useQuery({ queryKey: ['mdm-files-for-me'], queryFn: () => api.getMdmFilesForMe(), retry: false });
  return (
    <Panel title="Shared files" icon={BookOpen}>
      <div className="space-y-3 p-4" data-testid="mdm-for-me">
        {isLoading && <p className="text-xs text-slate-500">Loading…</p>}
        {error && <p className="text-xs text-amber-300/90">Shared files could not be loaded.</p>}
        {data && data.folders.length === 0 && <p className="text-xs text-slate-500">No shared folders have been pushed to you yet.</p>}
        {data?.folders.map((f) => (
          <div key={f.folder}>
            <p className="text-xs font-semibold capitalize text-slate-200">{f.folder}</p>
            <ul className="mt-1 space-y-1 text-xs">
              {f.files.length === 0 && <li className="text-slate-500">Empty.</li>}
              {f.files.map((file) => (
                <li key={file.name}>
                  <a className="text-slate-200 hover:text-white" href={`/api/mdm/files/${f.folder}/${encodeURIComponent(file.name)}`}>{file.name}</a>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </Panel>
  );
}

export default function Devices() {
  const user = useAuth().user;
  const isAdmin = Boolean(user?.is_admin);
  const queryClient = useQueryClient();
  const inventory = useQuery({ queryKey: ['mdm-devices'], queryFn: () => api.getMdmDevices(), enabled: isAdmin, retry: false });
  const files = useQuery({ queryKey: ['mdm-files'], queryFn: () => api.getMdmFiles(), enabled: isAdmin, retry: false });

  if (!isAdmin) {
    return (
      <div className={PAGE}>
        <header className="glass-panel rounded-2xl border border-white/10 p-4 sm:p-5">
          <h1 className="text-xl font-bold text-white sm:text-2xl">Devices</h1>
          <p className="mt-0.5 text-xs text-slate-400">Files shared with you, and your own device.</p>
        </header>
        <SharedFilesForMe />
      </div>
    );
  }

  const refused = isAxiosError(inventory.error) && inventory.error.response?.status === 403;
  const members = inventory.data?.members ?? [];

  return (
    <div className={PAGE}>
      <header className="glass-panel rounded-2xl border border-white/10 p-4 sm:p-5">
        <h1 className="text-xl font-bold text-white sm:text-2xl">Device management</h1>
        <p className="mt-0.5 text-xs text-slate-400">
          Every family device, the shared School and Library folders, and remote actions.
        </p>
      </header>

      <Panel title="Family devices" icon={Smartphone}
        aside={<span className="text-xs text-slate-500">{inventory.data?.devices.length ?? 0} device(s)</span>}>
        {inventory.isLoading && <p className="p-4 text-sm text-slate-500">Loading devices…</p>}
        {inventory.error && (
          <p className="p-4 text-sm text-amber-300/90" data-testid="mdm-error">
            {refused ? 'Device management is for the father and admins.' : 'Devices could not be loaded.'}
          </p>
        )}
        {inventory.data && inventory.data.devices.length === 0 && (
          <p className="p-4 text-sm text-slate-500" data-testid="mdm-empty">
            No devices have registered yet. A phone or tablet registers when a member signs in to the Jarvis app.
          </p>
        )}
        <ul className="divide-y divide-white/5">
          {inventory.data?.devices.map((d) => (
            <DeviceCard key={d.device_key} device={d} onRefresh={() => queryClient.invalidateQueries({ queryKey: ['mdm-devices'] })} />
          ))}
        </ul>
        {members.some((m) => !m.readable) && (
          <div className="border-t border-white/5 p-4" data-testid="mdm-unreadable">
            <p className="text-[10px] uppercase tracking-wider text-slate-500">Not readable</p>
            <ul className="mt-1 space-y-1 text-xs">
              {members.filter((m) => !m.readable).map((m) => (
                <li key={m.username} className="flex justify-between gap-2">
                  <span className="text-slate-300">{m.display_name}</span>
                  <span className="min-w-0 break-words text-right text-slate-500">{m.reason}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </Panel>

      {files.isLoading && <p className="text-sm text-slate-500">Loading shared folders…</p>}
      {files.data?.folders.map((f) => <FolderCard key={f.folder} folder={f} members={members.filter((m) => m.readable)} />)}
    </div>
  );
}
