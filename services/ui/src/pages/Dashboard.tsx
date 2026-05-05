import { useState, FC, ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import { 
  Shield, 
  Database, 
  Cpu, 
  Server, 
  FileText, 
  ArrowUpRight,
  Activity,
  X,
  Settings,
  Info,
  ExternalLink,
  Search,
  MessageSquare,
  Zap,
  Globe,
  LucideIcon
} from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { api, HealthStatus, LogEntry } from '../services/api';
import { useAuth } from '../context/AuthContext';
import toast from 'react-hot-toast';

interface ModalProps {
  isOpen: boolean;
  onClose: () => void;
  title?: string;
  children: ReactNode;
}

const Modal: FC<ModalProps> = ({ isOpen, onClose, title, children }) => {
  if (!isOpen) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-md">
      <motion.div 
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        exit={{ opacity: 0, scale: 0.95 }}
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

interface ServiceCardProps {
  name: string;
  icon: LucideIcon;
  color: string;
  status?: string;
  onClick: () => void;
}

const ServiceCard: FC<ServiceCardProps> = ({ name, icon: Icon, color, status, onClick }) => (
  <motion.div 
    whileHover={{ scale: 1.02 }}
    onClick={onClick}
    className="glass-card p-6 flex items-start gap-4 cursor-pointer group relative overflow-hidden"
  >
    <div className={`absolute top-0 right-0 w-32 h-32 bg-${color}-500/5 rounded-full blur-3xl -mr-16 -mt-16 group-hover:bg-${color}-500/10 transition-colors`} />
    <div className={`p-3 rounded-xl bg-${color}-500/20 text-${color}-400 border border-${color}-500/20 group-hover:border-${color}-500/50 transition-colors z-10`}>
      <Icon size={24} />
    </div>
    <div className="flex-1 z-10">
      <div className="flex items-center justify-between">
        <h3 className="font-semibold text-white group-hover:text-white transition-colors">{name}</h3>
        <span className={`text-[10px] uppercase tracking-wider px-2 py-0.5 rounded-full border ${
          status === 'OK' 
            ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20' 
            : 'bg-red-500/10 text-red-400 border-red-500/20'
        }`}>
          {status || 'OFFLINE'}
        </span>
      </div>
      <p className="text-sm text-slate-400 mt-1">
        {status === 'OK' ? 'Service Operational' : 'Awaiting Connection...'}
      </p>
    </div>
  </motion.div>
);

interface ServiceDetail {
  label: string;
  value: string;
}

interface ServiceInfo {
  key: string;
  name: string;
  icon: LucideIcon;
  color: string;
  description: string;
  details: ServiceDetail[];
}

interface SearchResult {
  answer?: string;
  files?: { name: string; path: string }[];
}

const Dashboard = () => {
  const [selectedService, setSelectedService] = useState<ServiceInfo | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [isSearching, setIsSearching] = useState(false);
  const [searchResults, setSearchResults] = useState<SearchResult | null>(null);
  const { user } = useAuth();
  const navigate = useNavigate();

  const { data: health } = useQuery<HealthStatus>({
    queryKey: ['health'],
    queryFn: () => api.getHealth(),
    refetchInterval: 5000
  });

  const { data: logs } = useQuery<LogEntry[]>({
    queryKey: ['recent-logs'],
    queryFn: () => api.getLogs(5),
    refetchInterval: 10000
  });

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!searchQuery.trim()) return;
    setIsSearching(true);
    try {
      const results = await api.globalSearch(searchQuery) as SearchResult;
      setSearchResults(results);
    } catch {
      toast.error('Search failed');
    } finally {
      setIsSearching(false);
    }
  };

  const services: ServiceInfo[] = [
    { 
      key: 'identity', 
      name: 'Identity Hub', 
      icon: Shield, 
      color: 'blue',
      description: 'Manages user authentication, service credentials, and security policies.',
      details: [
        { label: 'Database', value: 'SQLite (Encrypted)' },
        { label: 'Auth Engine', value: 'OAuth2 / Bcrypt' },
        { label: 'Vault Status', value: 'Locked' }
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
        { label: 'Index Mode', value: 'Dynamic' }
      ]
    },
    { 
      key: 'execution', 
      name: 'Execution Bridge', 
      icon: Cpu, 
      color: 'orange',
      description: 'Safely executes code and commands in isolated workspace environments.',
      details: [
        { label: 'Runtime', value: 'Python 3.12' },
        { label: 'Isolation', value: 'Docker' },
        { label: 'Permission', value: 'Restricted' }
      ]
    },
    { 
      key: 'storage', 
      name: 'Storage Hub', 
      icon: Server, 
      color: 'emerald',
      description: 'Centralized access to all filesystems, including local drives and cloud mounts.',
      details: [
        { label: 'Mounts', value: '/nextcloud, /shared' },
        { label: 'Sync', value: 'Real-time' },
        { label: 'FS Type', value: 'FUSE' }
      ]
    },
    { 
      key: 'gateway', 
      name: 'Dynamic Gateway', 
      icon: Globe, 
      color: 'indigo',
      description: 'Orchestrates intent routing and tool invocation across the microservice mesh.',
      details: [
        { label: 'Intent Engine', value: 'LLM-Guided' },
        { label: 'Routing', value: 'Context-Aware' },
        { label: 'Fast-Path', value: 'Enabled' }
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
  ];

  return (
    <div className="space-y-8 pb-12">
      <header className="flex flex-col md:flex-row md:items-center justify-between gap-6">
        <div>
          <h2 className="text-3xl font-bold text-white tracking-tight">Nexus Dashboard</h2>
          <p className="text-slate-400 mt-1">Welcome back, <span className="text-purple-400 font-bold">{user?.full_name || user?.username}</span></p>
        </div>
        
        <form onSubmit={handleSearch} className="relative w-full max-w-xl group">
          <div className="absolute inset-y-0 left-0 pl-4 flex items-center pointer-events-none">
            <Search className="text-slate-500 group-focus-within:text-purple-400 transition-colors" size={18} />
          </div>
          <input 
            type="text" 
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search RAG context, Nextcloud files, or ask Jarvis..."
            className="glass-input w-full pl-12 py-3 bg-white/5 border-white/10 group-hover:border-white/20 focus:border-purple-500/50"
          />
          <button 
            type="submit"
            disabled={isSearching}
            className="absolute right-2 top-1.5 glass-button px-4 py-1.5 bg-purple-600/40 text-xs font-bold"
          >
            {isSearching ? '...' : <ArrowUpRight size={16} />}
          </button>
        </form>
      </header>

      {searchResults && (
        <motion.section 
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          className="glass-panel p-6 border-indigo-500/20 bg-indigo-500/5"
        >
          <div className="flex items-center justify-between mb-4">
             <h3 className="font-bold text-indigo-400 flex items-center gap-2">
                <Zap size={18} />
                Nexus Intelligence Results
             </h3>
             <button onClick={() => setSearchResults(null)} className="text-slate-500 hover:text-white">
                <X size={16} />
             </button>
          </div>
          <div className="space-y-4">
             {searchResults.answer && (
               <div className="text-sm text-slate-300 leading-relaxed bg-black/20 p-4 rounded-xl border border-white/5">
                 {searchResults.answer}
               </div>
             )}
             <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {searchResults.files?.map((file, i: number) => (
                  <div key={i} className="flex items-center gap-3 p-3 glass-card text-xs">
                     <FileText size={14} className="text-blue-400" />
                     <div className="flex-1 truncate">
                        <p className="text-white font-medium truncate">{file.name}</p>
                        <p className="text-slate-500 text-[10px]">{file.path}</p>
                     </div>
                     <ExternalLink size={12} className="text-slate-500" />
                  </div>
                ))}
             </div>
          </div>
        </motion.section>
      )}

      <section>
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-xl font-bold text-white flex items-center gap-2">
             <Activity size={20} className="text-purple-400" />
             System Pulse
          </h2>
          <div className="flex items-center gap-2 text-[10px] font-bold text-slate-500 uppercase tracking-widest">
             <span className="w-2 h-2 rounded-full bg-emerald-500 animate-ping" />
             Live Connectivity
          </div>
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
            <MessageSquare size={20} className="text-blue-400" />
            Recent Activity
          </h2>
          <div className="space-y-4">
            {logs?.map((log: LogEntry) => (
              <div key={log.id} className="flex gap-4 p-4 rounded-xl hover:bg-white/5 transition-colors border border-white/5 group">
                <div className={`p-2 rounded-lg bg-slate-800 text-slate-400 group-hover:text-purple-400 transition-colors`}>
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
              <div className="flex flex-col items-center justify-center py-12 text-slate-500 gap-2">
                <Activity size={32} className="animate-pulse opacity-20" />
                <p className="text-sm italic">Listening for system events...</p>
              </div>
            )}
          </div>
        </section>

        <section className="glass-panel p-8 bg-gradient-to-br from-purple-900/10 to-transparent relative overflow-hidden group">
          <div className="absolute -right-20 -bottom-20 w-64 h-64 bg-purple-500/5 rounded-full blur-[100px] group-hover:bg-purple-500/10 transition-colors" />
          <h2 className="text-xl font-bold text-white mb-6 flex items-center gap-2">
             <Zap size={20} className="text-orange-400" />
             Automation Pulse
          </h2>
          <div className="flex flex-col items-center justify-center h-48 text-center relative z-10">
            <div className="w-32 h-32 rounded-full border-2 border-dashed border-purple-500/30 flex items-center justify-center animate-spin-slow">
               <Cpu size={48} className="text-purple-500/30" />
            </div>
            <p className="mt-6 text-slate-400 font-mono text-[10px] uppercase tracking-widest">Awaiting Trigger...</p>
          </div>
          <div className="mt-4 p-4 glass-card border-orange-500/10 bg-orange-500/5 text-[10px] text-orange-200/50 italic text-center">
             "Jarvis is monitoring your Home Assistant entities for behavioral patterns."
          </div>
        </section>
      </div>

      <AnimatePresence>
        {selectedService && (
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
                  <h4 className="font-bold text-white uppercase text-xs tracking-widest">Service Overview</h4>
                </div>
                <p className="text-slate-400 text-sm leading-relaxed mb-6">
                  {selectedService?.description}
                </p>
                <div className="grid grid-cols-2 gap-4">
                  {selectedService?.details.map((d: ServiceDetail, i: number) => (
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
                  <h4 className="font-bold text-white uppercase text-xs tracking-widest">Management</h4>
                </div>
                
                <div className="space-y-4">
                  <div className="p-4 glass-card bg-purple-500/5 flex items-center justify-between border-purple-500/20">
                      <div>
                        <p className="text-xs font-bold text-white">Identity Integration</p>
                        <p className="text-[10px] text-slate-400 mt-0.5">Sensitive credentials managed in secure vault.</p>
                      </div>
                      <button 
                        onClick={() => {
                          setSelectedService(null);
                          navigate('/identity');
                        }}
                        className="glass-button text-[10px] py-1.5 px-3 flex items-center gap-2"
                      >
                        Open Vault <ExternalLink size={12} />
                      </button>
                  </div>
                  
                  <div className="grid gap-3">
                    <div className="flex items-center justify-between p-4 bg-white/5 rounded-xl border border-white/5">
                        <div>
                          <p className="text-xs font-bold text-white">Log Level</p>
                          <p className="text-[10px] text-slate-500 mt-1">Affects all SOA service nodes</p>
                        </div>
                        <select 
                          className="glass-input text-[10px] py-1 px-2 h-8 w-24 bg-slate-900 border-white/10"
                          defaultValue="INFO"
                          onChange={(e) => toast.success(`Log level set to ${e.target.value}`)}
                        >
                          <option>DEBUG</option>
                          <option>INFO</option>
                          <option>WARN</option>
                          <option>ERROR</option>
                        </select>
                    </div>

                    <div className="flex items-center justify-between p-4 bg-white/5 rounded-xl border border-white/5">
                        <div>
                          <p className="text-xs font-bold text-white">Maintenance Mode</p>
                          <p className="text-[10px] text-slate-500 mt-1">Suspend all write operations</p>
                        </div>
                        <label className="relative inline-flex items-center cursor-pointer">
                          <input type="checkbox" className="sr-only peer" />
                          <div className="w-10 h-5 bg-slate-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full rtl:peer-checked:after:-translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:start-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-purple-600"></div>
                        </label>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </Modal>
        )}
      </AnimatePresence>
    </div>
  );
};

export default Dashboard;
