import { useState, useEffect } from 'react';
import { useAuth } from '../context/AuthContext';
import { useHaptics } from '../hooks/useHaptics';
import { useDarkModeSync } from '../hooks/useDarkModeSync';
import { useLocation } from '../context/LocationContext';
import { Capacitor } from '@capacitor/core';
import { User, Shield, Bell, Moon, LogOut, ChevronRight, SlidersHorizontal, Lock, X, Smartphone, Download, RefreshCw, MapPin, Footprints, AlertCircle, ExternalLink, Loader2, Package, PackageCheck, PhoneIncoming, Settings as SettingsIcon } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import LocationPanel from '../components/location/LocationPanel';
import ActivitySharingPanel from '../components/settings/ActivitySharingPanel';
import FamilyPanel from '../components/settings/FamilyPanel';
import AppCleanupPanel from '../components/settings/AppCleanupPanel';
import MobileMenuPanel from '../components/settings/MobileMenuPanel';
import NotificationPreferencesPanel from '../components/settings/NotificationPreferencesPanel';
import PushAppsPanel from '../components/settings/PushAppsPanel';
import SiteThemePanel from '../components/settings/SiteThemePanel';
import TelemetryReportsPanel from '../components/settings/TelemetryReportsPanel';
import WatchPanel from '../components/settings/WatchPanel';
import Toggle from '../components/ui/Toggle';
import UserAvatar from '../components/UserAvatar';
import { getAutoDropIn, setAutoDropIn } from '../lib/callSettings';
import { isAdminPinSet, setAdminPin, clearAdminPin, verifyAdminPin } from '../lib/adminPin';
import { disablePush, enablePush, permissionState, pushSupported } from '../lib/push';
import {
  checkForAppUpdates,
  checkApkUpdate,
  downloadAndInstallApk,
  downloadApkWithProgress,
  getApkInstallPermission,
  getRunningVersion,
  hasVerifiedInstallFlow,
  installVerifiedApk,
  openApkInstallSettings,
} from '../lib/appUpdater';
import type { ApkDownloadProgress, ApkInstallPermission, ApkUpdateStatus } from '../lib/appUpdater';
import toast from 'react-hot-toast';

