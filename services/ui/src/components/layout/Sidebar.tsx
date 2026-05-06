import { NavLink } from 'react-router-dom';
import { 
  LayoutDashboard, 
  Settings, 
  UserCircle, 
  MessageSquare, 
  FlaskConical, 
  Activity,
  HelpCircle
} from 'lucide-react';
import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';
import { useAuth } from '../../context/AuthContext';

function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const navItems = [
  { icon: LayoutDashboard, label: 'Dashboard', path: '/' },
  { icon: Settings, label: 'System Matrix', path: '/admin', adminOnly: true },
  { icon: UserCircle, label: 'Identity', path: '/identity' },
  { icon: MessageSquare, label: 'Communication', path: '/communication' },
  { icon: FlaskConical, label: 'Jarvis Lab', path: '/lab', adminOnly: true },
  { icon: HelpCircle, label: 'Help Hub', path: '/docs' },
];

const Sidebar = () => {
  const { user } = useAuth();
  return (
    <aside className="w-64 glass-panel m-4 mr-0 flex flex-col">
      <div className="p-6">
        <h1 className="text-xl font-bold bg-gradient-to-r from-purple-400 to-pink-400 bg-clip-text text-transparent flex items-center gap-2">
          <Activity className="text-purple-400" />
          Jarvis OS
        </h1>
      </div>
      
      <nav className="flex-1 px-4 space-y-2">
        {navItems
          .filter(item => !item.adminOnly || user?.is_admin)
          .map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              className={({ isActive }) => cn(
                "flex items-center gap-3 px-4 py-3 rounded-xl transition-all duration-200",
                isActive 
                  ? "bg-purple-600/30 text-white border border-purple-500/30 shadow-lg shadow-purple-500/10" 
                  : "text-slate-400 hover:text-white hover:bg-white/5"
              )}
            >
              <item.icon size={20} />
              <span className="font-medium">{item.label}</span>
            </NavLink>
          ))}
      </nav>

      <div className="p-4 mt-auto">
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
