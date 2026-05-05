import { motion } from 'framer-motion';
import { 
  ShieldCheck, 
  Database, 
  Cpu, 
  HardDrive, 
  ScrollText, 
  Terminal,
  ArrowUpRight,
  Activity
} from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../services/api';

const ServiceCard = ({ name, icon: Icon, color, status }: { name: string, icon: any, color: string, status?: string }) => (
  <motion.div 
    whileHover={{ scale: 1.02 }}
    className="glass-card p-6 flex items-start gap-4"
  >
    <div className={`p-3 rounded-xl bg-${color}-500/20 text-${color}-400 border border-${color}-500/20`}>
      <Icon size={24} />
    </div>
    <div className="flex-1">
      <div className="flex items-center justify-between">
        <h3 className="font-semibold text-white">{name}</h3>
        <span className={`text-[10px] uppercase tracking-wider px-2 py-0.5 rounded-full border ${
          status === 'OK' 
            ? 'bg-green-500/10 text-green-400 border-green-500/20' 
            : 'bg-red-500/10 text-red-400 border-red-500/20'
        }`}>
          {status || 'Unknown'}
        </span>
      </div>
      <p className="text-sm text-slate-400 mt-1">Uptime: {status === 'OK' ? '99.9%' : '0%'}</p>
    </div>
  </motion.div>
);

const Dashboard = () => {
  const { data: health } = useQuery({
    queryKey: ['health'],
    queryFn: () => api.getHealth(),
    refetchInterval: 5000
  });

  const { data: logs } = useQuery({
    queryKey: ['recent-logs'],
    queryFn: () => api.getLogs(5),
    refetchInterval: 10000
  });

  const services = [
    { key: 'identity', name: 'Identity Service', icon: ShieldCheck, color: 'blue' },
    { key: 'rag', name: 'RAG Engine', icon: Database, color: 'purple' },
    { key: 'execution', name: 'Execution Bridge', icon: Cpu, color: 'orange' },
    { key: 'storage', name: 'Storage Hub', icon: HardDrive, color: 'emerald' },
    { key: 'logging', name: 'Logging Service', icon: ScrollText, color: 'pink' },
    { key: 'workspace_runtime', name: 'Workspace Runtime', icon: Terminal, color: 'cyan' },
  ];

  return (
    <div className="space-y-8">
      <section>
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-2xl font-bold text-white">System Overview</h2>
          <button className="text-sm text-purple-400 hover:text-purple-300 flex items-center gap-1">
            View Details <ArrowUpRight size={16} />
          </button>
        </div>
        
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {services.map(s => (
            <ServiceCard 
              key={s.key} 
              name={s.name} 
              icon={s.icon} 
              color={s.color} 
              status={health?.services[s.key]}
            />
          ))}
        </div>
      </section>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
        <section className="glass-panel p-8">
          <h2 className="text-xl font-bold text-white mb-6">Recent Activity</h2>
          <div className="space-y-4">
            {logs?.map((log) => (
              <div key={log.id} className="flex gap-4 p-4 rounded-xl hover:bg-white/5 transition-colors border border-white/5">
                <div className="p-2 rounded-lg bg-slate-800 text-slate-400">
                  <Activity size={16} />
                </div>
                <div className="flex-1">
                  <div className="flex justify-between items-start">
                    <p className="text-sm text-white font-medium">{log.service}</p>
                    <span className="text-[10px] text-slate-500">{new Date(log.timestamp).toLocaleTimeString()}</span>
                  </div>
                  <p className="text-xs text-slate-400 truncate max-w-xs">{log.message}</p>
                </div>
              </div>
            )) || (
              <p className="text-sm text-slate-500 italic">Listening for system events...</p>
            )}
          </div>
        </section>

        <section className="glass-panel p-8 bg-gradient-to-br from-purple-900/20 to-transparent">
          <h2 className="text-xl font-bold text-white mb-6">Automation Pulse</h2>
          <div className="flex flex-col items-center justify-center h-48 text-center">
            <div className="w-32 h-32 rounded-full border-4 border-dashed border-purple-500/30 flex items-center justify-center animate-spin-slow">
               <Cpu size={48} className="text-purple-500/50" />
            </div>
            <p className="mt-6 text-slate-400">Waiting for HA event triggers...</p>
          </div>
        </section>
      </div>
    </div>
  );
};

export default Dashboard;
