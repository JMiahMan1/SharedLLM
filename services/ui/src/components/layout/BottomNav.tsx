import { NavLink } from 'react-router-dom';
import { Home, MessageSquare, Music, Settings, Shield } from 'lucide-react';
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

  const role = user?.is_admin ? 'admin' : (user?.role || 'user');

  const navItems = [
    { icon: Home, label: 'Home', path: '/', roles: ['admin', 'user', 'child'] },
    { icon: MessageSquare, label: 'Chat', path: '/communication', roles: ['admin', 'user', 'child'] },
    { icon: Music, label: 'Media', path: '/media', roles: ['admin', 'user', 'child'] },
    ...(role === 'admin'
      ? [{ icon: Shield, label: 'Admin', path: '/admin', roles: ['admin'] as const }]
      : [{ icon: Settings, label: 'Settings', path: '/settings', roles: ['user'] as const }]
    ),
  ];

  const handleTap = () => {
    trigger('light');
  };

  return (
    <nav className="fixed bottom-0 left-0 right-0 z-50 glass-panel border-t border-white/10" style={{ paddingBottom: 'env(safe-area-inset-bottom, 0px)' }}>
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
