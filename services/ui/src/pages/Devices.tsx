import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { isAxiosError } from 'axios';
import toast from 'react-hot-toast';
import {
  Activity, Bell, BookOpen, Check, Clock, Cpu, GraduationCap, History, KeyRound, Lightbulb, Loader2, Lock, Mic, Monitor, Pause, Play, Radio, RefreshCw, RotateCw, ShieldAlert, Smartphone, Speaker, Square, Tablet, Trash2, Unlock, Upload, Volume2, VolumeX, Watch, X,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { api } from '../services/api';
import { useAuth } from '../context/AuthContext';
import { relativeTime } from '../lib/wanderTrips';
import {
  deviceKindLabel,
  deviceNoun,
  devicePlatformLabel,
  ESPHOME_KIND_OPTIONS,
  esphomeTargetName,
  isEsphomeDevice,
} from '../lib/deviceKind';
import SharedFilesPanel from '../components/mdm/SharedFilesPanel';
import { DeviceDetailSheet } from '../components/settings/DeviceDetailSheet';
import type { CompanionDevice, MdmDevice, MdmFolder, MdmMember, MemberGuardState } from '../types/api';
import { confirmDialog } from '../components/ui/confirmDialog';

const KIND_ICON: Record<string, LucideIcon> = {
  phone: Smartphone,
  tablet: Tablet,
  watch: Watch,
  speaker: Speaker,
  screen: Monitor,
  assistant: Mic,
  light: Lightbulb,
};

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

/** App install/uninstall and COSU kiosk control: the existing screen rules, exposed per device. */
function AppControls({ owner }: { owner: string }) {
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ['screen-status', owner],
    queryFn: () => api.getScreenTimeStatus(owner),
    retry: false,
  });
  const [blocked, setBlocked] = useState<string | null>(null);
  const [uninstall, setUninstall] = useState<string | null>(null);
  const [install, setInstall] = useState<string | null>(null);
  const [kioskApps, setKioskApps] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (rules: {
      install_approval?: boolean;
      blocked_apps?: string[];
      uninstall_apps?: string[];
      install_apps?: string[];
      kiosk_mode?: boolean;
      kiosk_apps?: string[];
    }) => api.setScreenRules(owner, { ...data?.rules, ...rules }),
    onSuccess: () => {
      toast.success('App control saved');
      queryClient.invalidateQueries({ queryKey: ['screen-status', owner] });
    },
    onError: (e: Error) => toast.error(e.message || 'Could not save app control'),
  });
  if (isLoading) return <p className="text-xs text-slate-500">Loading app control…</p>;
  if (error || !data) return <p className="text-xs text-amber-300/90">App control could not be read for @{owner}.</p>;
  const installApproval = data.install_approval ?? false;
  const kioskMode = data.kiosk_mode ?? data.rules?.kiosk_mode ?? false;
  const currentBlocked = blocked ?? (data.rules?.blocked_apps ?? data.blocked_apps ?? []).join(', ');
  const currentUninstall = uninstall ?? (data.uninstall_apps ?? data.rules?.uninstall_apps ?? []).join(', ');
  const currentInstall = install ?? (data.install_apps ?? data.rules?.install_apps ?? []).join(', ');
  const currentKioskApps = kioskApps ?? (data.kiosk_apps ?? data.rules?.kiosk_apps ?? []).join(', ');
  const parsePackages = (raw: string) => raw.split(',').map((p) => p.trim()).filter(Boolean);
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
      <label className="block text-xs text-slate-300">
        Remote uninstall packages (removed silently on device-owner phones, blocked everywhere)
        <input
          className="glass-input mt-1 w-full bg-black/30 text-sm"
          value={currentUninstall}
          placeholder="com.roblox.client, com.zhiliaoapp.musically"
          onChange={(e) => setUninstall(e.target.value)}
          data-testid={`mdm-uninstall-apps-${owner}`}
        />
      </label>
      <label className="block text-xs text-slate-300">
        Pre-approved / required install packages (comma separated)
        <input
          className="glass-input mt-1 w-full bg-black/30 text-sm"
          value={currentInstall}
          placeholder="org.khanacademy.android, com.duolingo"
          onChange={(e) => setInstall(e.target.value)}
          data-testid={`mdm-install-apps-${owner}`}
        />
      </label>
      <label className="flex items-center justify-between gap-2 pt-1 text-xs text-slate-200">
        <span>Dedicated kiosk (COSU) mode — only allow Jarvis, calls/texts, and the kiosk apps below</span>
        <input
          type="checkbox"
          checked={kioskMode}
          disabled={save.isPending}
          onChange={(e) => save.mutate({ kiosk_mode: e.target.checked })}
          data-testid={`mdm-kiosk-mode-${owner}`}
        />
      </label>
      <label className="block text-xs text-slate-300">
        Kiosk allowed packages (comma separated)
        <input
          className="glass-input mt-1 w-full bg-black/30 text-sm"
          value={currentKioskApps}
          placeholder="org.khanacademy.android, com.sirma.mobile.bible.android"
          onChange={(e) => setKioskApps(e.target.value)}
          data-testid={`mdm-kiosk-apps-${owner}`}
        />
      </label>
      <button
        type="button"
        disabled={save.isPending || (blocked === null && uninstall === null && install === null && kioskApps === null)}
        onClick={() => save.mutate({
          blocked_apps: parsePackages(currentBlocked),
          uninstall_apps: parsePackages(currentUninstall),
          install_apps: parsePackages(currentInstall),
          kiosk_apps: parsePackages(currentKioskApps),
        })}
        className={`glass-button px-4 text-xs ${TOUCH}`}
        data-testid={`mdm-blocked-save-${owner}`}
      >
        {save.isPending ? <Loader2 size={14} className="animate-spin" /> : null} Save app &amp; kiosk lists
      </button>
    </div>
  );
}

