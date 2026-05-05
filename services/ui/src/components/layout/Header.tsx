import { Search, Bell } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../services/api';

const Header = () => {
  // Real-time widget polling the Gateway’s /health/ready endpoint
  const { data: health, isLoading, error } = useQuery({
    queryKey: ['health'],
    queryFn: () => api.getHealth(),
    refetchInterval: 10000, // Poll every 10 seconds
  });

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

        <button className="glass-card p-3 relative">
          <Bell size={20} className="text-slate-300" />
          <span className="absolute top-2 right-2 w-2 h-2 bg-pink-500 rounded-full" />
        </button>

        <div className="flex items-center gap-3">
          <div className="text-right">
            <p className="text-sm font-semibold text-white">Admin User</p>
            <p className="text-xs text-slate-500">Root Access</p>
          </div>
          <div className="w-10 h-10 rounded-full bg-gradient-to-br from-purple-500 to-pink-500 flex items-center justify-center text-white font-bold border border-white/20">
            A
          </div>
        </div>
      </div>
    </header>
  );
};

export default Header;
