import { useState, useMemo, useRef, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Search, Bell, LogOut, Satellite, User as UserIcon, Settings, Shield, ChevronUp } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../services/api';
import type { TelemetryNotification } from '../../types/api';
import { useAuth } from '../../context/AuthContext';
import { useLocation } from '../../context/LocationContext';
import { useSystemHealth } from '../../hooks/useSystemHealth';
import { relativeTime } from '../../lib/wanderTrips';
import { useClearedAt, newerThan } from '../../lib/clearedAt';
import { navPagesFor } from './navItems';

// Navigation search index: the header box is a *command-palette* style finder
// for pages/tabs/settings, distinct from the RAG/semantic search on Knowledge.
// Pages come from the shared nav list; these are the extra destinations.
type NavItem = { label: string; path: string; section: string; keywords: string };
const EXTRA_ITEMS: NavItem[] = [
  { label: 'Accounts & Integrations', path: '/identity', section: 'Pages', keywords: 'identity users accounts credentials api keys home assistant nextcloud github voice devices' },
  { label: 'Communication', path: '/communication', section: 'Pages', keywords: 'talk nextcloud messages chat' },
  { label: 'Remote', path: '/remote', section: 'Pages', keywords: 'home assistant control devices' },
  { label: 'Profile Settings', path: '/settings', section: 'Settings', keywords: 'account name password' },
  { label: 'RAG / Storage Settings', path: '/knowledge', section: 'Settings', keywords: 'embeddings collections purge index' },
  { label: 'Voice Assistant', path: '/settings', section: 'Settings', keywords: 'microphone speech tts' },
];

/** When the bell was last opened; notices newer than this light the dot. */
const SEEN_KEY = 'jarvis_notifications_seen_at';
/** When "Clear all" was last used; older notices stay hidden on this device. */
const CLEARED_KEY = 'jarvis_notifications_cleared_at';

