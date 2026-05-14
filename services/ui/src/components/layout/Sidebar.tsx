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
  Boxes
} from 'lucide-react';
import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';
import { useAuth } from '../../context/AuthContext';

function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const navItems = [
  { icon: LayoutDashboard, label: 'Dashboard', path: '/' },
  { icon: Boxes, label: 'My Workspaces', path: '/workspaces' },
  { icon: UserCircle, label: 'Identity', path: '/identity' },
  { icon: MessageSquare, label: 'Communication', path: '/communication' },
  { icon: Database, label: 'Knowledge Hub', path: '/knowledge' },
  { icon: HelpCircle, label: 'Help Hub', path: '/docs' },
  // Admin-only: System Ops & Raven
  { icon: Settings, label: 'System Ops & Raven', path: '/admin', adminOnly: true },
  { icon: FlaskConical, label: 'Jarvis Lab', path: '/lab', adminOnly: true },
];

const Sidebar = () => {
  const { user } = useAuth();
  return (
    <aside className="w-20 md:w-64 glass-panel m-2 md:m-4 md:mr-0 flex flex-col transition-all duration-300">
      <div className="p-4 md:p-6 flex justify-center md:justify-start">
        <h1 className="text-xl font-bold bg-gradient-to-r from-purple-400 to-pink-400 bg-clip-text text-transparent flex items-center gap-2">
          <Activity className="text-purple-400" />
          <span className="hidden md:inline">Jarvis OS</span>
        </h1>
      </div>
      
      <nav className="flex-1 px-2 md:px-4 space-y-2">
        {navItems
          .filter(item => !item.adminOnly || user?.is_admin)
          .map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              className={({ isActive }) => cn(
                "flex items-center justify-center md:justify-start gap-3 px-3 md:px-4 py-3 rounded-xl transition-all duration-200",
                isActive 
                  ? "bg-purple-600/30 text-white border border-purple-500/30 shadow-lg shadow-purple-500/10" 
                  : "text-slate-400 hover:text-white hover:bg-white/5"
              )}
              title={item.label}
            >
              <item.icon size={20} className="shrink-0" />
              <span className="font-medium hidden md:inline">{item.label}</span>
            </NavLink>
          ))}
      </nav>

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

export default Sidebar;
