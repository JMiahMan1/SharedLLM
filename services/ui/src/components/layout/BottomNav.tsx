import { NavLink } from 'react-router-dom';
import { Brain, Calendar, FolderKanban, Home, MessageSquare, Music, Settings } from 'lucide-react';
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
    { icon: Music, label: 'Media', path: '/media', roles: ['admin', 'user', 'child'] },
    { icon: Calendar, label: 'Calendar', path: '/calendar', roles: ['admin', 'user', 'child'] },
    { icon: MessageSquare, label: 'Chat', path: '/communication', roles: ['admin', 'user', 'child'] },
    { icon: FolderKanban, label: 'Workspaces', path: '/workspaces', roles: ['admin', 'user', 'child'] },
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
    <nav className="fixed bottom-0 left-0 right-0 z-50 glass-panel border-t border-white/10" style={{ paddingBottom: 'env(safe-area-inset-bottom, 0px)' }}>
      <div className="flex items-center justify-around h-16">
        {navItems.map((item) => (
          <NavLink
            key={item.path}
            to={item.path}
            onClick={handleTap}
            className={({ isActive }) =>
              cn(
                'flex flex-col items-center justify-center gap-1 px-2 py-3 min-w-[4rem] flex-1 max-w-[8rem] transition-colors',
                isActive
                  ? 'text-purple-400 neon-glow font-bold'
                  : 'text-slate-500 hover:text-slate-300'
              )
            }
          >
            <item.icon size={20} />
            <span className="text-[9px] font-medium truncate">{item.label}</span>
          </NavLink>
        ))}
      </div>
    </nav>
  );
};

export default BottomNav;