interface EsphomeEntityRow {
  domain?: string;
  key?: number | string;
  object_id?: string;
  name?: string;
  state?: string | number | boolean | null;
  unit?: string;
  options?: string[];
  min_value?: number;
  max_value?: number;
  step?: number;
  brightness_pct?: number;
  volume_pct?: number;
  muted?: boolean;
  effect?: string;
  effects?: string[];
}

interface EsphomeServiceRow {
  name?: string;
  args?: string[];
}

const SENSOR_DOMAINS = new Set(['sensor', 'binary_sensor', 'text_sensor']);
const CONTROLLABLE_DOMAINS = new Set([
  'button',
  'switch',
  'light',
  'select',
  'number',
  'text',
  'media_player',
  'update',
]);

const SCREEN_PAGES: Array<{ page: number; label: string }> = [
  { page: 0, label: 'Home' },
  { page: 1, label: 'Climate' },
  { page: 2, label: 'Weather' },
  { page: 3, label: 'Energy' },
  { page: 4, label: 'Media' },
  { page: 5, label: 'Settings' },
];

const SCREEN_DIALOGS: Array<{ dlg: number; label: string }> = [
  { dlg: 1, label: 'Play On' },
  { dlg: 2, label: 'Playlists' },
  { dlg: 3, label: 'Clock' },
  { dlg: 0, label: 'Close dialog' },
];

function formatEntityState(ent: EsphomeEntityRow): string | null {
  if (ent.state === undefined || ent.state === null || ent.state === '') return null;
  const domain = (ent.domain || '').toLowerCase();
  if (domain === 'binary_sensor' || domain === 'switch') {
    return ent.state ? 'on' : 'off';
  }
  if (domain === 'light') {
    const onOff = ent.state ? 'on' : 'off';
    return ent.brightness_pct != null && ent.state ? `${onOff} (${ent.brightness_pct}%)` : onOff;
  }
  if (domain === 'media_player') {
    const parts = [String(ent.state)];
    if (ent.volume_pct != null) parts.push(`${ent.volume_pct}%`);
    if (ent.muted) parts.push('muted');
    return parts.join(' · ');
  }
  return ent.unit ? `${ent.state} ${ent.unit}` : String(ent.state);
}