const Header = () => {
  const { user, logout } = useAuth();
  const { isTracking } = useLocation();
  const navigate = useNavigate();
  const [navQuery, setNavQuery] = useState('');
  const [navOpen, setNavOpen] = useState(false);
  const navRef = useRef<HTMLDivElement>(null);
  const [profileOpen, setProfileOpen] = useState(false);
  const [showNotifications, setShowNotifications] = useState(false);
  const profileRef = useRef<HTMLDivElement>(null);
  const bellRef = useRef<HTMLDivElement>(null);

  const navItems = useMemo<NavItem[]>(() => [
    ...navPagesFor(!!user?.is_admin).map(({ label, path, keywords }) => ({ label, path, keywords, section: 'Pages' })),
    ...EXTRA_ITEMS,
  ], [user?.is_admin]);

  const matches = useMemo(() => {
    const q = navQuery.trim().toLowerCase();
    if (!q) return [];
    return navItems.filter((it) =>
      (it.label + ' ' + it.keywords + ' ' + it.section).toLowerCase().includes(q)
    ).slice(0, 12);
  }, [navQuery, navItems]);

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (navRef.current && !navRef.current.contains(e.target as Node)) setNavOpen(false);
    };
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, []);

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (profileRef.current && !profileRef.current.contains(e.target as Node)) setProfileOpen(false);
      if (bellRef.current && !bellRef.current.contains(e.target as Node)) setShowNotifications(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setProfileOpen(false);
        setShowNotifications(false);
      }
    };
    document.addEventListener('mousedown', onClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onClick);
      document.removeEventListener('keydown', onKey);
    };
  }, []);
  
  const { data: health, isLoading, error } = useSystemHealth();

  // The bell shows this user's own notices from the telemetry outbox: family
  // arrivals/departures, leave-by reminders, finished reports: the same
  // notices the Android app raises as system notifications. It used to show
  // filtered system *logs* (admin only), which never matched anything, and
  // its "Clear All" wiped the whole system log.
  const { data: notifications = [] } = useQuery<TelemetryNotification[]>({
    queryKey: ['header-notifications'],
    queryFn: async () => {
      const { notifications: items } = await api.getTelemetryNotifications(20);
      return [...(items ?? [])].sort((x, y) => (y.created_at ?? '').localeCompare(x.created_at ?? ''));
    },
    refetchInterval: 60_000,
    enabled: !!user,
  });

  // Periodic background check for service updates (admin accounts only).
  // Same query key as Admin so pull/restart invalidations refresh the badge.
  useQuery({
    queryKey: ['service-updates'],
    queryFn: () => api.checkAllUpdates(),
    enabled: !!user?.is_admin,
    refetchInterval: 300000, // 5 minutes
    refetchOnWindowFocus: false,
    staleTime: 60000,
  });

  const [seenAt, markSeen] = useClearedAt(SEEN_KEY);
  // "Clear all" hides the current notices on this device; the outbox keeps
  // them (they are also the phone's notification history).
  const [clearedAt, clearNotifications] = useClearedAt(CLEARED_KEY);
  const visible = newerThan(notifications, clearedAt, (n) => n.created_at);
  const unread = newerThan(visible, seenAt, (n) => n.created_at).length;

  const toggleNotifications = () => {
    const opening = !showNotifications;
    setShowNotifications(opening);
    if (opening && notifications[0]?.created_at) markSeen(notifications[0].created_at);
  };

  const isReady = health?.status === 'READY';
  const statusColor = error ? 'bg-red-500' : (isLoading ? 'bg-yellow-500' : (isReady ? 'bg-green-500' : 'bg-red-400'));
  const statusText = error ? 'Offline' : (isLoading ? 'Polling...' : (health?.status || 'Unknown'));

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
          <div className="absolute top-14 left-0 right-0 z-50 glass-menu p-2 max-h-96 overflow-y-auto">
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

        <div className="relative" ref={bellRef}>
          <button
            onClick={toggleNotifications}
            aria-label={unread ? `Notifications, ${unread} new` : 'Notifications'}
            aria-expanded={showNotifications}
            className="glass-card p-3 relative hover:bg-white/5 transition-colors"
          >
            <Bell size={20} className="text-slate-300" />
            {unread > 0 && (
              <span className="absolute top-2 right-2 w-2 h-2 rounded-full bg-emerald-500" />
            )}
          </button>

          {showNotifications && (
            <div className="fixed left-4 right-4 top-16 sm:absolute sm:left-auto sm:top-auto sm:right-0 sm:mt-4 sm:w-80 glass-menu p-4 z-50 animate-in slide-in-from-top-2 duration-200">
              <div className="mb-3 flex items-center justify-between gap-3 border-b border-white/5 pb-2">
                <h4 className="text-sm font-bold text-white">Notifications</h4>
                {visible.length > 0 && (
                  <button
                    onClick={() => clearNotifications(visible[0]?.created_at)}
                    className="min-h-11 px-2 -mr-2 text-xs font-semibold text-slate-400 hover:text-white transition-colors"
                  >
                    Clear all
                  </button>
                )}
              </div>
              <div className="space-y-2 max-h-[60vh] sm:max-h-72 overflow-y-auto pr-1">
                {visible.length === 0 ? (
                  <p className="text-xs text-slate-500 text-center py-6">You're all caught up</p>
                ) : visible.slice(0, 10).map((n) => (
                  <div key={n.id} className="p-3 rounded-xl bg-white/5 border border-white/5">
                    <p className="text-xs text-white font-medium break-words">{n.title}</p>
                    {(n.body || n.error) && (
                      <p className="text-[11px] text-slate-400 mt-1 break-words line-clamp-2">{n.body || n.error}</p>
                    )}
                    <p className="text-[10px] text-slate-500 mt-1.5">{relativeTime(n.created_at)}</p>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="flex items-center gap-3">
          <div className="text-right hidden sm:block">
            <p className="text-sm font-semibold text-white">{user?.username || 'Guest'}</p>
            <p className="text-xs text-slate-500">{user?.is_admin ? 'Admin' : 'Family Member'}</p>
          </div>
          <div className="relative" ref={profileRef} data-testid="profile-menu-root">
            <button
              onClick={() => setProfileOpen((open) => !open)}
              aria-haspopup="menu"
              aria-expanded={profileOpen}
              aria-label="Account menu"
              className="w-11 h-11 rounded-full bg-gradient-to-br from-purple-500 to-pink-500 flex items-center justify-center text-white font-bold border border-white/20 hover:scale-110 transition-transform group relative"
            >
              {user?.username?.[0].toUpperCase() || 'G'}
              <div className="absolute -bottom-0.5 -right-0.5 w-4 h-4 rounded-full bg-slate-950 border border-white/20 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
                <ChevronUp size={10} />
              </div>
            </button>

            {profileOpen && (
              <div
                role="menu"
                aria-label="Account menu"
                data-testid="profile-menu"
                className="absolute right-0 mt-2 w-60 glass-menu p-1.5 rounded-2xl z-50"
              >
                <div className="px-3 py-2.5 border-b border-white/5 mb-1">
                  <p className="text-sm font-semibold text-white truncate">{user?.username || 'Guest'}</p>
                  <p className="text-[11px] text-slate-500">
                    {user?.is_admin ? 'Admin' : 'Family Member'}
                  </p>
                </div>

                <button
                  role="menuitem"
                  onClick={() => {
                    setProfileOpen(false);
                    navigate('/settings');
                  }}
                  className="w-full flex items-center gap-2.5 px-3 min-h-11 rounded-xl text-sm text-[var(--site-text)] hover:bg-slate-500/20 transition-colors"
                >
                  <Settings size={15} className="text-slate-400" /> Profile Settings
                </button>

                <button
                  role="menuitem"
                  onClick={() => {
                    setProfileOpen(false);
                    navigate('/identity');
                  }}
                  className="w-full flex items-center gap-2.5 px-3 min-h-11 rounded-xl text-sm text-[var(--site-text)] hover:bg-slate-500/20 transition-colors"
                >
                  <UserIcon size={15} className="text-slate-400" /> Accounts & Integrations
                </button>

                {user?.is_admin && (
                  <button
                    role="menuitem"
                    onClick={() => {
                      setProfileOpen(false);
                      navigate('/admin');
                    }}
                    className="w-full flex items-center gap-2.5 px-3 min-h-11 rounded-xl text-sm text-[var(--site-text)] hover:bg-slate-500/20 transition-colors"
                  >
                    <Shield size={15} className="text-purple-300" /> Admin Control Panel
                  </button>
                )}

                <div className="my-1 border-t border-white/5" />

                <button
                  role="menuitem"
                  data-testid="profile-menu-logout"
                  onClick={() => {
                    setProfileOpen(false);
                    logout();
                  }}
                  className="w-full flex items-center gap-2.5 px-3 min-h-11 rounded-xl text-sm text-rose-300 hover:bg-rose-500/10 transition-colors"
                >
                  <LogOut size={15} /> Log Out
                </button>
              </div>
            )}
          </div>
        </div>
      </div>
    </header>
  );
};

export default Header;
