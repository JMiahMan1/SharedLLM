import { useState, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useAuth } from '../context/AuthContext';
import { useHaptics } from '../hooks/useHaptics';
import { useDarkModeSync } from '../hooks/useDarkModeSync';
import { useLocation } from '../context/LocationContext';
import { User, Shield, Bell, Moon, Key, LogOut, ChevronRight, SlidersHorizontal, Lock, X, Smartphone, Download, RefreshCw, MapPin, Footprints, AlertCircle, ExternalLink } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { api } from '../services/api';
import type { GlobalSetting } from '../services/api';
import LocationPanel from '../components/location/LocationPanel';
import Toggle from '../components/ui/Toggle';
import { isAdminPinSet, setAdminPin, clearAdminPin } from '../lib/adminPin';
import { checkForAppUpdates, downloadAndInstallApk, getRunningVersion } from '../lib/appUpdater';
import toast from 'react-hot-toast';

const Settings = () => {
  const { user, logout } = useAuth();
  const { trigger } = useHaptics();
  const { theme, setThemeMode } = useDarkModeSync();
  const navigate = useNavigate();
  const [notifications, setNotifications] = useState(true);

  const handleToggle = (setter: (v: boolean) => void, value: boolean) => {
    trigger('light');
    setter(!value);
  };

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

        <SettingToggle
          icon={<Bell size={18} />}
          label="Notifications"
          description="Show push notifications"
          value={notifications}
          onChange={() => handleToggle(setNotifications, notifications)}
        />
      </div>

      <SensorsSection />

      <AppUpdatesSection />

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

          <AdminPinManager trigger={trigger} />
        </div>
      )}

      <SystemConfigSection isAdmin={Boolean(user?.is_admin)} onEdit={() => navigate('/admin/integrations')} />

      <div className="glass-panel rounded-2xl p-4 space-y-1">
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider px-3 pt-1 mb-2">Account</h2>

        <button
          onClick={() => { trigger('light'); }}
          className="w-full flex items-center justify-between p-3 rounded-xl bg-white/5 hover:bg-white/10 transition-colors"
        >
          <div className="flex items-center gap-3">
            <Key size={18} className="text-slate-400" />
            <div className="text-left">
              <p className="text-white text-sm font-medium">Personal Integrations</p>
              <p className="text-xs text-slate-400">Nextcloud, Skylight, GitHub, CalDAV</p>
            </div>
          </div>
          <ChevronRight size={16} className="text-slate-500" />
        </button>

        <button
          onClick={() => { trigger('light'); navigate('/identity'); }}
          className="w-full flex items-center justify-between p-3 rounded-xl bg-white/5 hover:bg-white/10 transition-colors"
        >
          <div className="flex items-center gap-3">
            <User size={18} className="text-slate-400" />
            <div className="text-left">
              <p className="text-white text-sm font-medium">Identity & API Keys</p>
              <p className="text-xs text-slate-400">Manage your profile and credentials</p>
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
  const [pin, setPin] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [configured, setConfigured] = useState(isAdminPinSet());

  const reset = () => {
    setPin('');
    setConfirm('');
    setError('');
  };

  const close = () => {
    setOpen(false);
    reset();
  };

  const save = async () => {
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
              {configured ? 'Required to unlock admin features on this device' : 'Not set — set to protect admin access'}
            </p>
          </div>
        </div>
        <span className="text-xs text-purple-400 font-medium">{configured ? 'Change' : 'Set'}</span>
      </button>

      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-xl">
          <div className="glass-panel w-full max-w-sm mx-4 p-6 rounded-2xl relative">
            <button onClick={close} className="absolute top-4 right-4 text-slate-400 hover:text-white">
              <X size={20} />
            </button>

            <h2 className="text-xl font-bold text-white text-center mb-1">
              {configured ? 'Change Admin PIN' : 'Set Admin PIN'}
            </h2>
            <p className="text-sm text-slate-400 text-center mb-6">Use at least 4 digits</p>

            <input
              type="password"
              inputMode="numeric"
              autoComplete="new-password"
              placeholder="New PIN"
              value={pin}
              onChange={(e) => setPin(e.target.value.replace(/\D/g, ''))}
              className="w-full mb-3 px-4 py-3 rounded-xl bg-white/5 border border-white/10 text-white text-center tracking-[0.5em] text-lg outline-none focus:border-purple-500/50"
            />
            <input
              type="password"
              inputMode="numeric"
              autoComplete="new-password"
              placeholder="Confirm PIN"
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

const SettingToggle = ({ icon, label, description, value, onChange }: {
  icon: React.ReactNode;
  label: string;
  description: string;
  value: boolean;
  onChange: () => void;
}) => (
  <div className="w-full flex items-center justify-between gap-3 p-3 rounded-xl bg-white/5 hover:bg-white/10 transition-colors">
    <div className="flex items-center gap-3 min-w-0 text-left">
      <span className="text-slate-400 shrink-0">{icon}</span>
      <div className="min-w-0">
        <p className="text-white text-sm font-medium">{label}</p>
        <p className="text-xs text-slate-400">{description}</p>
      </div>
    </div>
    <Toggle checked={value} onChange={() => onChange()} ariaLabel={label} />
  </div>
);

/** Always-on info-gathering sensors — each can be turned off; related services stop with it. */
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
      offDesc: 'Off — no location is recorded',
    },
    {
      id: 'steps',
      icon: <Footprints size={18} />,
      label: 'Step counter',
      onDesc: 'Hardware pedometer syncs to Wander',
      offDesc: 'Off — pedometer not read or synced',
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

const SystemConfigSection = ({ isAdmin, onEdit }: { isAdmin: boolean; onEdit: () => void }) => {
  const { trigger } = useHaptics();
  const { data: settings = [] } = useQuery<GlobalSetting[]>({
    queryKey: ['settings'],
    queryFn: () => api.getSettings(),
    retry: 1,
  });

  const visibleSettings = settings.filter(
    (s) => !['assistant_model', 'coding_model', 'librarian_model'].includes(s.key)
  );

  return (
    <div className="glass-panel rounded-2xl p-4 space-y-1">
      <div className="flex items-center justify-between px-3 pt-1 mb-2">
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider">System Configuration</h2>
        {isAdmin && (
          <button
            onClick={() => { trigger('light'); onEdit(); }}
            className="flex items-center gap-1 text-xs font-medium text-purple-400 hover:text-purple-300 transition-colors"
          >
            <SlidersHorizontal size={14} />
            Edit
          </button>
        )}
      </div>

      {visibleSettings.length === 0 ? (
        <p className="px-3 py-3 text-xs text-slate-500">No system configuration available.</p>
      ) : (
        <div className="space-y-1">
          {visibleSettings.map((setting) => (
            <div key={setting.key} className="px-3 py-2.5 rounded-xl bg-white/5">
              <p className="font-mono text-xs text-purple-300 truncate">{setting.key}</p>
              <p className="mt-1 text-xs text-slate-300 break-words">{setting.value}</p>
              {setting.description && (
                <p className="mt-1 text-[10px] text-slate-600 italic">{setting.description}</p>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

const AppUpdatesSection = () => {
  const { trigger } = useHaptics();
  const [checking, setChecking] = useState(false);
  const [lastChecked, setLastChecked] = useState<Date | null>(null);
  const [updateInfo, setUpdateInfo] = useState<{
    version: string;
    gitSha: string;
    remoteSha?: string;
    remoteVersion?: string;
    releaseNotes?: string;
    apkUrl?: string | null;
    apkAvailable?: boolean;
    hasUpdate?: boolean;
  }>({ version: '1.2.0', gitSha: 'unknown' });

  useEffect(() => {
    let active = true;
    void getRunningVersion().then((v) => {
      if (active) setUpdateInfo((prev) => ({ ...prev, version: v.version, gitSha: v.gitSha }));
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
        apkUrl: res.apkUrl,
        apkAvailable: res.apkUpdateAvailable,
      }));
      setLastChecked(new Date());
    } finally {
      setChecking(false);
    }
  };

  const updateState = updateInfo.hasUpdate
    ? { label: 'Update Available', cls: 'text-amber-300 border-amber-500/40 bg-amber-500/10' }
    : updateInfo.remoteSha
      ? { label: 'Up to Date', cls: 'text-emerald-300 border-emerald-500/40 bg-emerald-500/10' }
      : { label: 'Not Checked', cls: 'text-slate-400 border-white/10 bg-white/5' };

  return (
    <div className="glass-panel rounded-2xl p-4 space-y-3">
      <div className="flex items-center justify-between px-3 pt-1">
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider flex items-center gap-2">
          <Smartphone size={16} className="text-purple-400" />
          App &amp; OTA Updates
        </h2>
        <span className="text-[10px] text-slate-500 font-mono">v{updateInfo.version} ({updateInfo.gitSha})</span>
      </div>

      <div className="p-3.5 rounded-xl bg-white/5 space-y-2.5">
        <div className="flex items-center justify-between gap-2">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <p className="text-sm font-medium text-white">Live Over-The-Air Updates</p>
              <span className={`px-2 py-0.5 rounded-full border text-[9px] font-bold uppercase tracking-wider ${updateState.cls}`}>
                {updateState.label}
              </span>
            </div>
            <p className="text-xs text-slate-400 mt-0.5">
              Web UI updates install silently; background checks run on launch and every 6 hours
            </p>
            {updateInfo.remoteSha && (
              <p className="text-[10px] text-slate-500 font-mono mt-1">
                Server: {updateInfo.remoteVersion || '1.2.0'} ({updateInfo.remoteSha})
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
            className="glass-button shrink-0 px-3 py-1.5 rounded-xl text-xs font-semibold text-purple-300 hover:text-white flex items-center gap-1.5 border-purple-500/30"
          >
            <RefreshCw size={13} className={checking ? 'animate-spin text-purple-400' : ''} />
            <span>{checking ? 'Checking...' : 'Check Now'}</span>
          </button>
        </div>

        {updateInfo.apkAvailable && updateInfo.apkUrl && (
          <div className="mt-2 pt-2.5 border-t border-white/5 flex items-center justify-between gap-2">
            <div className="min-w-0">
              <p className="text-xs font-semibold text-amber-300 flex items-center gap-1">
                <span>📦 New Native APK Build Available</span>
              </p>
              <p className="text-[10px] text-slate-400">Required for new plugins / native permissions (e.g. step counter)</p>
            </div>
            <button
              onClick={() => {
                trigger('medium');
                downloadAndInstallApk(updateInfo.apkUrl!);
              }}
              className="shrink-0 px-3 py-1.5 rounded-xl bg-amber-500/20 text-amber-300 hover:bg-amber-500/30 border border-amber-500/40 text-xs font-bold flex items-center gap-1.5"
            >
              <Download size={13} />
              <span>Download APK</span>
            </button>
          </div>
        )}
      </div>
    </div>
  );
};

export default Settings;