const Settings = () => {
  const { user, logout } = useAuth();
  const { trigger } = useHaptics();
  const { theme, setThemeMode } = useDarkModeSync();
  const navigate = useNavigate();
  const native = Capacitor.isNativePlatform();

  const cycleTheme = () => {
    trigger('light');
    const cycle: Record<string, 'light' | 'dark' | 'system'> = {
      dark: 'system',
      system: 'light',
      light: 'dark',
    };
    setThemeMode(cycle[theme]);
  };

  const themeLabel = theme === 'system' ? 'System' : theme === 'dark' ? 'Dark' : 'Light';

  return (
    <div className="space-y-6 max-w-2xl mx-auto">
      <h1 className="text-2xl font-bold text-white">Settings</h1>

      <div className="glass-panel rounded-2xl p-4">
        <div className="flex items-center gap-4 p-3 rounded-xl bg-white/5">
          <div className="w-12 h-12 rounded-full bg-gradient-to-br from-purple-500 to-pink-500 flex items-center justify-center text-white font-bold shrink-0">
            {user?.username?.[0].toUpperCase() || 'G'}
          </div>
          <div className="min-w-0">
            <p className="text-white font-medium">{user?.username || 'Guest'}</p>
            <p className="text-xs text-slate-400">{user?.is_admin ? 'Admin' : 'Family Member'}</p>
          </div>
        </div>
      </div>

      <div className="glass-panel rounded-2xl p-4 space-y-1">
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider px-3 pt-1 mb-2">Preferences</h2>

        <button
          onClick={cycleTheme}
          className="w-full flex items-center justify-between p-3 rounded-xl bg-white/5 hover:bg-white/10 transition-colors"
        >
          <div className="flex items-center gap-3">
            <span className="text-slate-400"><Moon size={18} /></span>
            <div className="text-left">
              <p className="text-white text-sm font-medium">Theme</p>
              <p className="text-xs text-slate-400">{themeLabel} mode (tap to cycle)</p>
            </div>
          </div>
          <span className="text-xs text-purple-400 font-medium">{themeLabel}</span>
        </button>

        <NotificationsRow />
      </div>

      <section className="space-y-4" aria-labelledby="settings-notifications">
        <h2 id="settings-notifications" className="px-1 text-sm font-semibold text-slate-400 uppercase tracking-wider">Notifications</h2>
        <NotificationPreferencesPanel />
        {native && <PushAppsPanel />}
      </section>

      <section className="space-y-4" aria-labelledby="settings-appearance">
        <h2 id="settings-appearance" className="px-1 text-sm font-semibold text-slate-400 uppercase tracking-wider">Appearance</h2>
        <SiteThemePanel />
        <MobileMenuPanel />
      </section>

      <section className="space-y-4" aria-labelledby="settings-family">
        <h2 id="settings-family" className="px-1 text-sm font-semibold text-slate-400 uppercase tracking-wider">Family</h2>
        <FamilyPanel />
        <ActivitySharingPanel />
        <FamilyDropInSection />
        <TelemetryReportsPanel />
      </section>

      <section className="space-y-4" aria-labelledby="settings-device">
        <h2 id="settings-device" className="px-1 text-sm font-semibold text-slate-400 uppercase tracking-wider">This device</h2>
        <SensorsSection />
        <AppUpdatesSection />
        <AppCleanupPanel />
      </section>

      <section className="space-y-4" aria-labelledby="settings-watch">
        <h2 id="settings-watch" className="px-1 text-sm font-semibold text-slate-400 uppercase tracking-wider">Jarvis Watch</h2>
        <WatchPanel />
      </section>

      <div className="glass-panel rounded-2xl p-4 space-y-1">
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider px-3 pt-1 mb-2">Location & Presence</h2>

        <LocationPanel />
      </div>

      {user?.is_admin && (
        <div className="glass-panel rounded-2xl p-4 space-y-1">
          <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider px-3 pt-1 mb-2">Admin</h2>

          <button
            onClick={() => { trigger('light'); navigate('/admin'); }}
            className="w-full flex items-center justify-between p-3 rounded-xl bg-white/5 hover:bg-white/10 transition-colors"
          >
            <div className="flex items-center gap-3">
              <Shield size={18} className="text-slate-400" />
              <div className="text-left">
                <p className="text-white text-sm font-medium">System Ops & Raven</p>
                <p className="text-xs text-slate-400">Manage services and autonomous agents</p>
              </div>
            </div>
            <ChevronRight size={16} className="text-slate-500" />
          </button>

          <button
            onClick={() => { trigger('light'); navigate('/admin/integrations'); }}
            className="w-full flex items-center justify-between p-3 rounded-xl bg-white/5 hover:bg-white/10 transition-colors"
          >
            <div className="flex items-center gap-3">
              <SlidersHorizontal size={18} className="text-slate-400" />
              <div className="text-left">
                <p className="text-white text-sm font-medium">System Configuration</p>
                <p className="text-xs text-slate-400">Models, service URLs and shared integrations</p>
              </div>
            </div>
            <ChevronRight size={16} className="text-slate-500" />
          </button>

          {/* The PIN gates admin pages only in the Android app (AdminElevation),
              so offering it in a browser would promise protection it does not give. */}
          {native && <AdminPinManager trigger={trigger} />}
        </div>
      )}

      <div className="glass-panel rounded-2xl p-4 space-y-1">
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider px-3 pt-1 mb-2">Account</h2>

        <button
          onClick={() => { trigger('light'); navigate('/identity'); }}
          className="w-full flex items-center justify-between p-3 rounded-xl bg-white/5 hover:bg-white/10 transition-colors"
        >
          <div className="flex min-w-0 items-center gap-3">
            {/* The person's own picture, so a new one shows here as soon as it is saved. */}
            {user ? (
              <UserAvatar username={user.username} avatar={user.avatar} name={user.full_name || user.username} size={32} />
            ) : (
              <User size={18} className="text-slate-400" />
            )}
            <div className="min-w-0 text-left">
              <p className="text-white text-sm font-medium">Accounts & Integrations</p>
              <p className="text-xs text-slate-400">Profile, Nextcloud / Skylight / GitHub / CalDAV, voice, devices, API keys</p>
            </div>
          </div>
          <ChevronRight size={16} className="text-slate-500" />
        </button>

        <button
          onClick={() => { trigger('medium'); logout(); }}
          className="w-full flex items-center gap-3 p-3 rounded-xl bg-red-500/10 hover:bg-red-500/20 transition-colors text-left mt-2"
        >
          <LogOut size={18} className="text-red-400" />
          <p className="text-red-400 text-sm font-medium">Sign Out</p>
        </button>
      </div>
    </div>
  );
};

