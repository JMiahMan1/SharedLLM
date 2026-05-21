import { useState } from 'react';
import { useAuth } from '../context/AuthContext';
import { useHaptics } from '../hooks/useHaptics';
import { useDarkModeSync } from '../hooks/useDarkModeSync';
import { User, Shield, Bell, Moon, Key, LogOut, ChevronRight } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import LocationPanel from '../components/location/LocationPanel';

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
        </div>
      )}

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

const SettingToggle = ({ icon, label, description, value, onChange }: {
  icon: React.ReactNode;
  label: string;
  description: string;
  value: boolean;
  onChange: () => void;
}) => (
  <button
    onClick={onChange}
    className="w-full flex items-center justify-between p-3 rounded-xl bg-white/5 hover:bg-white/10 transition-colors"
  >
    <div className="flex items-center gap-3">
      <span className="text-slate-400">{icon}</span>
      <div className="text-left">
        <p className="text-white text-sm font-medium">{label}</p>
        <p className="text-xs text-slate-400">{description}</p>
      </div>
    </div>
    <div className={`w-10 h-6 rounded-full relative transition-colors ${value ? 'bg-purple-500' : 'bg-slate-600'}`}>
      <div className={`absolute top-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform ${value ? 'translate-x-4' : 'translate-x-0.5'}`} />
    </div>
  </button>
);

export default Settings;
