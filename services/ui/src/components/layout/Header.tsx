import { useState } from 'react';
import { Search, Bell, LogOut, Trash2 } from 'lucide-react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../services/api';
import type { LogEntry } from '../../services/api';
import { useAuth } from '../../context/AuthContext';

const LEVEL_COLOR: Record<string, string> = {
  ERROR: 'text-red-400',
  WARNING: 'text-yellow-400',
  WARN: 'text-yellow-400',
  INFO: 'text-emerald-400',
  DEBUG: 'text-slate-400',
};

const Header = () => {
  const { user, logout } = useAuth();
  
  const { data: health, isLoading, error } = useQuery({
    queryKey: ['health'],
    queryFn: () => api.getHealth(),
    refetchInterval: 10000,
  });

  const { data: notifications = [] } = useQuery<LogEntry[]>({
    queryKey: ['header-notifications'],
    queryFn: () => api.getLogs(5),
    refetchInterval: 15000,
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

  const hasErrors = notifications.some(n => n.level === 'ERROR' || n.level === 'WARNING' || n.level === 'WARN');

  return (
    <header className="h-20 flex items-center justify-between px-4 md:px-8 bg-transparent">
      <div className="hidden md:flex flex-1 max-w-2xl relative group">
        <div className="absolute inset-y-0 left-4 flex items-center pointer-events-none text-slate-400 group-focus-within:text-purple-400 transition-colors">
          <Search size={20} />
        </div>
        <input 
          type="text" 
          placeholder="Search semantic memory or storage..."
          className="w-full glass-input pl-12 h-12 text-lg"
        />
      </div>

      <div className="flex items-center gap-3 md:gap-6 ml-auto md:ml-0">
        <div className="hidden sm:flex items-center gap-2 glass-panel px-4 py-2 text-sm">
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
            {/* Dot color: red if errors/warnings, green if all OK */}
            <span className={`absolute top-2 right-2 w-2 h-2 rounded-full ${hasErrors ? 'bg-red-500' : 'bg-emerald-500'}`} />
          </button>
          
          {showNotifications && (
            <div className="absolute right-0 mt-4 w-80 max-w-[calc(100vw-2rem)] glass-panel p-4 z-50 animate-in slide-in-from-top-2 duration-200">
              <div className="flex items-center justify-between mb-4 border-b border-white/5 pb-3">
                <h4 className="text-sm font-bold text-white uppercase tracking-widest">Activity Feed</h4>
                <button 
                  onClick={handleClearLogs}
                  disabled={notifications.length === 0 || clearMutation.isPending}
                  className="flex items-center gap-1.5 text-[10px] font-black uppercase tracking-widest text-red-400 hover:text-red-300 transition-colors disabled:opacity-30"
                >
                  <Trash2 size={12} />
                  {clearMutation.isPending ? '...' : 'Clear All'}
                </button>
              </div>
              <div className="space-y-2 max-h-72 overflow-y-auto pr-1">
                {notifications.length === 0 ? (
                  <p className="text-xs text-slate-500 text-center py-4">No recent activity</p>
                ) : notifications.map((log, i) => (
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
                className="w-full mt-3 py-2 text-[10px] font-black uppercase tracking-widest text-slate-500 hover:text-white transition-colors border-t border-white/5 pt-3"
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