const AdminPinManager = ({
  trigger,
  onChanged,
}: {
  trigger: ReturnType<typeof useHaptics>['trigger'];
  onChanged?: () => void;
}) => {
  const [open, setOpen] = useState(false);
  const [current, setCurrent] = useState('');
  const [pin, setPin] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [configured, setConfigured] = useState(isAdminPinSet());

  const reset = () => {
    setCurrent('');
    setPin('');
    setConfirm('');
    setError('');
  };

  const close = () => {
    setOpen(false);
    reset();
  };

  // Settings is not behind AdminElevation, so without this anyone holding an
  // unlocked phone could replace or clear the PIN and walk into /admin.
  const currentPinOk = async () => {
    if (!configured) return true;
    if (await verifyAdminPin(current)) return true;
    setError('Current PIN is incorrect');
    trigger('error');
    return false;
  };

  const save = async () => {
    if (!(await currentPinOk())) return;
    if (pin.length < 4) {
      setError('PIN must be at least 4 digits');
      return;
    }
    if (pin !== confirm) {
      setError('PINs do not match');
      return;
    }
    setSaving(true);
    try {
      await setAdminPin(pin);
      setConfigured(true);
      trigger('success');
      toast.success('Admin PIN updated');
      onChanged?.();
      close();
    } catch {
      setError('Could not save PIN');
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    if (!(await currentPinOk())) return;
    clearAdminPin();
    setConfigured(false);
    trigger('light');
    toast.success('Admin PIN removed');
    onChanged?.();
    close();
  };

  return (
    <>
      <button
        onClick={() => { trigger('light'); setOpen(true); }}
        className="w-full flex items-center justify-between p-3 rounded-xl bg-white/5 hover:bg-white/10 transition-colors"
      >
        <div className="flex items-center gap-3">
          <Lock size={18} className="text-slate-400" />
          <div className="text-left">
            <p className="text-white text-sm font-medium">Admin PIN</p>
            <p className="text-xs text-slate-400">
              {configured ? 'Required to unlock admin features on this device' : 'Not set. Set one to protect admin access'}
            </p>
          </div>
        </div>
        <span className="text-xs text-purple-400 font-medium">{configured ? 'Change' : 'Set'}</span>
      </button>

      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-xl">
          <div className="glass-panel w-full max-w-sm mx-4 p-6 rounded-2xl relative">
            <button onClick={close} aria-label="Close" className="absolute top-2 right-2 w-11 h-11 flex items-center justify-center text-slate-400 hover:text-white">
              <X size={20} />
            </button>

            <h2 className="text-xl font-bold text-white text-center mb-1">
              {configured ? 'Change Admin PIN' : 'Set Admin PIN'}
            </h2>
            <p className="text-sm text-slate-400 text-center mb-6">Use at least 4 digits</p>

            {configured && (
              <input
                type="password"
                inputMode="numeric"
                autoComplete="current-password"
                placeholder="Current PIN"
                aria-label="Current PIN"
                value={current}
                onChange={(e) => setCurrent(e.target.value.replace(/\D/g, ''))}
                className="w-full mb-3 px-4 py-3 rounded-xl bg-white/5 border border-white/10 text-white text-center tracking-[0.5em] text-lg outline-none focus:border-purple-500/50"
              />
            )}
            <input
              type="password"
              inputMode="numeric"
              autoComplete="new-password"
              placeholder="New PIN"
              aria-label="New PIN"
              value={pin}
              onChange={(e) => setPin(e.target.value.replace(/\D/g, ''))}
              className="w-full mb-3 px-4 py-3 rounded-xl bg-white/5 border border-white/10 text-white text-center tracking-[0.5em] text-lg outline-none focus:border-purple-500/50"
            />
            <input
              type="password"
              inputMode="numeric"
              autoComplete="new-password"
              placeholder="Confirm PIN"
              aria-label="Confirm PIN"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value.replace(/\D/g, ''))}
              className="w-full mb-3 px-4 py-3 rounded-xl bg-white/5 border border-white/10 text-white text-center tracking-[0.5em] text-lg outline-none focus:border-purple-500/50"
            />

            {error && <p className="text-sm text-red-400 text-center mb-3">{error}</p>}

            <button
              onClick={save}
              disabled={saving}
              className="w-full py-3 rounded-xl bg-purple-500/30 border border-purple-500/30 text-white font-medium hover:bg-purple-500/40 transition-colors disabled:opacity-50"
            >
              {saving ? 'Saving…' : 'Save PIN'}
            </button>

            {configured && (
              <button
                onClick={remove}
                className="w-full mt-2 py-3 rounded-xl bg-red-500/10 border border-red-500/20 text-red-400 font-medium hover:bg-red-500/20 transition-colors"
              >
                Remove PIN
              </button>
            )}
          </div>
        </div>
      )}
    </>
  );
};

/**
 * Notifications are real on both surfaces but delivered differently, so the
 * row says how and offers the one switch that exists for that surface:
 * - Android app: PushService (started at sign-in and boot) holds the push
 *   stream and posts every notice on a channel per kind; the Notifications
 *   section below and Android's channel settings are the off switches.
 * - Browser: Web Push via /push-sw.js and the telemetry service's VAPID key,
 *   carrying the same notices. Everything also lands under the header bell.
 */
