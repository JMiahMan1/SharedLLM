import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { 
  Shield, 
  Database, 
  Cpu, 
  Server, 
  FileText, 
  Terminal,
  ArrowUpRight,
  Activity,
  X,
  Settings,
  Info,
  ExternalLink
} from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../services/api';

const Modal = ({ isOpen, onClose, title, children }: any) => {
  if (!isOpen) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm">
      <motion.div 
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        className="glass-panel w-full max-w-2xl max-h-[90vh] overflow-hidden flex flex-col"
      >
        <div className="p-6 border-b border-white/5 flex items-center justify-between">
          <h3 className="text-xl font-bold text-white">{title}</h3>
          <button onClick={onClose} className="text-slate-400 hover:text-white transition-colors">
            <X size={24} />
          </button>
        </div>
        <div className="p-6 overflow-y-auto flex-1">
          {children}
        </div>
      </motion.div>
    </div>
  );
};

const ServiceCard = ({ name, icon: Icon, color, status, onClick }: any) => (
  <motion.div 
    whileHover={{ scale: 1.02 }}
    onClick={onClick}
    className="glass-card p-6 flex items-start gap-4 cursor-pointer group"
  >
    <div className={`p-3 rounded-xl bg-${color}-500/20 text-${color}-400 border border-${color}-500/20 group-hover:border-${color}-500/50 transition-colors`}>
      <Icon size={24} />
    </div>
    <div className="flex-1">
      <div className="flex items-center justify-between">
        <h3 className="font-semibold text-white group-hover:text-purple-400 transition-colors">{name}</h3>
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
  const [selectedService, setSelectedService] = useState<any>(null);

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
    { 
      key: 'identity', 
      name: 'Identity Service', 
      icon: Shield, 
      color: 'blue',
      description: 'Manages user authentication, service credentials, and security policies.',
      details: [
        { label: 'Database', value: 'SQLite (Encrypted)' },
        { label: 'Auth Engine', value: 'OAuth2 / Bcrypt' },
        { label: 'Discovery', value: 'Active (Home Assistant, Nextcloud)' }
      ]
    },
    { 
      key: 'rag', 
      name: 'RAG Engine', 
      icon: Database, 
      color: 'purple',
      description: 'The semantic brain of Jarvis. Indexes workspaces and provides contextual retrieval.',
      details: [
        { label: 'Vector Store', value: 'ChromaDB' },
        { label: 'Embedding Model', value: 'text-embedding-3-small' },
        { label: 'Index Mode', value: 'Dynamic / Auto-Sync' }
      ]
    },
    { 
      key: 'execution', 
      name: 'Execution Bridge', 
      icon: Cpu, 
      color: 'orange',
      description: 'Safely executes code and commands in isolated workspace environments.',
      details: [
        { label: 'Runtime', value: 'Python 3.12 (Isolated)' },
        { label: 'Isolation', value: 'Docker Containerized' },
        { label: 'Security', value: 'ReadOnly (System Files)' }
      ]
    },
    { 
      key: 'storage', 
      name: 'Storage Hub', 
      icon: Server, 
      color: 'emerald',
      description: 'Centralized access to all filesystems, including local drives and cloud mounts.',
      details: [
        { label: 'Mounts', value: '/nextcloud, /local_storage' },
        { label: 'Protocol', value: 'FUSE / WebDAV' },
        { label: 'Sync Status', value: 'Real-time' }
      ]
    },
    { 
      key: 'logging', 
      name: 'Logging Service', 
      icon: FileText, 
      color: 'pink',
      description: 'Aggregates audit logs and telemetry from all system services.',
      details: [
        { label: 'Backend', value: 'Redis Streams' },
        { label: 'Retention', value: '30 Days' },
        { label: 'Log Level', value: 'INFO' }
      ]
    },
    { 
      key: 'workspace_runtime', 
      name: 'Workspace Runtime', 
      icon: Terminal, 
      color: 'cyan',
      description: 'Provides the active context for RAG operations and code execution.',
      details: [
        { label: 'Active Workspaces', value: '1' },
        { label: 'Base Path', value: '/home/jeremiah/SharedLLM' },
        { label: 'State', value: 'Synchronized' }
      ]
    },
  ];

  return (
    <div className="space-y-8">
      <section>
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-2xl font-bold text-white">System Overview</h2>
          <button className="text-sm text-purple-400 hover:text-purple-300 flex items-center gap-1">
            System Diagnostics <ArrowUpRight size={16} />
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
              onClick={() => setSelectedService(s)}
            />
          ))}
        </div>
      </section>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
        <section className="glass-panel p-8">
          <h2 className="text-xl font-bold text-white mb-6 flex items-center gap-2">
            <Activity size={20} className="text-purple-400" />
            Recent Activity
          </h2>
          <div className="space-y-4">
            {logs?.map((log: any) => (
              <div key={log.id} className="flex gap-4 p-4 rounded-xl hover:bg-white/5 transition-colors border border-white/5">
                <div className={`p-2 rounded-lg bg-slate-800 text-slate-400`}>
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
            <p className="mt-6 text-slate-400 font-mono text-xs uppercase tracking-widest">Awaiting Trigger...</p>
          </div>
        </section>
      </div>

      <Modal 
        isOpen={!!selectedService} 
        onClose={() => setSelectedService(null)}
        title={selectedService?.name}
      >
        <div className="space-y-8">
          <div>
            <div className="flex items-center gap-3 mb-4">
              <div className={`p-2 rounded-lg bg-${selectedService?.color}-500/20 text-${selectedService?.color}-400`}>
                <Info size={20} />
              </div>
              <h4 className="font-bold text-white uppercase text-xs tracking-widest">Service Details</h4>
            </div>
            <p className="text-slate-400 text-sm leading-relaxed mb-6">
              {selectedService?.description}
            </p>
            <div className="grid grid-cols-2 gap-4">
              {selectedService?.details.map((d: any, i: number) => (
                <div key={i} className="p-4 bg-white/5 rounded-xl border border-white/5">
                  <p className="text-[10px] text-slate-500 uppercase font-bold mb-1">{d.label}</p>
                  <p className="text-xs text-white font-mono">{d.value}</p>
                </div>
              ))}
            </div>
          </div>

          <div className="pt-8 border-t border-white/5">
            <div className="flex items-center gap-3 mb-6">
              <div className={`p-2 rounded-lg bg-purple-500/20 text-purple-400`}>
                <Settings size={20} />
              </div>
              <h4 className="font-bold text-white uppercase text-xs tracking-widest">System Configuration</h4>
            </div>
            
            <div className="space-y-4">
               <div className="p-4 glass-card bg-purple-500/5 flex items-center justify-between border-purple-500/20">
                  <div>
                    <p className="text-xs font-bold text-white">Advanced Settings</p>
                    <p className="text-[10px] text-slate-400 mt-0.5">Configuration for this service is now managed in the Identity Hub.</p>
                  </div>
                  <button className="glass-button text-[10px] py-1.5 px-3 flex items-center gap-2">
                    Open Hub <ExternalLink size={12} />
                  </button>
               </div>
               
               <div className="grid gap-3">
                 <div className="flex items-center justify-between p-3 bg-white/5 rounded-lg">
                    <span className="text-xs text-slate-400 italic">Log Level</span>
                    <span className="text-xs text-emerald-400 font-mono">INFO</span>
                 </div>
                 <div className="flex items-center justify-between p-3 bg-white/5 rounded-lg">
                    <span className="text-xs text-slate-400 italic">Health Status</span>
                    <span className="text-xs text-emerald-400 font-mono uppercase">Operational</span>
                 </div>
               </div>
            </div>
          </div>
        </div>
      </Modal>
    </div>
  );
};

export default Dashboard;
