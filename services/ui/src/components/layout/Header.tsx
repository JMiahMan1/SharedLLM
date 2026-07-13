import { useState, useMemo, useRef, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Search, Bell, LogOut, Trash2, Satellite } from 'lucide-react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../services/api';
import type { LogEntry } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import { useLocation } from '../../context/LocationContext';

// Navigation search index — the header box is a *command-palette* style finder
// for pages/tabs/settings, distinct from the RAG/semantic search on Knowledge.
type NavItem = { label: string; path: string; section: string; keywords: string };
const NAV_ITEMS: NavItem[] = [
  { label: 'Dashboard', path: '/', section: 'Pages', keywords: 'home overview jarvis' },
  { label: 'Knowledge', path: '/knowledge', section: 'Pages', keywords: 'rag semantic memory storage indexed files ha entities' },
  { label: 'Identity', path: '/identity', section: 'Pages', keywords: 'users accounts login credentials' },
  { label: 'Communication', path: '/communication', section: 'Pages', keywords: 'talk nextcloud messages chat' },
  { label: 'Calendar', path: '/calendar', section: 'Pages', keywords: 'schedule events' },
  { label: 'Media', path: '/media', section: 'Pages', keywords: 'music assistant audiobookshelf player' },
  { label: 'Remote', path: '/remote', section: 'Pages', keywords: 'home assistant control devices' },
  { label: 'Settings', path: '/settings', section: 'Pages', keywords: 'preferences configuration profile' },
  { label: 'Workspaces', path: '/workspaces', section: 'Pages', keywords: 'files folders registry' },
  { label: 'Docs', path: '/docs', section: 'Pages', keywords: 'documentation help' },
  { label: 'Lab', path: '/lab', section: 'Pages', keywords: 'raven missions autonomous agent' },
  { label: 'Admin', path: '/admin', section: 'Pages', keywords: 'manage control panel users services' },
  { label: 'Profile Settings', path: '/settings', section: 'Settings', keywords: 'account name password' },
  { label: 'RAG / Storage Settings', path: '/knowledge', section: 'Settings', keywords: 'embeddings collections purge index' },
  { label: 'Voice Assistant', path: '/settings', section: 'Settings', keywords: 'microphone speech tts' },
  { label: 'Notifications', path: '/', section: 'Tabs', keywords: 'alerts bell logs' },
];

const LEVEL_COLOR: Record<string, string> = {
  ERROR: 'text-red-400',
  WARNING: 'text-yellow-400',
  WARN: 'text-yellow-400',
  INFO: 'text-emerald-400',
  DEBUG: 'text-slate-400',
};