const NotificationsRow = () => {
  const native = Capacitor.isNativePlatform();
  const supported = !native && pushSupported();
  const [state, setState] = useState<string>(() => (supported ? permissionState() : 'unsupported'));
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!supported) return;
    let active = true;
    navigator.serviceWorker
      .getRegistration('/push-sw.js')
      .then(async (registration) => {
        const subscription = await registration?.pushManager.getSubscription();
        if (active) setState(subscription ? 'subscribed' : permissionState());
      })
      .catch(() => {
        if (active) setState(permissionState());
      });
    return () => {
      active = false;
    };
  }, [supported]);

  const toggle = async (next: boolean) => {
    setBusy(true);
    try {
      if (!next) {
        await disablePush();
        setState(permissionState());
        toast.success('Browser notifications off');
        return;
      }
      const result = await enablePush();
      if (result.ok) {
        setState('subscribed');
        toast.success('Browser notifications on');
      } else {
        setState(result.reason ?? 'denied');
        toast.error(result.reason === 'denied'
          ? 'Notifications are blocked for this site in the browser settings'
          : 'Could not turn on browser notifications');
      }
    } catch {
      toast.error('Could not change browser notifications');
    } finally {
      setBusy(false);
    }
  };

  const description = native
    ? 'Notices arrive as Android notifications. Choose which kinds below, or tune each one in Android Settings › Apps › Jarvis › Notifications.'
    : !supported
      ? 'This browser cannot receive push notifications. Alerts still appear under the bell in the header.'
      : state === 'denied'
        ? 'Blocked in this browser. Allow notifications for this site, then turn this on.'
        : 'Family chat, arrivals and reports as browser notifications on this device. Alerts also appear under the bell.';

  return (
    <div className="w-full flex items-center justify-between gap-3 p-3 rounded-xl bg-white/5" data-testid="notifications-row">
      <div className="flex items-center gap-3 min-w-0 text-left">
        <span className="text-slate-400 shrink-0"><Bell size={18} /></span>
        <div className="min-w-0">
          <p className="text-white text-sm font-medium">Notifications</p>
          <p className="text-xs text-slate-400">{description}</p>
        </div>
      </div>
      {supported && (
        <Toggle
          checked={state === 'subscribed'}
          disabled={busy || state === 'denied'}
          onChange={(next) => void toggle(next)}
          ariaLabel="Browser notifications"
        />
      )}
    </div>
  );
};

/**
 * Opt-in auto drop-in. This is a per-device choice: only the device that
 * enables it opens its microphone and speaker when a family call starts.
 */
const FamilyDropInSection = () => {
  const [enabled, setEnabled] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let active = true;
    void getAutoDropIn().then((value) => {
      if (!active) return;
      setEnabled(value);
      setLoaded(true);
    });
    return () => {
      active = false;
    };
  }, []);

  const change = async (next: boolean) => {
    setEnabled(next);
    try {
      await setAutoDropIn(next);
      toast.success(next ? 'Auto drop-in is on for this device.' : 'Auto drop-in is off for this device.');
    } catch {
      setEnabled(!next);
      toast.error('Could not save the drop-in setting.');
    }
  };

  return (
    <div className="glass-panel rounded-2xl p-4 space-y-2">
      <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider px-3 pt-1 mb-2 flex items-center gap-2">
        <PhoneIncoming size={16} className="text-emerald-400" />
        Family drop-in
      </h2>
      <div className="flex items-center justify-between gap-3 p-3 rounded-xl bg-white/5">
        <div className="min-w-0">
          <p className="text-white text-sm font-medium">Auto drop-in on this device</p>
          <p className="text-xs text-slate-400 mt-0.5">
            When a family call starts, this device joins with its microphone and speaker after a
            short beep. Off by default; calls still appear in the drop-in banner.
          </p>
        </div>
        <Toggle
          checked={enabled}
          disabled={!loaded}
          onChange={(next) => void change(next)}
          ariaLabel="Auto drop-in on this device"
          testId="auto-drop-in"
        />
      </div>
      <p className="px-1 text-[11px] leading-relaxed text-slate-500">
        This is a per-device setting. The first time, the browser or phone may ask for microphone
        permission. If auto-join is blocked, use the Drop in banner instead.
      </p>
    </div>
  );
};