function EsphomeDeviceSection({
  device,
  isAdmin = true,
  onOpenDetails,
  onRefresh,
}: {
  device: MdmDevice;
  isAdmin?: boolean;
  onOpenDetails: () => void;
  onRefresh: () => void;
}) {
  const queryClient = useQueryClient();
  const target = esphomeTargetName(device);
  const [entities, setEntities] = useState<EsphomeEntityRow[] | null>(null);
  const [services, setServices] = useState<EsphomeServiceRow[]>([]);
  const [probeInfo, setProbeInfo] = useState<Record<string, unknown> | null>(null);
  const [probeError, setProbeError] = useState<string | null>(null);
  const [numDrafts, setNumDrafts] = useState<Record<string, string>>({});
  const [textDrafts, setTextDrafts] = useState<Record<string, string>>({});
  const [glanceLine, setGlanceLine] = useState('');
  const [notifyTitle, setNotifyTitle] = useState('');
  const [notifyMsg, setNotifyMsg] = useState('');
  const [otaPassword, setOtaPassword] = useState('');

  const probe = useMutation({
    mutationFn: () => api.esphomeList(target),
    onSuccess: (res) => {
      if (res.status === 'FAILURE') {
        setProbeError(res.message || 'Could not reach this ESPHome device.');
        setEntities(null);
        setServices([]);
        return;
      }
      setProbeError(null);
      const detail = (res.detail ?? {}) as Record<string, unknown>;
      setProbeInfo(
        ((detail.device as Record<string, unknown>) ??
          (detail.device_info as Record<string, unknown>) ??
          null),
      );
      const list = Array.isArray(detail.entities) ? (detail.entities as EsphomeEntityRow[]) : [];
      const svcs = Array.isArray(detail.services) ? (detail.services as EsphomeServiceRow[]) : [];
      setEntities(list);
      setServices(svcs);
      toast.success(`Connected to ${device.label || target} (${list.length} entities)`);
    },
    onError: (e: Error) => {
      const msg = e.message || 'Could not reach this ESPHome device (it may be asleep or out of range).';
      setProbeError(msg);
      toast.error(msg);
    },
  });

  const syncJarvis = useMutation({
    mutationFn: () => api.esphomeConfigureJarvis(target),
    onSuccess: (res) => {
      if (res.status === 'FAILURE') {
        toast.error(res.message || 'Could not sync Jarvis credentials to this device');
        return;
      }
      toast.success(res.message || `Synced Jarvis URL and API key to ${device.label || target}`);
    },
    onError: (e: Error) => toast.error(e.message || 'Could not sync Jarvis credentials to this device'),
  });

  const triggerEntity = useMutation({
    mutationFn: (vars: { entityName: string; params?: Record<string, unknown> }) =>
      api.esphomeCommand(target, vars.entityName, vars.params),
    onSuccess: (res, vars) => {
      if (res.status === 'FAILURE') {
        toast.error(res.message || `Could not trigger ${vars.entityName}`);
        return;
      }
      toast.success(res.message || `Sent command to ${vars.entityName}`);
    },
    onError: (e: Error) => toast.error(e.message || 'ESPHome command failed'),
  });

  const otaUpload = useMutation({
    mutationFn: (file: File) => api.uploadEsphomeOta(target, file, otaPassword),
    onSuccess: (res) => {
      toast.success(res.message || `Uploaded ${res.filename} to ${device.label || target}`);
      queryClient.invalidateQueries({ queryKey: ['mdm-audit'] });
      onRefresh();
    },
    onError: (e: Error) => toast.error(e.message || 'ESPHome OTA upload failed'),
  });

  const serviceNames = new Set(services.map((s) => (s.name || '').toLowerCase()).filter(Boolean));
  const hasWatchActions =
    serviceNames.has('find_watch') ||
    serviceNames.has('set_time') ||
    serviceNames.has('set_glance') ||
    serviceNames.has('notify');
  const hasScreenActions =
    serviceNames.has('show_page') ||
    serviceNames.has('set_rotation') ||
    serviceNames.has('show_dlg');

  const sensorEntities = (entities ?? []).filter(
    (e) => SENSOR_DOMAINS.has((e.domain || '').toLowerCase()) && formatEntityState(e) !== null,
  );
  const controlEntities = (entities ?? []).filter((e) => {
    const domain = (e.domain || '').toLowerCase();
    if (SENSOR_DOMAINS.has(domain)) return false;
    if (!isAdmin) {
      if (domain === 'update') return false;
      const name = `${e.object_id || ''} ${e.name || ''}`.toLowerCase();
      if (/(restart|reboot|factory|reset|safe mode|shutdown|firmware|ota|update|wifi|encryption|api key|provision)/i.test(name)) {
        return false;
      }
    }
    return true;
  });

  return (
    <div className="space-y-2" data-testid={`mdm-esphome-${device.device_key}`}>
      <div className="rounded-xl border border-indigo-500/20 bg-indigo-500/5 p-2.5 text-xs space-y-2">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-wider text-indigo-300">
            ESPHome companion device · native TCP API &amp; OTA
          </p>
          <p className="mt-1 text-slate-300">
            Managed over ESPHome native protocol rather than Android MDM. Wake the {deviceNoun(device.kind)} before probing, controlling, or pushing OTA firmware if it sleeps between checks.
          </p>
        </div>
        {probeError && (
          <p className="text-amber-300" data-testid={`mdm-esphome-error-${device.device_key}`}>
            {probeError}
          </p>
        )}
        {probeInfo && (
          <p className="text-emerald-300" data-testid={`mdm-esphome-info-${device.device_key}`}>
            Online · {[
              probeInfo.friendly_name || probeInfo.name,
              probeInfo.model,
              probeInfo.esphome_version ? `ESPHome ${String(probeInfo.esphome_version)}` : null,
              probeInfo.project_version ? `fw ${String(probeInfo.project_version)}` : null,
            ].filter(Boolean).join(' · ')}
          </p>
        )}

        {sensorEntities.length > 0 && (
          <div className="border-t border-white/10 pt-2" data-testid={`mdm-esphome-telemetry-${device.device_key}`}>
            <p className="text-[10px] uppercase tracking-wider text-slate-400">
              Live telemetry ({sensorEntities.length})
            </p>
            <dl className="mt-1 grid grid-cols-2 sm:grid-cols-3 gap-1.5">
              {sensorEntities.map((ent, idx) => {
                const label = ent.name || ent.object_id || String(ent.key ?? idx);
                const val = formatEntityState(ent);
                return (
                  <div
                    key={`sensor-${ent.object_id || idx}`}
                    className="rounded-lg border border-white/5 bg-black/25 px-2 py-1"
                  >
                    <dt className="truncate text-[10px] text-slate-400">{label}</dt>
                    <dd className="truncate font-medium text-slate-100">{val}</dd>
                  </div>
                );
              })}
            </dl>
          </div>
        )}

        {(hasWatchActions || hasScreenActions) && (
          <div className="border-t border-white/10 pt-2 space-y-2" data-testid={`mdm-esphome-services-${device.device_key}`}>
            <p className="text-[10px] uppercase tracking-wider text-slate-400">
              Device quick actions
            </p>
            {hasWatchActions && (
              <div className="space-y-1.5">
                <div className="flex flex-wrap gap-1.5">
                  {serviceNames.has('find_watch') && (
                    <button
                      type="button"
                      onClick={() => triggerEntity.mutate({ entityName: 'find_watch' })}
                      className="inline-flex items-center gap-1 rounded-lg border border-sky-500/30 bg-sky-500/10 px-2.5 py-1 text-[11px] text-sky-200 hover:bg-sky-500/20"
                      data-testid={`mdm-esphome-svc-${device.device_key}-find_watch`}
                    >
                      <Bell size={12} /> Find watch
                    </button>
                  )}
                  {serviceNames.has('set_time') && (
                    <button
                      type="button"
                      onClick={() =>
                        triggerEntity.mutate({
                          entityName: 'set_time',
                          params: { epoch: Math.floor(Date.now() / 1000) },
                        })
                      }
                      className="inline-flex items-center gap-1 rounded-lg border border-sky-500/30 bg-sky-500/10 px-2.5 py-1 text-[11px] text-sky-200 hover:bg-sky-500/20"
                      data-testid={`mdm-esphome-svc-${device.device_key}-set_time`}
                    >
                      <Clock size={12} /> Sync clock
                    </button>
                  )}
                </div>
                {serviceNames.has('set_glance') && (
                  <div className="flex flex-wrap items-center gap-1.5">
                    <input
                      type="text"
                      aria-label={`Glance text for ${device.label || target}`}
                      value={glanceLine}
                      onChange={(e) => setGlanceLine(e.target.value)}
                      placeholder="Watch face glance text…"
                      className="glass-input flex-1 min-w-[140px] bg-black/30 px-2 py-1 text-xs"
                      data-testid={`mdm-esphome-glance-input-${device.device_key}`}
                    />
                    <button
                      type="button"
                      onClick={() =>
                        triggerEntity.mutate({
                          entityName: 'set_glance',
                          params: { line: glanceLine },
                        })
                      }
                      className="rounded-lg border border-white/10 bg-white/5 px-2.5 py-1 text-[11px] text-sky-300 hover:bg-white/10"
                      data-testid={`mdm-esphome-svc-${device.device_key}-set_glance`}
                    >
                      Set glance
                    </button>
                  </div>
                )}
                {serviceNames.has('notify') && (
                  <div className="flex flex-wrap items-center gap-1.5">
                    <input
                      type="text"
                      aria-label={`Notification title for ${device.label || target}`}
                      value={notifyTitle}
                      onChange={(e) => setNotifyTitle(e.target.value)}
                      placeholder="Title"
                      className="glass-input w-28 bg-black/30 px-2 py-1 text-xs"
                      data-testid={`mdm-esphome-notify-title-${device.device_key}`}
                    />
                    <input
                      type="text"
                      aria-label={`Notification message for ${device.label || target}`}
                      value={notifyMsg}
                      onChange={(e) => setNotifyMsg(e.target.value)}
                      placeholder="Message to show on watch…"
                      className="glass-input flex-1 min-w-[140px] bg-black/30 px-2 py-1 text-xs"
                      data-testid={`mdm-esphome-notify-msg-${device.device_key}`}
                    />
                    <button
                      type="button"
                      disabled={!notifyTitle.trim() && !notifyMsg.trim()}
                      onClick={() =>
                        triggerEntity.mutate({
                          entityName: 'notify',
                          params: { title: notifyTitle.trim() || 'Jarvis', message: notifyMsg.trim() },
                        })
                      }
                      className="rounded-lg border border-white/10 bg-white/5 px-2.5 py-1 text-[11px] text-sky-300 hover:bg-white/10"
                      data-testid={`mdm-esphome-svc-${device.device_key}-notify`}
                    >
                      Send notice
                    </button>
                  </div>
                )}
              </div>
            )}

            {hasScreenActions && (
              <div className="space-y-1.5">
                {serviceNames.has('show_page') && (
                  <div className="flex flex-wrap items-center gap-1">
                    <span className="text-[10px] text-slate-400 mr-1">Screen page:</span>
                    {SCREEN_PAGES.map((p) => (
                      <button
                        key={p.page}
                        type="button"
                        onClick={() =>
                          triggerEntity.mutate({ entityName: 'show_page', params: { page: p.page } })
                        }
                        className="rounded-lg border border-white/10 bg-white/5 px-2 py-0.5 text-[11px] text-sky-300 hover:bg-white/10"
                        data-testid={`mdm-esphome-page-${device.device_key}-${p.page}`}
                      >
                        {p.label}
                      </button>
                    ))}
                  </div>
                )}
                {serviceNames.has('set_rotation') && (
                  <div className="flex flex-wrap items-center gap-1">
                    <span className="text-[10px] text-slate-400 mr-1">Orientation:</span>
                    {[0, 180].map((angle) => (
                      <button
                        key={angle}
                        type="button"
                        onClick={() =>
                          triggerEntity.mutate({ entityName: 'set_rotation', params: { angle } })
                        }
                        className="inline-flex items-center gap-1 rounded-lg border border-white/10 bg-white/5 px-2 py-0.5 text-[11px] text-sky-300 hover:bg-white/10"
                        data-testid={`mdm-esphome-rot-${device.device_key}-${angle}`}
                      >
                        <RotateCw size={11} /> {angle}°
                      </button>
                    ))}
                  </div>
                )}
                {serviceNames.has('show_dlg') && (
                  <div className="flex flex-wrap items-center gap-1">
                    <span className="text-[10px] text-slate-400 mr-1">Dialog:</span>
                    {SCREEN_DIALOGS.map((d) => (
                      <button
                        key={d.dlg}
                        type="button"
                        onClick={() =>
                          triggerEntity.mutate({ entityName: 'show_dlg', params: { dlg: d.dlg } })
                        }
                        className="rounded-lg border border-white/10 bg-white/5 px-2 py-0.5 text-[11px] text-slate-300 hover:bg-white/10"
                        data-testid={`mdm-esphome-dlg-${device.device_key}-${d.dlg}`}
                      >
                        {d.label}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {entities && (
          <div className="border-t border-white/10 pt-2" data-testid={`mdm-esphome-entities-${device.device_key}`}>
            <p className="text-[10px] uppercase tracking-wider text-slate-400">
              Exposed entities ({entities.length})
            </p>
            {entities.length === 0 ? (
              <p className="mt-1 text-slate-500">No entities exposed on this node.</p>
            ) : (
              <ul className="mt-1 max-h-64 space-y-1.5 overflow-y-auto">
                {(controlEntities.length > 0 ? controlEntities : entities).map((ent, idx) => {
                  const domain = (ent.domain || '').toLowerCase();
                  const label = ent.name || ent.object_id || String(ent.key ?? idx);
                  const targetEntity = ent.object_id || ent.name || String(ent.key ?? '');
                  const canTrigger = CONTROLLABLE_DOMAINS.has(domain) && Boolean(targetEntity);
                  const stateSummary = formatEntityState(ent);
                  return (
                    <li
                      key={`${domain}-${targetEntity}-${idx}`}
                      className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-white/5 bg-black/20 px-2 py-1"
                    >
                      <span className="min-w-0 truncate text-slate-200">
                        {label}{' '}
                        <span className="text-[10px] uppercase text-slate-500">({domain || 'entity'})</span>
                        {stateSummary ? (
                          <span className="ml-1.5 text-[11px] text-emerald-300">· {stateSummary}</span>
                        ) : null}
                      </span>

                      {canTrigger && (domain === 'button' || domain === 'switch' || domain === 'light' || domain === 'update') && (
                        <div className="flex flex-wrap items-center gap-1.5">
                          {domain === 'light' && (
                            <input
                              type="range"
                              min={0}
                              max={100}
                              aria-label={`Brightness for ${label}`}
                              defaultValue={ent.brightness_pct ?? 80}
                              onChange={(e) =>
                                triggerEntity.mutate({
                                  entityName: targetEntity,
                                  params: { state: true, brightness_pct: Number(e.target.value) },
                                })
                              }
                              className="w-20 accent-sky-400"
                              data-testid={`mdm-esphome-bri-${device.device_key}-${targetEntity}`}
                            />
                          )}
                          <button
                            type="button"
                            onClick={() =>
                              triggerEntity.mutate({
                                entityName: targetEntity,
                                params:
                                  typeof ent.state === 'boolean'
                                    ? { state: !ent.state }
                                    : undefined,
                              })
                            }
                            className="rounded-lg border border-white/10 bg-white/5 px-2 py-0.5 text-[11px] text-sky-300 hover:bg-white/10"
                            data-testid={`mdm-esphome-call-${device.device_key}-${targetEntity}`}
                          >
                            {domain === 'button' ? 'Press' : domain === 'update' ? 'Install' : 'Toggle'}
                          </button>
                        </div>
                      )}

                      {canTrigger && domain === 'select' && Array.isArray(ent.options) && ent.options.length > 0 && (
                        <select
                          aria-label={`${label} on ${device.label || target}`}
                          value={typeof ent.state === 'string' && ent.options.includes(ent.state) ? ent.state : ent.options[0]}
                          onChange={(e) =>
                            triggerEntity.mutate({
                              entityName: targetEntity,
                              params: { option: e.target.value },
                            })
                          }
                          className="glass-input bg-black/40 px-2 py-0.5 text-xs text-slate-100"
                          data-testid={`mdm-esphome-select-${device.device_key}-${targetEntity}`}
                        >
                          {ent.options.map((opt) => (
                            <option key={opt} value={opt}>
                              {opt}
                            </option>
                          ))}
                        </select>
                      )}

                      {canTrigger && domain === 'number' && (
                        <div className="flex items-center gap-1">
                          <input
                            type="number"
                            aria-label={`${label} value`}
                            min={ent.min_value}
                            max={ent.max_value}
                            step={ent.step ?? 1}
                            value={numDrafts[targetEntity] ?? (ent.state != null ? String(ent.state) : '')}
                            onChange={(e) =>
                              setNumDrafts((prev) => ({ ...prev, [targetEntity]: e.target.value }))
                            }
                            className="glass-input w-20 bg-black/40 px-2 py-0.5 text-xs text-slate-100"
                            data-testid={`mdm-esphome-num-input-${device.device_key}-${targetEntity}`}
                          />
                          <button
                            type="button"
                            onClick={() => {
                              const raw = numDrafts[targetEntity] ?? (ent.state != null ? String(ent.state) : '0');
                              const num = Number(raw);
                              if (!Number.isNaN(num)) {
                                triggerEntity.mutate({ entityName: targetEntity, params: { value: num } });
                              }
                            }}
                            className="rounded-lg border border-white/10 bg-white/5 px-2 py-0.5 text-[11px] text-sky-300 hover:bg-white/10"
                            data-testid={`mdm-esphome-num-set-${device.device_key}-${targetEntity}`}
                          >
                            Set
                          </button>
                        </div>
                      )}

                      {canTrigger && domain === 'text' && (
                        <div className="flex items-center gap-1">
                          <input
                            type="text"
                            aria-label={`${label} text`}
                            value={textDrafts[targetEntity] ?? (typeof ent.state === 'string' ? ent.state : '')}
                            onChange={(e) =>
                              setTextDrafts((prev) => ({ ...prev, [targetEntity]: e.target.value }))
                            }
                            className="glass-input w-32 bg-black/40 px-2 py-0.5 text-xs text-slate-100"
                            data-testid={`mdm-esphome-text-input-${device.device_key}-${targetEntity}`}
                          />
                          <button
                            type="button"
                            onClick={() => {
                              const val = textDrafts[targetEntity] ?? (typeof ent.state === 'string' ? ent.state : '');
                              triggerEntity.mutate({ entityName: targetEntity, params: { value: val } });
                            }}
                            className="rounded-lg border border-white/10 bg-white/5 px-2 py-0.5 text-[11px] text-sky-300 hover:bg-white/10"
                            data-testid={`mdm-esphome-text-set-${device.device_key}-${targetEntity}`}
                          >
                            Send
                          </button>
                        </div>
                      )}

                      {canTrigger && domain === 'media_player' && (
                        <div className="flex flex-wrap items-center gap-1">
                          <button
                            type="button"
                            onClick={() =>
                              triggerEntity.mutate({
                                entityName: targetEntity,
                                params: { command: ent.state === 'playing' ? 'pause' : 'play' },
                              })
                            }
                            aria-label={`Play or pause ${label}`}
                            className="inline-flex items-center gap-1 rounded-lg border border-white/10 bg-white/5 px-2 py-0.5 text-[11px] text-sky-300 hover:bg-white/10"
                            data-testid={`mdm-esphome-media-play-${device.device_key}-${targetEntity}`}
                          >
                            {ent.state === 'playing' ? <Pause size={11} /> : <Play size={11} />}
                            {ent.state === 'playing' ? 'Pause' : 'Play'}
                          </button>
                          <button
                            type="button"
                            onClick={() =>
                              triggerEntity.mutate({
                                entityName: targetEntity,
                                params: { command: 'stop' },
                              })
                            }
                            aria-label={`Stop ${label}`}
                            className="inline-flex items-center gap-1 rounded-lg border border-white/10 bg-white/5 px-2 py-0.5 text-[11px] text-slate-300 hover:bg-white/10"
                            data-testid={`mdm-esphome-media-stop-${device.device_key}-${targetEntity}`}
                          >
                            <Square size={11} /> Stop
                          </button>
                          <button
                            type="button"
                            onClick={() =>
                              triggerEntity.mutate({
                                entityName: targetEntity,
                                params: { command: ent.muted ? 'unmute' : 'mute' },
                              })
                            }
                            aria-label={ent.muted ? `Unmute ${label}` : `Mute ${label}`}
                            className="inline-flex items-center gap-1 rounded-lg border border-white/10 bg-white/5 px-2 py-0.5 text-[11px] text-slate-300 hover:bg-white/10"
                            data-testid={`mdm-esphome-media-mute-${device.device_key}-${targetEntity}`}
                          >
                            {ent.muted ? <VolumeX size={11} /> : <Volume2 size={11} />}
                            {ent.muted ? 'Unmute' : 'Mute'}
                          </button>
                          <input
                            type="range"
                            min={0}
                            max={100}
                            aria-label={`Volume for ${label}`}
                            defaultValue={ent.volume_pct ?? 60}
                            onChange={(e) =>
                              triggerEntity.mutate({
                                entityName: targetEntity,
                                params: { volume_pct: Number(e.target.value) },
                              })
                            }
                            className="w-20 accent-sky-400"
                            data-testid={`mdm-esphome-media-vol-${device.device_key}-${targetEntity}`}
                          />
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        )}

        {isAdmin && (
          <div
            className="border-t border-white/10 pt-2 flex flex-wrap items-center gap-2"
            data-testid={`mdm-esphome-ota-box-${device.device_key}`}
          >
            <input
              type="password"
              aria-label={`OTA password for ${device.label || target}`}
              value={otaPassword}
              onChange={(e) => setOtaPassword(e.target.value)}
              placeholder="OTA password (optional)"
              className="glass-input w-44 bg-black/30 px-2.5 py-1 text-xs"
              data-testid={`mdm-esphome-ota-pass-${device.device_key}`}
            />
            <label
              className={`glass-button inline-flex cursor-pointer items-center gap-1.5 px-3 text-xs ${TOUCH}`}
            >
              {otaUpload.isPending ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
              Upload firmware (.bin) OTA
              <input
                type="file"
                accept=".bin,.bin.gz,.ota"
                className="hidden"
                disabled={otaUpload.isPending}
                data-testid={`mdm-esphome-ota-${device.device_key}`}
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) otaUpload.mutate(file);
                  e.target.value = '';
                }}
              />
            </label>
          </div>
        )}
      </div>

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          disabled={probe.isPending}
          onClick={() => probe.mutate()}
          className={`glass-button px-3 text-xs ${TOUCH}`}
          data-testid={`mdm-esphome-probe-${device.device_key}`}
        >
          {probe.isPending ? <Loader2 size={14} className="animate-spin" /> : <Radio size={14} />} Ping &amp; list entities
        </button>
        {isAdmin && (
          <button
            type="button"
            disabled={syncJarvis.isPending}
            onClick={() => syncJarvis.mutate()}
            className={`glass-button px-3 text-xs ${TOUCH}`}
            data-testid={`mdm-esphome-sync-${device.device_key}`}
          >
            {syncJarvis.isPending ? <Loader2 size={14} className="animate-spin" /> : <KeyRound size={14} />} Sync Jarvis key &amp; URL
          </button>
        )}
        <button
          type="button"
          onClick={onOpenDetails}
          className={`glass-button px-3 text-xs ${TOUCH}`}
          data-testid={`mdm-details-${device.device_key}`}
        >
          <Activity size={14} /> Battery, steps &amp; history
        </button>
      </div>
    </div>
  );
}

function DeviceCard({
  device,
  members = [],
  isAdmin = true,
  onRefresh,
  onOpenDetails,
}: {
  device: MdmDevice;
  members?: MdmMember[];
  isAdmin?: boolean;
  onRefresh: () => void;
  onOpenDetails: (device: MdmDevice) => void;
}) {
  const [showApps, setShowApps] = useState(false);
  const esphome = isEsphomeDevice(device);
  const noun = deviceNoun(device.kind);
  const Icon = KIND_ICON[(device.kind || '').toLowerCase()] ?? (esphome ? Cpu : Smartphone);
  const guard = device.capabilities?.guard;
  // A lock really covers the phone/tablet on a device owner (Android's own lock screen
  // plus lock task) or on any Android device with the screen guard on (the full-screen
  // Accessibility overlay). With the guard off it is only a notification.
  const reallyLocks = Boolean(guard?.deviceOwner || guard?.guardEnabled);
  const batteryText =
    device.battery?.pct != null
      ? `${Math.round(device.battery.pct)}%${device.battery.usb ? ' (charging)' : ''}`
      : device.battery?.usb
        ? 'on USB (charging)'
        : null;

  const facts: Array<[string, string]> = esphome
    ? [
        ['Owner', device.owner_display_name || device.owner_username || 'unknown'],
        ['Platform', `ESPHome · ${deviceKindLabel(device.kind)}`],
        ['Hardware', device.hardware || device.model || 'unknown'],
        ['ESPHome', device.esphome_version || 'unknown'],
        ['Firmware', device.app_version || 'unknown'],
        ...(batteryText ? ([['Battery', batteryText]] as Array<[string, string]>) : []),
        ['Last seen', device.last_seen_at ? relativeTime(device.last_seen_at) : 'never'],
      ]
    : [
        ['Owner', device.owner_display_name || device.owner_username || 'unknown'],
        ['Form factor', deviceKindLabel(device.kind)],
        ['Model', [device.manufacturer, device.model].filter(Boolean).join(' ') || 'unknown'],
        ['Android', [device.os_version, device.os_build].filter(Boolean).join(' · ') || 'unknown'],
        ['Jarvis app', device.app_version ? `${device.app_version}${device.app_build ? ` (build ${device.app_build})` : ''}` : 'unknown'],
        ...(batteryText ? ([['Battery', batteryText]] as Array<[string, string]>) : []),
        ['Last seen', device.last_seen_at ? relativeTime(device.last_seen_at) : 'never'],
      ];

  const assignOwner = useMutation({
    mutationFn: (username: string) => api.assignCompanionDevice(device.device_key, username),
    onSuccess: (_res, username) => {
      toast.success(username ? `Assigned to ${username}` : 'Device unassigned');
      onRefresh();
    },
    onError: (e: Error) => toast.error(e.message || 'Could not assign device owner'),
  });

  const update = useMutation({
    mutationFn: () => api.pushMdmAppUpdate({ device_keys: [device.device_key] }),
    onSuccess: (r) => toast.success(`Update notice sent to ${r.owners.join(', ')}`),
    onError: (e: Error) => toast.error(e.message || 'Could not push the update'),
  });
  const lock = useMutation({
    mutationFn: (resume: boolean) => api.pauseScreenTime(device.owner_username, resume ? { resume: true } : {}),
    onSuccess: (_r, resume) => {
      if (resume) {
        toast.success('Unlocked');
      } else if (reallyLocks) {
        toast.success('Locked');
      } else {
        // Never claim a lock Android did not really apply: with the screen guard
        // off this device only shows a notification (see PhoneProtectionCard).
        toast(`Not locked — the screen guard is off on this ${noun}, so it only got a notification`, { icon: '⚠️' });
      }
    },
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
  const reclassify = useMutation({
    mutationFn: (nextKind: string) =>
      api.updateCompanionDevice(device.device_key, { kind: nextKind }),
    onSuccess: (_res, nextKind) => {
      toast.success(`Marked as ${deviceKindLabel(nextKind)}`);
      onRefresh();
    },
    onError: (e: Error) => toast.error(e.message || 'Could not update device type'),
  });

  const nextMobileKind: 'phone' | 'tablet' = device.kind === 'tablet' ? 'phone' : 'tablet';

  return (
    <li className="space-y-2 px-4 py-3 sm:px-6" data-testid={`mdm-device-${device.device_key}`}>
      <div className="flex flex-wrap items-center gap-2">
        <Icon size={18} className={`shrink-0 ${esphome ? 'text-indigo-400' : 'text-sky-400'}`} />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm text-white">{device.label || device.model || deviceKindLabel(device.kind)}</span>
          <span className="block text-xs text-slate-500" data-testid={`mdm-device-badge-${device.device_key}`}>
            {devicePlatformLabel(device)} · {deviceKindLabel(device.kind)} · {device.device_key}
          </span>
        </span>
        {isAdmin && members.length > 0 && (
          <select
            aria-label={`Owner for ${device.label || device.device_key}`}
            value={device.owner_username || ''}
            disabled={assignOwner.isPending}
            onChange={(e) => assignOwner.mutate(e.target.value)}
            className="glass-input bg-black/30 px-2.5 py-1 text-xs text-slate-200"
            data-testid={`mdm-device-owner-${device.device_key}`}
          >
            <option value="">(Unassigned)</option>
            {members.map((m) => (
              <option key={m.username} value={m.username}>
                {m.display_name || m.username}
              </option>
            ))}
          </select>
        )}
        {isAdmin && esphome && (
          <select
            aria-label={`Device type for ${device.label || device.device_key}`}
            value={ESPHOME_KIND_OPTIONS.includes(device.kind as (typeof ESPHOME_KIND_OPTIONS)[number]) ? device.kind : 'assistant'}
            disabled={reclassify.isPending}
            onChange={(e) => reclassify.mutate(e.target.value)}
            className="glass-input bg-black/30 px-2.5 py-1 text-xs text-slate-200"
            data-testid={`mdm-esphome-kind-${device.device_key}`}
          >
            {ESPHOME_KIND_OPTIONS.map((k) => (
              <option key={k} value={k}>
                {deviceKindLabel(k)}
              </option>
            ))}
          </select>
        )}
        {isAdmin && !esphome && (device.kind === 'phone' || device.kind === 'tablet') && (
          <button
            type="button"
            disabled={reclassify.isPending}
            onClick={() => reclassify.mutate(nextMobileKind)}
            className="rounded-full border border-white/10 bg-white/5 px-2.5 py-1 text-[11px] text-slate-300 hover:bg-white/10"
            data-testid={`mdm-kind-toggle-${device.device_key}`}
          >
            Mark as {deviceKindLabel(nextMobileKind).toLowerCase()}
          </button>
        )}
      </div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        {facts.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-slate-500">{k}</dt>
            <dd className="min-w-0 break-words text-slate-200">{v}</dd>
          </div>
        ))}
      </dl>
      {esphome ? (
        <EsphomeDeviceSection
          device={device}
          isAdmin={isAdmin}
          onOpenDetails={() => onOpenDetails(device)}
          onRefresh={onRefresh}
        />
      ) : (
        <>
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
              <p className="mt-1 text-slate-500">This {noun} has not reported its protection yet.</p>
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
              onClick={() => onOpenDetails(device)}
              className={`glass-button px-3 text-xs ${TOUCH}`}
              data-testid={`mdm-details-${device.device_key}`}
            >
              <Activity size={14} /> Details &amp; history
            </button>
            <button
              type="button"
              disabled={wipe.isPending}
              onClick={async () => {
                if (await confirmDialog({
                  title: `Erase this ${noun}?`,
                  body: `This erases EVERYTHING on ${device.label || device.device_key}. It cannot be undone.`,
                  confirmLabel: 'Erase',
                  danger: true,
                })) wipe.mutate();
              }}
              className={`glass-button border-red-500/30 bg-red-600/20 px-3 text-xs text-red-200 ${TOUCH}`}
              data-testid={`mdm-wipe-${device.device_key}`}
            >
              <ShieldAlert size={14} /> Wipe…
            </button>
          </div>
          {showApps && <AppControls owner={device.owner_username} />}
        </>
      )}
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
        {folder.folder === 'library' && <LibraryBooks />}
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

/** The read-only Library shelf, shown beside the parent's Library folder. */
function LibraryBooks() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['mdm-library-books'],
    queryFn: () => api.getMdmLibraryBooks(),
    retry: false,
  });
  return (
    <div className="border-t border-white/5 pt-2" data-testid="mdm-library-books">
      <p className="text-[10px] uppercase tracking-wider text-slate-500">From the family library (read-only)</p>
      {isLoading && <p className="mt-1 text-xs text-slate-500">Loading library…</p>}
      {error && <p className="mt-1 text-xs text-amber-300/90">The family library could not be loaded.</p>}
      {data && !data.configured && <p className="mt-1 text-xs text-slate-500">{data.reason}</p>}
      {data?.configured && data.error && <p className="mt-1 text-xs text-amber-300/90">{data.error}</p>}
      {data?.configured && !data.error && data.books.length === 0 && (
        <p className="mt-1 text-xs text-slate-500">The family library has no books yet.</p>
      )}
      {data?.configured && !data.error && data.books.length > 0 && (
        <ul className="mt-1 max-h-56 space-y-1 overflow-auto text-xs">
          {data.books.map((b) => (
            <li key={b.path} className="flex items-center justify-between gap-2">
              <span className="min-w-0 truncate text-slate-200">{b.name}</span>
              <span className="shrink-0 text-[10px] uppercase tracking-wider text-slate-500">{b.format}</span>
            </li>
          ))}
        </ul>
      )}
      {data?.truncated && (
        <p className="mt-1 text-xs text-slate-500">
          Showing the first {data.books.length} of {data.count} books.
        </p>
      )}
    </div>
  );
}

/** Plain-language labels for the MDM audit actions (the server's action names). */
const AUDIT_LABELS: Record<string, string> = {
  app_update_push: 'pushed an app update',
  esphome_ota: 'flashed ESPHome firmware over the air',
  folder_push: 'pushed a shared folder',
  file_upload: 'uploaded a shared file',
  file_delete: 'removed a shared file',
  device_wipe: 'queued a device wipe',
  device_lock: 'locked a device',
  device_unlock: 'unlocked a device',
};

/** Who did what to the family's devices and shared files (HANDOVER.md Phase 12). */
function AuditLogPanel() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['mdm-audit'],
    queryFn: () => api.getMdmAudit(),
    retry: false,
  });
  return (
    <Panel title="Activity log" icon={History}
      aside={<span className="text-xs text-slate-500">{data?.events.length ?? 0} recent</span>}>
      <div className="space-y-1 p-4" data-testid="mdm-audit">
        {isLoading && <p className="text-xs text-slate-500">Loading activity…</p>}
        {error && <p className="text-xs text-amber-300/90">The activity log could not be loaded.</p>}
        {data && data.events.length === 0 && (
          <p className="text-xs text-slate-500">No device-management actions have been taken yet.</p>
        )}
        <ul className="space-y-1 text-xs">
          {data?.events.map((e, i) => (
            <li key={`${e.at}-${i}`} className="flex flex-wrap items-baseline gap-x-2">
              <span className="text-slate-200">{e.actor}</span>
              <span className="text-slate-400">{AUDIT_LABELS[e.action] ?? e.action}</span>
              {e.target && <span className="min-w-0 break-words text-slate-500">{e.target}</span>}
              <span className="ml-auto shrink-0 text-slate-600">{relativeTime(e.at)}</span>
            </li>
          ))}
        </ul>
      </div>
    </Panel>
  );
}

export default function Devices() {
  const user = useAuth().user;
  const isAdmin = Boolean(user?.is_admin || user?.family_role === 'father' || user?.username === 'default');
  const queryClient = useQueryClient();
  const [detailDevice, setDetailDevice] = useState<MdmDevice | null>(null);
  const inventory = useQuery({ queryKey: ['mdm-devices'], queryFn: () => api.getMdmDevices(), enabled: isAdmin, retry: false });
  const myDevices = useQuery({ queryKey: ['my-companion-devices'], queryFn: () => api.getCompanionDevices(), enabled: !isAdmin, retry: false });
  const files = useQuery({ queryKey: ['mdm-files'], queryFn: () => api.getMdmFiles(), enabled: isAdmin, retry: false });

  if (!isAdmin) {
    return (
      <div className={PAGE}>
        <header className="glass-panel rounded-2xl border border-white/10 p-4 sm:p-5">
          <h1 className="text-xl font-bold text-white sm:text-2xl">My Devices &amp; Files</h1>
          <p className="mt-0.5 text-xs text-slate-400">Your assigned companion devices, controls, and shared family files.</p>
        </header>

        <Panel
          title="My devices"
          icon={Cpu}
          aside={<span className="text-xs text-slate-500">{myDevices.data?.length ?? 0} device(s)</span>}
        >
          {myDevices.isLoading && <p className="p-4 text-sm text-slate-500">Loading your devices…</p>}
          {myDevices.data && myDevices.data.length === 0 && (
            <p className="p-4 text-sm text-slate-500" data-testid="my-devices-empty">
              No devices currently assigned to you. An administrator can assign an ESPHome watch, speaker, or smart display.
            </p>
          )}
          <ul className="divide-y divide-white/5">
            {myDevices.data?.map((d) => (
              <DeviceCard
                key={d.device_key}
                device={{
                  ...d,
                  platform: isEsphomeDevice(d) ? 'esphome' : 'android',
                  owner_username: d.owner_username || user?.username,
                  owner_display_name: user?.display_name || user?.username,
                } as unknown as MdmDevice}
                isAdmin={false}
                onRefresh={() => queryClient.invalidateQueries({ queryKey: ['my-companion-devices'] })}
                onOpenDetails={(dev) => setDetailDevice(dev)}
              />
            ))}
          </ul>
        </Panel>

        <SharedFilesPanel />

        {detailDevice && (
          <DeviceDetailSheet
            device={{
              ...(detailDevice as unknown as CompanionDevice),
              registered_by: detailDevice.registered_by || (isEsphomeDevice(detailDevice) ? 'paired' : 'self'),
            }}
            onClose={() => setDetailDevice(null)}
          />
        )}
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
          Phones, tablets, ESPHome watches &amp; companion nodes, shared School and Library folders, and remote actions.
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
            No devices have registered yet. A phone or tablet registers when a member signs in to the Jarvis app; ESPHome watches and assistants are added by pairing.
          </p>
        )}
        <ul className="divide-y divide-white/5">
          {inventory.data?.devices.map((d) => (
            <DeviceCard
              key={d.device_key}
              device={d}
              members={members}
              isAdmin={true}
              onRefresh={() => queryClient.invalidateQueries({ queryKey: ['mdm-devices'] })}
              onOpenDetails={(dev) => setDetailDevice(dev)}
            />
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

      <AuditLogPanel />

      {detailDevice && (
        <DeviceDetailSheet
          device={{
            ...(detailDevice as unknown as CompanionDevice),
            registered_by: detailDevice.registered_by || (isEsphomeDevice(detailDevice) ? 'paired' : 'self'),
          }}
          onClose={() => setDetailDevice(null)}
        />
      )}
    </div>
  );
}
