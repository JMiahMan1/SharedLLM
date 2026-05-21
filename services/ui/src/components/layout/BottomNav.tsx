import { NavLink } from 'react-router-dom';
import { Home, Mic, Music, Radio, Settings } from 'lucide-react';
import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';
import { useHaptics } from '../../hooks/useHaptics';
import { useAuth } from '../../context/AuthContext';

function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const allNavItems = [
  { icon: Home, label: 'Home', path: '/', roles: ['admin', 'user', 'child'] },
  { icon: Mic, label: 'Intercom', path: '/communication', roles: ['admin', 'user', 'child'] },
  { icon: Music, label: 'Media', path: '/media', roles: ['admin', 'user', 'child'] },
  { icon: Radio, label: 'Remote', path: '/remote', roles: ['admin', 'user'] },
  { icon: Settings, label: 'Settings', path: '/settings', roles: ['admin', 'user'] },
];

const BottomNav = () => {
  const { trigger } = useHaptics();
  const { user } = useAuth();

  const role = user?.is_admin ? 'admin' : (user?.role || 'user');
  const navItems = allNavItems.filter((item) => item.roles.includes(role));

  const handleTap = () => {
    trigger('light');
  };

  return (
    <nav className="fixed bottom-0 left-0 right-0 z-50 glass-panel border-t border-white/10 safe-area-bottom">
      <div className="flex items-center justify-around h-16">
        {navItems.map((item) => (
          <NavLink
            key={item.path}
            to={item.path}
            onClick={handleTap}
            className={({ isActive }) =>
              cn(
                'flex flex-col items-center justify-center gap-1 px-3 py-2 min-w-0 flex-1 transition-colors',
                isActive
                  ? 'text-purple-400'
                  : 'text-slate-500 hover:text-slate-300'
              )
            }
          >
            <item.icon size={22} />
            <span className="text-[10px] font-medium truncate">{item.label}</span>
          </NavLink>
        ))}
      </div>
    </nav>
  );
};

export default BottomNav;