/** Always-on info-gathering sensors. Each can be turned off; related services stop with it. */
const SensorsSection = () => {
  const { sensors, enableSensor, disableSensor, openSensorSettings } = useLocation();
  const { trigger } = useHaptics();

  const handleSensorToggle = async (id: 'location' | 'steps', next: boolean) => {
    trigger('light');
    if (next) {
      const ok = await enableSensor(id);
      if (ok) toast.success(id === 'steps' ? 'Step counter enabled' : 'Location tracking enabled');
    } else {
      await disableSensor(id);
      toast.success(id === 'steps' ? 'Step counter off' : 'Location tracking off');
    }
  };

  const rows: Array<{
    id: 'location' | 'steps';
    icon: React.ReactNode;
    label: string;
    onDesc: string;
    offDesc: string;
  }> = [
    {
      id: 'location',
      icon: <MapPin size={18} />,
      label: 'Location tracking',
      onDesc: 'GPS breadcrumbs, trips, and presence',
      offDesc: 'Off: no location is recorded',
    },
    {
      id: 'steps',
      icon: <Footprints size={18} />,
      label: 'Step counter',
      onDesc: 'Hardware pedometer syncs to Wander',
      offDesc: 'Off: pedometer not read or synced',
    },
  ];

  return (
    <div className="glass-panel rounded-2xl p-4 space-y-1">
      <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider px-3 pt-1 mb-2">Sensors & Privacy</h2>
      <p className="px-3 text-[11px] text-slate-500 mb-2">
        Turn off any sensor to stop its related services. Re-enable to prompt for permission again.
      </p>
      {rows.map((row) => {
        const s = sensors[row.id];
        return (
          <div key={row.id} className="p-3 rounded-xl bg-white/5 space-y-2">
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-3 min-w-0">
                <span className="text-slate-400 shrink-0">{row.icon}</span>
                <div className="min-w-0">
                  <p className="text-white text-sm font-medium">{row.label}</p>
                  <p className="text-xs text-slate-400">{s.enabled ? row.onDesc : row.offDesc}</p>
                </div>
              </div>
              <Toggle
                checked={s.enabled}
                onChange={(next) => void handleSensorToggle(row.id, next)}
                ariaLabel={row.label}
              />
            </div>
            {s.message && (
              <div className="flex items-start gap-2 p-2 rounded-lg bg-amber-500/10 border border-amber-500/20 text-xs text-amber-300">
                <AlertCircle size={14} className="shrink-0 mt-0.5" />
                <div className="flex-1 min-w-0">
                  <span>{s.message}</span>
                  {s.permission === 'denied' && (
                    <button
                      type="button"
                      onClick={() => {
                        trigger('light');
                        void openSensorSettings(row.id);
                      }}
                      className="ml-2 inline-flex items-center gap-1 underline hover:text-amber-200"
                    >
                      Open settings <ExternalLink size={11} />
                    </button>
                  )}
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
};

const formatBytes = (bytes?: number) => {
  if (!bytes || bytes <= 0) return null;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

const AppUpdatesSection = () => {
  const { trigger } = useHaptics();
  const [checking, setChecking] = useState(false);
  const [lastChecked, setLastChecked] = useState<Date | null>(null);
  const [apk, setApk] = useState<ApkUpdateStatus | null>(null);
  const [perm, setPerm] = useState<ApkInstallPermission | null>(null);
  // Download/install state machine. `ready` means the APK is in the cache and
  // its checksum matched what the server published.
  const [apkPhase, setApkPhase] = useState<'idle' | 'downloading' | 'ready' | 'installing'>('idle');
  const [progress, setProgress] = useState<ApkDownloadProgress | null>(null);
  const [apkError, setApkError] = useState<string | null>(null);
  // null until known, so nothing is offered while the check is still running.
  const [canVerify, setCanVerify] = useState<boolean | null>(null);
  const native = Capacitor.isNativePlatform();
  const [updateInfo, setUpdateInfo] = useState<{
    version: string;
    gitSha: string;
    remoteSha?: string;
    remoteVersion?: string;
    releaseNotes?: string;
    hasUpdate?: boolean;
  }>({ version: '', gitSha: '…' });

  useEffect(() => {
    let active = true;
    void getRunningVersion().then((v) => {
      if (active) setUpdateInfo((prev) => ({ ...prev, version: v.version, gitSha: v.gitSha }));
    });
    return () => { active = false; };
  }, []);

  // Notice the pending APK on arrival rather than only after "Check Now".
  useEffect(() => {
    let active = true;
    void checkApkUpdate().then((status) => {
      if (active) setApk(status);
    });
    return () => { active = false; };
  }, []);

  const handleCheck = async () => {
    trigger('light');
    setChecking(true);
    try {
      const res = await checkForAppUpdates({ silent: false });
      setUpdateInfo((prev) => ({
        ...prev,
        remoteSha: res.remoteGitSha,
        remoteVersion: res.remoteVersion,
        releaseNotes: res.releaseNotes,
        hasUpdate: res.hasUpdate,
      }));
      // Re-probe for the full APK detail (version code, size) the combined
      // check does not return, so the notice reflects this press.
      setApk(await checkApkUpdate());
      setLastChecked(new Date());
    } finally {
      setChecking(false);
    }
  };

  const apkPending = Boolean(apk?.updateAvailable && apk.apkUrl);
  const apkSize = formatBytes(apk?.sizeBytes);

  // Why the in-app flow is or is not on offer, so the notice can say which of
  // the three very different situations it is in. The bundle arrives over OTA,
  // so a brand new UI can land on a device still running the APK built before
  // the native downloader existed -- and that used to render an amber banner
  // with no button at all, which is indistinguishable from a broken app.
  const apkFlow: 'checking' | 'ready' | 'no-plugin' | 'no-digest' = !native
    ? 'checking'
    : perm === null || canVerify === null
      ? 'checking'
      : !perm.known
        ? 'no-plugin'
        : canVerify
          ? 'ready'
          : 'no-digest';

  // Android only grants "Install unknown apps" from system Settings, so the
  // check has to happen before the user commits to installing -- otherwise they
  // tap Install and get bounced into Settings mid-flow, which is what made
  // this feel like a broken multi-step dance. Re-checked on return from
  // Settings so the button flips without a manual refresh.
  useEffect(() => {
    if (!apkPending) return;
    let active = true;
    const probe = () => {
      void getApkInstallPermission().then((p) => {
        if (active) setPerm(p);
      });
    };
    probe();
    const onVisible = () => {
      if (document.visibilityState === 'visible') probe();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      active = false;
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [apkPending]);

  // The web bundle arrives over OTA, so this UI can be newer than the APK it
  // is running on. Only offer the verified download when this build has it AND
  // the server published a digest to check against.
  useEffect(() => {
    if (!apkPending) return;
    let active = true;
    void hasVerifiedInstallFlow().then((ok) => {
      if (active) setCanVerify(ok && Boolean(apk?.apkSha256));
    });
    return () => {
      active = false;
    };
  }, [apkPending, apk?.apkSha256]);

  // A pending APK outranks "Up to Date": claiming the app is current while an
  // install is waiting is the contradiction this notice exists to remove.
  const updateState = apkPending
    ? { label: 'App Update Ready', cls: 'text-amber-300 border-amber-500/40 bg-amber-500/10' }
    : updateInfo.hasUpdate
      ? { label: 'Update Available', cls: 'text-amber-300 border-amber-500/40 bg-amber-500/10' }
      : updateInfo.remoteSha === 'unknown'
        // checkForAppUpdates reports a failed check as remote sha 'unknown';
        // that is not "Up to Date".
        ? { label: 'Check Failed', cls: 'text-red-300 border-red-500/40 bg-red-500/10' }
      : updateInfo.remoteSha
        ? { label: 'Up to Date', cls: 'text-emerald-300 border-emerald-500/40 bg-emerald-500/10' }
        : { label: 'Not Checked', cls: 'text-slate-400 border-white/10 bg-white/5' };

  return (
    <div className="glass-panel rounded-2xl p-4 space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 px-3 pt-1">
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider flex items-center gap-2">
          <Smartphone size={16} className="text-purple-400" />
          App &amp; OTA Updates
        </h2>
        <span className="min-w-0 break-all text-[10px] text-slate-500 font-mono">
          {updateInfo.version ? `v${updateInfo.version} ` : ''}({updateInfo.gitSha})
        </span>
      </div>

      <div className="p-3.5 rounded-xl bg-white/5 space-y-2.5">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-sm font-medium text-white">Live Over-The-Air Updates</p>
              <span className={`px-2 py-0.5 rounded-full border text-[9px] font-bold uppercase tracking-wider ${updateState.cls}`}>
                {updateState.label}
              </span>
            </div>
            <p className="text-xs text-slate-400 mt-0.5">
              Web UI updates install silently; background checks run on launch and every 6 hours
            </p>
            {updateInfo.remoteSha && updateInfo.remoteSha !== 'unknown' && (
              <p className="text-[10px] text-slate-500 font-mono mt-1 break-all">
                Server: {updateInfo.remoteVersion ? `${updateInfo.remoteVersion} ` : ''}({updateInfo.remoteSha})
                {lastChecked && ` · checked ${lastChecked.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`}
              </p>
            )}
            {updateInfo.releaseNotes && updateInfo.hasUpdate && (
              <p className="text-[10px] text-slate-500 italic mt-0.5 truncate">{updateInfo.releaseNotes}</p>
            )}
          </div>
          <button
            onClick={handleCheck}
            disabled={checking}
            className="glass-button shrink-0 px-3 py-1.5 rounded-xl text-xs font-semibold text-purple-300 hover:text-white flex items-center gap-1.5 border-purple-500/30 pointer-coarse:min-h-11 self-start sm:self-auto"
          >
            <RefreshCw size={13} className={checking ? 'animate-spin text-purple-400' : ''} />
            <span>{checking ? 'Checking...' : 'Check Now'}</span>
          </button>
        </div>
      </div>

      {apkPending && apk && (
        <div
          data-testid="apk-update-notice"
          role="status"
          className="p-3.5 rounded-xl border border-amber-500/40 bg-amber-500/10 space-y-2.5"
        >
          <div className="flex items-start gap-2.5">
            <Package size={16} className="text-amber-300 shrink-0 mt-0.5" />
            <div className="min-w-0">
              <p className="text-sm font-semibold text-amber-200">New app version available</p>
              <p className="text-xs text-amber-100/80 mt-0.5">
                Install build {apk.apkVersionCode} to get new native features and permissions.
                {apk.nativeBuildNumber !== undefined && ` You are on build ${apk.nativeBuildNumber}.`}
                {apkSize && ` ${apkSize} download.`}
              </p>
              {perm?.known && !perm.allowed && (
                <p className="text-[11px] text-amber-200/70 mt-1">
                  One-time setup: Android needs permission to install updates. Allow it once,
                  then come back and tap Install.
                </p>
              )}
            </div>
          </div>
          {/* In the app it all happens here: Download (with progress, checksum
              verified) -> Install -> Android's installer, which restarts the
              app. Install permission is asked for only at Install, the one
              step that needs it. A browser (an Android phone's or a
              Chromebook's) cannot install anything itself, so it gets the
              file to open. */}
          <div className="flex flex-wrap items-center gap-2">
            {!native ? (
              <a
                href={apk.apkUrl!}
                download="jarvis-os.apk"
                target="_blank"
                rel="noreferrer"
                onClick={() => trigger('light')}
                className="px-3 py-1.5 rounded-xl text-amber-200/90 hover:text-white border border-amber-500/30 text-xs font-semibold flex items-center gap-1.5 pointer-coarse:min-h-11"
              >
                <ExternalLink size={13} />
                <span>Download link</span>
              </a>
            ) : apkFlow === 'checking' && apkPhase === 'idle' ? (
              <p
                data-testid="apk-install-checking"
                role="status"
                aria-live="polite"
                className="text-[11px] text-amber-200/80"
              >
                Checking what this build can do…
              </p>
            ) : apkPhase === 'downloading' ? (
              <div
                data-testid="apk-download-progress"
                className="w-full space-y-1.5"
                role="status"
                aria-live="polite"
              >
                <div className="flex items-center justify-between text-[11px] font-semibold text-amber-100">
                  <span>Downloading update…</span>
                  <span>{progress?.percent ?? 0}%</span>
                </div>
                <div className="h-1.5 w-full rounded-full bg-amber-500/20 overflow-hidden">
                  <div
                    data-testid="apk-download-bar"
                    className="h-full rounded-full bg-amber-400 transition-[width] duration-200"
                    style={{ width: `${Math.max(2, progress?.percent ?? 0)}%` }}
                  />
                </div>
                {progress?.total ? (
                  <p className="text-[10px] text-amber-200/70 font-mono">
                    {(progress.received / 1048576).toFixed(1)} / {(progress.total / 1048576).toFixed(1)} MB
                  </p>
                ) : null}
              </div>
            ) : (apkPhase === 'ready' || apkPhase === 'installing') && perm?.known && !perm.allowed ? (
              <button
                onClick={() => {
                  trigger('medium');
                  void openApkInstallSettings();
                }}
                className="px-3 py-1.5 rounded-xl bg-amber-500/25 text-amber-100 hover:bg-amber-500/35 border border-amber-500/50 text-xs font-bold flex items-center gap-1.5 pointer-coarse:min-h-11"
              >
                <SettingsIcon size={13} />
                <span>Allow updates</span>
              </button>
            ) : apkPhase === 'ready' || apkPhase === 'installing' ? (
              <button
                data-testid="apk-install-button"
                onClick={() => {
                  trigger('medium');
                  setApkError(null);
                  setApkPhase('installing');
                  void installVerifiedApk()
                    .then(() => toast.success('Installer opened. Tap Install.', { id: 'apk-install' }))
                    .catch((err) => {
                      setApkPhase('ready');
                      setApkError(err instanceof Error ? err.message : String(err));
                    });
                }}
                className="px-3 py-1.5 rounded-xl bg-emerald-500/25 text-emerald-100 hover:bg-emerald-500/35 border border-emerald-500/50 text-xs font-bold flex items-center gap-1.5 pointer-coarse:min-h-11"
              >
                {apkPhase === 'installing' ? <Loader2 size={13} className="animate-spin" /> : <PackageCheck size={13} />}
                <span>{apkPhase === 'installing' ? 'Opening installer…' : 'Install'}</span>
              </button>
            ) : canVerify ? (
              <button
                data-testid="apk-download-button"
                onClick={() => {
                  trigger('light');
                  setApkError(null);
                  setApkPhase('downloading');
                  setProgress(null);
                  void downloadApkWithProgress(apk.apkUrl!, apk.apkSha256!, (p) => setProgress(p))
                    .then(() => {
                      trigger('medium');
                      setApkPhase('ready');
                    })
                    .catch((err) => {
                      setApkPhase('idle');
                      setApkError(err instanceof Error ? err.message : String(err));
                    });
                }}
                className="px-3 py-1.5 rounded-xl bg-amber-500/25 text-amber-100 hover:bg-amber-500/35 border border-amber-500/50 text-xs font-bold flex items-center gap-1.5 pointer-coarse:min-h-11"
              >
                <Download size={13} />
                <span>Download</span>
              </button>
            ) : canVerify === false && perm?.known && !perm.allowed ? (
              <button
                onClick={() => {
                  trigger('medium');
                  void openApkInstallSettings();
                }}
                className="px-3 py-1.5 rounded-xl bg-amber-500/25 text-amber-100 hover:bg-amber-500/35 border border-amber-500/50 text-xs font-bold flex items-center gap-1.5 pointer-coarse:min-h-11"
              >
                <SettingsIcon size={13} />
                <span>Allow updates</span>
              </button>
            ) : canVerify === false && perm?.known ? (
              // An APK older than the verified flow, or no digest published:
              // the unverified in-app install, never a browser.
              <button
                onClick={() => {
                  trigger('medium');
                  void downloadAndInstallApk(apk.apkUrl!);
                }}
                className="px-3 py-1.5 rounded-xl bg-amber-500/25 text-amber-100 hover:bg-amber-500/35 border border-amber-500/50 text-xs font-bold flex items-center gap-1.5 pointer-coarse:min-h-11"
              >
                <Download size={13} />
                <span>Install update</span>
              </button>
            ) : apkFlow === 'no-plugin' ? (
              // The amber banner with no button is what made this look broken.
              // This build's native side predates the in-app downloader, so say
              // so plainly and give the one step that fixes it for good.
              <p
                data-testid="apk-install-unavailable"
                className="w-full space-y-1.5 text-[11px] leading-snug text-amber-100/90"
              >
                <span className="block font-semibold">
                  This version of the app cannot install updates from inside itself.
                </span>
                <span className="block text-amber-200/70">
                  Open{' '}
                  <a
                    href={apk.apkUrl!}
                    target="_blank"
                    rel="noreferrer"
                    className="underline underline-offset-2"
                    onClick={() => trigger('light')}
                  >
                    this link
                  </a>{' '}
                  once to install the current app. After that, updates download and install on their own.
                </span>
              </p>
            ) : null}
          </div>
          {apkError && (
            <p data-testid="apk-download-error" role="alert" className="text-[11px] text-red-300">
              {apkError}
            </p>
          )}
          {apkFlow === 'no-digest' && (
            <p data-testid="apk-install-nodigest" className="text-[11px] text-amber-200/70">
              The server published no checksum for this build, so the download cannot be verified
              before it is installed.
            </p>
          )}
          {apkPhase === 'ready' && (
            <p className="text-[11px] text-emerald-200/80">
              Downloaded and checksum verified. The app restarts after installing.
            </p>
          )}
        </div>
      )}

      {apk?.indeterminate && (
        <div
          data-testid="apk-update-unknown"
          role="status"
          className="p-3.5 rounded-xl border border-white/10 bg-white/5 flex items-start gap-2.5"
        >
          <AlertCircle size={15} className="text-slate-400 shrink-0 mt-0.5" />
          <p className="text-xs text-slate-400">
            An APK build is published but the server did not report its version code, so we
            cannot tell whether it is newer than yours.
          </p>
        </div>
      )}

      {apk?.error && (
        <div
          data-testid="apk-update-error"
          role="status"
          className="p-3.5 rounded-xl border border-white/10 bg-white/5 flex items-start gap-2.5"
        >
          <AlertCircle size={15} className="text-slate-500 shrink-0 mt-0.5" />
          <p className="text-xs text-slate-500">
            Could not check for app updates: {apk.error}
          </p>
        </div>
      )}
    </div>
  );
};

export default Settings;
