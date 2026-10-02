import { useEffect, useState } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { Brain, Calendar, Compass, HeartPulse, Home, Music, Settings, Shield, StickyNote, Users, X } from 'lucide-react';
import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';
import { useHaptics } from '../../hooks/useHaptics';
import { useAuth } from '../../context/AuthContext';

function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const pinnedItems = [
  { icon: Home, label: 'Home', path: '/' },
  { icon: Compass, label: 'Wander', path: '/wander' },
  { icon: Users, label: 'Family', path: '/family' },
  { icon: Music, label: 'Media', path: '/media' },
  { icon: HeartPulse, label: 'Health', path: '/fitness' },
];

const BottomNav = () => {
  const { trigger } = useHaptics();
  const { user } = useAuth();
  const location = useLocation();
  const [moreOpen, setMoreOpen] = useState(false);

  const isAdmin = user?.is_admin ?? false;

  const moreItems = [
    { icon: Calendar, label: 'Calendar', path: '/calendar', adminOnly: false },
    { icon: StickyNote, label: 'Notes', path: '/notes', adminOnly: false },
    { icon: Brain, label: 'Lab', path: '/lab', adminOnly: true },
    // Admin-only. Without this, an admin on a phone can reach the control
    // panel only by digging through the profile menu, which is not
    // discoverable — the panel is where user management lives.
    { icon: Shield, label: 'Admin', path: '/admin', adminOnly: true },
    { icon: Settings, label: 'Settings', path: '/settings', adminOnly: false },
  ].filter((item) => !item.adminOnly || isAdmin);

  const moreActive = moreItems.some((item) => location.pathname.startsWith(item.path));

  useEffect(() => {
    if (!moreOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setMoreOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [moreOpen]);

  const handleTap = () => {
    trigger('light');
  };

  return (
    <>
      {moreOpen && (
        <div className="fixed inset-0 z-[60] flex items-end" data-testid="more-sheet">
          <button
            type="button"
            aria-label="Close menu"
            className="absolute inset-0 bg-black/60 backdrop-blur-sm"
            onClick={() => {
              trigger('light');
              setMoreOpen(false);
            }}
          />
          <div className="relative w-full max-w-lg mx-auto bg-slate-950/95 backdrop-blur-2xl border-t border-slate-800/90 rounded-t-2xl p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] shadow-[0_-8px_30px_rgba(0,0,0,0.85)]">
            <div className="flex items-center justify-between px-2 pb-2">
              <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider">More</span>
              <button
                type="button"
                aria-label="Close menu"
                className="p-2 -m-1 text-slate-400 hover:text-slate-200"
                onClick={() => {
                  trigger('light');
                  setMoreOpen(false);
                }}
              >
                <X size={18} />
              </button>
            </div>
            <div className="grid grid-cols-2 gap-2">
              {moreItems.map((item) => (
                <NavLink
                  key={item.path}
                  to={item.path}
                  onClick={() => {
                    handleTap();
                    setMoreOpen(false);
                  }}
                  className={({ isActive }) =>
                    cn(
                      'flex items-center gap-3 min-h-11 px-3 py-2.5 rounded-xl border transition-colors',
                      isActive || location.pathname.startsWith(item.path)
                        ? 'bg-purple-500/15 border-purple-500/40 text-purple-300'
                        : 'bg-white/5 border-white/10 text-slate-300 hover:bg-white/10'
                    )
                  }
                >
                  <item.icon size={18} className="shrink-0" />
                  <span className="text-sm font-medium">{item.label}</span>
                </NavLink>
              ))}
            </div>
          </div>
        </div>
      )}
      <nav
        className="fixed bottom-0 left-0 right-0 z-50 bg-slate-950/95 backdrop-blur-2xl border-t border-slate-800/90 shadow-[0_-8px_30px_rgba(0,0,0,0.85)]"
        style={{ paddingBottom: 'env(safe-area-inset-bottom, 0px)' }}
      >
        <div className="flex items-center justify-around h-16 w-full max-w-lg mx-auto px-1">
          {pinnedItems.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              end={item.path === '/'}
              onClick={handleTap}
              className={({ isActive }) =>
                cn(
                  'flex flex-col items-center justify-center gap-1 py-1.5 px-0.5 flex-1 min-w-0 transition-colors',
                  isActive
                    ? 'text-purple-400 font-bold'
                    : 'text-slate-400 hover:text-slate-200'
                )
              }
            >
              <item.icon size={19} className="shrink-0" />
              <span className="text-[10px] font-medium truncate w-full text-center tracking-tight leading-none">
                {item.label}
              </span>
            </NavLink>
          ))}
          <button
            type="button"
            onClick={() => {
              trigger('light');
              setMoreOpen((v) => !v);
            }}
            aria-expanded={moreOpen}
            aria-label="More pages"
            className={cn(
              'flex flex-col items-center justify-center gap-1 py-1.5 px-0.5 flex-1 min-w-0 transition-colors',
              moreOpen || moreActive
                ? 'text-purple-400 font-bold'
                : 'text-slate-400 hover:text-slate-200'
            )}
          >
            <span className="relative">
              <span className="block w-[19px] h-[19px] relative">
                <span className="absolute top-1/2 left-0 -translate-y-1/2 w-[4px] h-[4px] rounded-full bg-current" />
                <span className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[4px] h-[4px] rounded-full bg-current" />
                <span className="absolute top-1/2 right-0 -translate-y-1/2 w-[4px] h-[4px] rounded-full bg-current" />
              </span>
            </span>
            <span className="text-[10px] font-medium truncate w-full text-center tracking-tight leading-none">
              More
            </span>
          </button>
        </div>
      </nav>
    </>
  );
};

export default BottomNav;
