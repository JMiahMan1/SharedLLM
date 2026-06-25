import { useState, useEffect, useCallback } from 'react';
import { NavLink } from 'react-router-dom';
import { 
  LayoutDashboard, 
  Settings, 
  UserCircle, 
  MessageSquare, 
  FlaskConical, 
  Activity,
  HelpCircle,
  Database,
  Boxes,
  Music,
  Radio,
  SlidersHorizontal,
  Brain,
  Loader2,
  ShieldCheck,
} from 'lucide-react';
import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';
import { useAuth } from '../../context/AuthContext';
import { api } from '../../services/api';
import type { RavenMission } from '../../services/api';

function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const navItems = [
  { icon: LayoutDashboard, label: 'Dashboard', path: '/' },
  { icon: Boxes, label: 'My Workspaces', path: '/workspaces' },
  { icon: Music, label: 'Media', path: '/media' },
  { icon: Radio, label: 'Remote', path: '/remote' },
  { icon: MessageSquare, label: 'Communication', path: '/communication' },
  { icon: Database, label: 'Knowledge Hub', path: '/knowledge' },
  { icon: UserCircle, label: 'Identity', path: '/identity' },
  { icon: SlidersHorizontal, label: 'Settings', path: '/settings' },
  { icon: HelpCircle, label: 'Help Hub', path: '/docs' },
  // Admin-only: System Ops & Raven
  { icon: Settings, label: 'System Ops & Raven', path: '/admin/ops', adminOnly: true },
  { icon: FlaskConical, label: 'Jarvis Lab', path: '/lab', adminOnly: true },
];

const Sidebar = () => {
  const { user } = useAuth();
  return (
    <aside className="w-20 md:w-64 glass-sidebar m-2 md:m-4 md:mr-0 flex flex-col transition-all duration-300 rounded-2xl">
      <div className="p-4 md:p-6 flex justify-center md:justify-start">
        <h1 className="text-xl font-bold bg-gradient-to-r from-purple-400 to-pink-400 bg-clip-text text-transparent flex items-center gap-2">
          <Activity className="text-purple-400" />
          <span className="hidden md:inline">Jarvis OS</span>
        </h1>
      </div>
      
      <nav className="flex-1 px-2 md:px-4 space-y-2 overflow-y-auto">
        {navItems
          .filter(item => !item.adminOnly || user?.is_admin)
          .map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              className={({ isActive }) => cn(
                "flex items-center justify-center md:justify-start gap-3 px-3 md:px-4 py-3 rounded-xl transition-all duration-200",
                isActive 
                  ? "bg-purple-600/30 text-white border border-purple-500/40 neon-border shadow-lg shadow-purple-500/10" 
                  : "text-slate-400 hover:text-white hover:bg-white/5"
              )}
              title={item.label}
            >
              <item.icon size={20} className="shrink-0" />
              <span className="font-medium hidden md:inline">{item.label}</span>
            </NavLink>
          ))}
      </nav>

      {user?.is_admin && (
        <RavenStatusSection />
      )}
      <div className="p-4 mt-auto hidden md:block">
        <div className="glass-card p-4 text-xs text-slate-500">
          <p>System v1.0.0-alpha</p>
          <div className="flex items-center gap-2 mt-1">
            <div className="w-2 h-2 rounded-full bg-green-500 animate-pulse" />
            <span>All Services Nominal</span>
          </div>
        </div>
      </div>
    </aside>
  );
};

const RavenStatusSection = () => {
  const [activeMissions, setActiveMissions] = useState<RavenMission[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchMissions = useCallback(async () => {
    try {
      const resp = await api.getUserMissions();
      const missions = Array.isArray(resp) ? resp : [];
      const active = missions.filter((m: RavenMission) =>
        ['queued', 'running', 'paused'].includes(m.status)
      );
      setActiveMissions(active);
    } catch {
      setActiveMissions([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Defer initial fetch to avoid synchronous setState in effect
    const timeoutId = setTimeout(() => fetchMissions(), 0);
    const intervalId = setInterval(fetchMissions, 30000);
    return () => {
      clearTimeout(timeoutId);
      clearInterval(intervalId);
    };
  }, [fetchMissions]);

  const handleLaunch = async () => {
    try {
      await api.createUserMission('System diagnostic and maintenance scan', 2);
      await fetchMissions();
    } catch {
      // Silently fail - user sees this in Lab
    }
  };

  return (
    <div className="p-4 md:px-4 hidden md:block">
      <div className="glass-card p-3 md:p-4 text-xs border-l-2 border-l-purple-500">
        <button
          onClick={handleLaunch}
          className="flex items-center gap-2 text-slate-300 hover:text-purple-400 transition-colors w-full"
          title="Launch Raven mission"
        >
          {loading ? (
            <Loader2 size={14} className="animate-spin text-purple-400" />
          ) : activeMissions.length > 0 ? (
            <Brain size={14} className="text-purple-400" />
          ) : (
            <ShieldCheck size={14} className="text-green-400" />
          )}
          <span className="font-medium hidden md:inline">
            {loading ? 'Loading...' : activeMissions.length > 0 ? `${activeMissions.length} Mission${activeMissions.length > 1 ? 's' : ''} Active` : 'Raven Idle'}
          </span>
        </button>
        {!loading && activeMissions.length > 0 && (
          <div className="mt-2 space-y-1 hidden md:block">
            {activeMissions.slice(0, 2).map((mission: RavenMission) => (
              <div key={mission.id} className="flex items-center gap-1 text-slate-400">
                <div className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
                  mission.status === 'running' ? 'bg-orange-400 animate-pulse' :
                  mission.status === 'queued' ? 'bg-yellow-400' :
                  'bg-blue-400'
                }`} />
                <span className="truncate">{mission.proposed_mission.substring(0, 40)}{mission.proposed_mission.length > 40 ? '...' : ''}</span>
              </div>
            ))}
          </div>
        )}
        {!loading && activeMissions.length === 0 && (
          <p className="text-slate-500 hidden md:block mt-2">
            Raven ready to scan workspaces
          </p>
        )}
      </div>
    </div>
  );
};

export default Sidebar;