const Header = () => {
  const { user, logout } = useAuth();
  const { isTracking } = useLocation();
  const navigate = useNavigate();
  const [navQuery, setNavQuery] = useState('');
  const [navOpen, setNavOpen] = useState(false);
  const navRef = useRef<HTMLDivElement>(null);

  const matches = useMemo(() => {
    const q = navQuery.trim().toLowerCase();
    if (!q) return [];
    return NAV_ITEMS.filter((it) =>
      (it.label + ' ' + it.keywords + ' ' + it.section).toLowerCase().includes(q)
    ).slice(0, 12);
  }, [navQuery]);

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (navRef.current && !navRef.current.contains(e.target as Node)) setNavOpen(false);
    };
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, []);
  
  const { data: health, isLoading, error } = useQuery({
    queryKey: ['health'],
    queryFn: () => api.getHealth(),
    refetchInterval: 10000,
  });

  const { data: notifications = [] } = useQuery<LogEntry[]>({
    queryKey: ['header-notifications'],
    queryFn: () => api.getLogs(50),
    refetchInterval: 15000,
  });

  // Periodic background check for service updates (admin accounts only)
  useQuery({
    queryKey: ['service-updates-background'],
    queryFn: () => api.checkAllUpdates(),
    enabled: !!user?.is_admin,
    refetchInterval: 300000, // 5 minutes
    refetchOnWindowFocus: false,
  });

  const queryClient = useQueryClient();
  const clearMutation = useMutation({
    mutationFn: () => api.clearLogs(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['header-notifications'] });
      queryClient.invalidateQueries({ queryKey: ['recent-logs'] });
    },
  });

  const handleClearLogs = async () => {
    try {
      await clearMutation.mutateAsync();
    } catch {
      // Error handled by query client or toast if needed
    }
  };

  const [showNotifications, setShowNotifications] = useState(false);
  const isReady = health?.status === 'READY';
  const statusColor = error ? 'bg-red-500' : (isLoading ? 'bg-yellow-500' : (isReady ? 'bg-green-500' : 'bg-red-400'));
  const statusText = error ? 'Offline' : (isLoading ? 'Polling...' : (health?.status || 'Unknown'));

  // Filter notifications: show communications for the logged-in user or updates for admins
  const filteredNotifications = notifications.filter(n => {
    const msg = n.message.toLowerCase();
    const service = n.service.toLowerCase();
    
    // 1. Check if it's an update notification (admin only)
    const isUpdate = msg.includes('update available') || msg.includes('updates available') || msg.includes('image update');
    if (isUpdate) {
      return !!user?.is_admin;
    }
    
    // 2. Check if it's a communication (Nextcloud Talk, messages, mentions, chats)
    const isComm = ['talk', 'nextcloud', 'message', 'chat', 'mention', 'communication', 'notification'].some(
      kw => msg.includes(kw) || service.includes(kw)
    );
    
    const relatesToUser = user?.username && msg.includes(user.username.toLowerCase());
    
    if (isComm) {
      // Include if it explicitly mentions/targets the current user or is from talk/nextcloud services
      if (relatesToUser || service.includes('talk') || service.includes('nextcloud') || msg.includes('talk') || msg.includes('nextcloud')) {
        return true;
      }
    }
    
    return false;
  });

  const hasErrors = filteredNotifications.some(n => n.level === 'ERROR' || n.level === 'WARNING' || n.level === 'WARN');

  return (
    <header className="h-14 md:h-20 flex items-center justify-between px-4 md:px-8 bg-transparent">
      <div className="flex-1 max-w-2xl relative group md:flex hidden" ref={navRef}>
        <div className="absolute inset-y-0 left-4 flex items-center pointer-events-none text-slate-400 group-focus-within:text-purple-400 transition-colors">
          <Search size={20} />
        </div>
        <input
          type="text"
          value={navQuery}
          onChange={(e) => { setNavQuery(e.target.value); setNavOpen(true); }}
          onFocus={() => setNavOpen(true)}
          onKeyDown={(e) => {
            if (e.key === 'Escape') { setNavOpen(false); setNavQuery(''); }
            if (e.key === 'Enter' && matches[0]) {
              navigate(matches[0].path);
              setNavOpen(false); setNavQuery('');
            }
          }}
          placeholder="Search pages, tabs & settings…"
          className="w-full glass-input pl-12 h-12 text-lg"
          aria-label="Search pages, tabs and settings"
        />
        {navOpen && navQuery.trim() && (
          <div className="absolute top-14 left-0 right-0 z-50 glass-panel p-2 max-h-96 overflow-y-auto">
            {matches.length === 0 ? (
              <p className="text-xs text-slate-500 px-3 py-3">No matches found.</p>
            ) : (
              matches.map((m) => (
                <button
                  key={`${m.path}-${m.label}`}
                  onClick={() => { navigate(m.path); setNavOpen(false); setNavQuery(''); }}
                  className="w-full flex items-center justify-between gap-3 px-3 py-2.5 rounded-lg text-left hover:bg-white/5 transition-colors"
                >
                  <span className="text-sm text-white font-medium">{m.label}</span>
                  <span className="text-[9px] font-black uppercase tracking-widest text-slate-500 bg-white/5 px-2 py-0.5 rounded-md">
                    {m.section}
                  </span>
                </button>
              ))
            )}
          </div>
        )}
      </div>

      <div className="flex items-center gap-3 md:gap-6 ml-auto md:ml-0">
        <div className="hidden md:flex items-center gap-1.5 p-2 rounded-lg" title={isTracking ? 'Location tracking active' : 'Location tracking paused'}>
          <Satellite size={16} className={isTracking ? 'text-green-400' : 'text-slate-600'} />
          <div className={`w-1.5 h-1.5 rounded-full ${isTracking ? 'bg-green-400' : 'bg-red-500'}`} />
        </div>

        <div className="hidden sm:flex items-center gap-2 glass-panel neon-border px-4 py-2 text-sm">
          <div className={`w-2 h-2 rounded-full ${statusColor} animate-pulse`} />
          <span className="text-slate-300">Pulse:</span>
          <span className={`font-semibold uppercase ${isReady ? 'text-green-400' : 'text-yellow-400'}`}>
            {statusText}
          </span>
        </div>

        <div className="relative">
          <button 
            onClick={() => setShowNotifications(!showNotifications)}
            className="glass-card p-3 relative hover:bg-white/5 transition-colors"
          >
            <Bell size={20} className="text-slate-300" />
            {/* Show dot only if there are active notifications */}
            {filteredNotifications.length > 0 && (
              <span className={`absolute top-2 right-2 w-2 h-2 rounded-full ${hasErrors ? 'bg-red-500' : 'bg-emerald-500'}`} />
            )}
          </button>
          
          {showNotifications && (
            <div className="absolute right-0 mt-4 w-80 max-w-[calc(100vw-2rem)] glass-panel p-4 z-50 animate-in slide-in-from-top-2 duration-200">
              <div className="flex items-center justify-between mb-4 border-b border-white/5 pb-3">
                <h4 className="text-sm font-bold text-white uppercase tracking-widest">Notifications</h4>
                <button 
                  onClick={handleClearLogs}
                  disabled={filteredNotifications.length === 0 || clearMutation.isPending}
                  className="flex items-center gap-1.5 text-[10px] font-black uppercase tracking-widest text-red-400 hover:text-red-300 transition-colors disabled:opacity-30 p-2 -m-2"
                >
                  <Trash2 size={12} />
                  {clearMutation.isPending ? '...' : 'Clear All'}
                </button>
              </div>
              <div className="space-y-2 max-h-72 overflow-y-auto pr-1">
                {filteredNotifications.length === 0 ? (
                  <p className="text-xs text-slate-500 text-center py-4">No notifications</p>
                ) : filteredNotifications.slice(0, 5).map((log, i) => (
                  <div key={`${log.timestamp}-${i}`} className="p-3 rounded-xl bg-white/5 border border-white/5">
                    <div className="flex items-center justify-between gap-2">
                      <p className="text-xs text-white font-medium truncate">{log.service}</p>
                      <span className={`text-[9px] font-black uppercase tracking-widest shrink-0 ${LEVEL_COLOR[log.level] ?? 'text-slate-400'}`}>
                        {log.level}
                      </span>
                    </div>
                    <p className="text-[11px] text-slate-400 mt-1 break-words line-clamp-2">{log.message}</p>
                    <p className="text-[9px] text-slate-600 mt-1.5">
                      {new Date(log.timestamp).toLocaleTimeString()}
                    </p>
                  </div>
                ))}
              </div>
              <button 
                onClick={() => setShowNotifications(false)}
                className="w-full mt-3 py-3 text-[10px] font-black uppercase tracking-widest text-slate-500 hover:text-white transition-colors border-t border-white/5 pt-3"
              >
                Dismiss
              </button>
            </div>
          )}
        </div>

        <div className="flex items-center gap-3">
          <div className="text-right hidden sm:block">
            <p className="text-sm font-semibold text-white">{user?.username || 'Guest'}</p>
            <p className="text-xs text-slate-500">{user?.is_admin ? 'Admin' : 'Family Member'}</p>
          </div>
          <button 
            onClick={logout}
            className="w-11 h-11 rounded-full bg-gradient-to-br from-purple-500 to-pink-500 flex items-center justify-center text-white font-bold border border-white/20 hover:scale-110 transition-transform group relative"
          >
            {user?.username?.[0].toUpperCase() || 'G'}
            <div className="absolute inset-0 rounded-full bg-black/60 opacity-0 group-hover:opacity-100 flex items-center justify-center transition-opacity">
              <LogOut size={16} />
            </div>
          </button>
        </div>
      </div>
    </header>
  );
};

export default Header;
