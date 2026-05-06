import { useState } from 'react';
import { Search, Bell, LogOut } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../services/api';
import { useAuth } from '../../context/AuthContext';

const Header = () => {
  const { user, logout } = useAuth();
  
  // Real-time widget polling the Gateway’s /health/ready endpoint
  const { data: health, isLoading, error } = useQuery({
    queryKey: ['health'],
    queryFn: () => api.getHealth(),
    refetchInterval: 10000, // Poll every 10 seconds
  });

  const [showNotifications, setShowNotifications] = useState(false);
  const isReady = health?.status === 'READY';
  const statusColor = error ? 'bg-red-500' : (isLoading ? 'bg-yellow-500' : (isReady ? 'bg-green-500' : 'bg-red-400'));
  const statusText = error ? 'Offline' : (isLoading ? 'Polling...' : (health?.status || 'Unknown'));

  return (
    <header className="h-20 flex items-center justify-between px-8 bg-transparent">
      <div className="flex-1 max-w-2xl relative group">
        <div className="absolute inset-y-0 left-4 flex items-center pointer-events-none text-slate-400 group-focus-within:text-purple-400 transition-colors">
          <Search size={20} />
        </div>
        <input 
          type="text" 
          placeholder="Search semantic memory or storage..."
          className="w-full glass-input pl-12 h-12 text-lg"
        />
      </div>

      <div className="flex items-center gap-6">
        <div className="flex items-center gap-2 glass-panel px-4 py-2 text-sm">
          <div className={`w-2 h-2 rounded-full ${statusColor} animate-pulse`} />
          <span className="text-slate-300">System Pulse:</span>
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
            <span className="absolute top-2 right-2 w-2 h-2 bg-pink-500 rounded-full" />
          </button>
          
          {showNotifications && (
            <div className="absolute right-0 mt-4 w-80 glass-panel p-4 z-50 animate-in slide-in-from-top-2 duration-200">
               <div className="flex items-center justify-between mb-4">
                  <h4 className="text-sm font-bold text-white uppercase tracking-widest">Notifications</h4>
                  <span className="text-[10px] text-slate-500">Live Feed</span>
               </div>
               <div className="space-y-3">
                  <div className="p-3 rounded-xl bg-white/5 border border-white/5">
                     <p className="text-xs text-white font-medium">System Update</p>
                     <p className="text-[11px] text-slate-400 mt-1">SharedLLM is currently monitoring all active data providers.</p>
                     <p className="text-[9px] text-slate-600 mt-2">Just now</p>
                  </div>
                  <div className="p-3 rounded-xl bg-white/5 border border-white/5 opacity-60">
                     <p className="text-xs text-white font-medium">RAG Sync Complete</p>
                     <p className="text-[11px] text-slate-400 mt-1">Successfully indexed 24 new documents from Home Assistant.</p>
                     <p className="text-[9px] text-slate-600 mt-2">12 minutes ago</p>
                  </div>
               </div>
               <button className="w-full mt-4 py-2 text-[10px] font-black uppercase tracking-widest text-slate-500 hover:text-white transition-colors border-t border-white/5 pt-4">
                  View All Alerts
               </button>
            </div>
          )}
        </div>

        <div className="flex items-center gap-3">
          <div className="text-right">
            <p className="text-sm font-semibold text-white">{user?.username || 'Guest'}</p>
            <p className="text-xs text-slate-500">{user?.is_admin ? 'Admin' : 'Family Member'}</p>
          </div>
          <button 
            onClick={logout}
            className="w-10 h-10 rounded-full bg-gradient-to-br from-purple-500 to-pink-500 flex items-center justify-center text-white font-bold border border-white/20 hover:scale-110 transition-transform group relative"
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
