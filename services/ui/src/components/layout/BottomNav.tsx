import { NavLink } from 'react-router-dom';
import { Brain, Calendar, Compass, Home, MessageSquare, Music, Settings } from 'lucide-react';
import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';
import { useHaptics } from '../../hooks/useHaptics';
import { useAuth } from '../../context/AuthContext';

function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const BottomNav = () => {
  const { trigger } = useHaptics();
  const { user } = useAuth();

  const isAdmin = user?.is_admin ?? false;

  const navItems = [
    { icon: Home, label: 'Home', path: '/', roles: ['admin', 'user', 'child'] },
    { icon: Compass, label: 'Wander', path: '/wander', roles: ['admin', 'user', 'child'] },
    { icon: Music, label: 'Media', path: '/media', roles: ['admin', 'user', 'child'] },
    { icon: Calendar, label: 'Calendar', path: '/calendar', roles: ['admin', 'user', 'child'] },
    { icon: MessageSquare, label: 'Chat', path: '/communication', roles: ['admin', 'user', 'child'] },
  ];

  if (isAdmin) {
    navItems.push(
      { icon: Brain, label: 'Lab', path: '/lab', roles: ['admin'] as const },
    );
  } else {
    navItems.push(
      { icon: Settings, label: 'Settings', path: '/settings', roles: ['admin', 'user', 'child'] as const },
    );
  }

  const handleTap = () => {
    trigger('light');
  };

  return (
    <nav
      className="fixed bottom-0 left-0 right-0 z-50 bg-slate-950/95 backdrop-blur-2xl border-t border-slate-800/90 shadow-[0_-8px_30px_rgba(0,0,0,0.85)]"
      style={{ paddingBottom: 'env(safe-area-inset-bottom, 0px)' }}
    >
      <div className="flex items-center justify-around h-16 w-full max-w-lg mx-auto px-1">
        {navItems.map((item) => (
          <NavLink
            key={item.path}
            to={item.path}
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
      </div>
    </nav>
  );
};

export default BottomNav;
