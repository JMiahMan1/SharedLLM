import { motion } from 'framer-motion';
import { 
  ShieldCheck, 
  Database, 
  Cpu, 
  HardDrive, 
  ScrollText, 
  Terminal,
  ArrowUpRight
} from 'lucide-react';

const ServiceCard = ({ name, icon: Icon, color }: any) => (
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
        <span className="text-[10px] uppercase tracking-wider px-2 py-0.5 rounded-full bg-green-500/10 text-green-400 border border-green-500/20">
          Online
        </span>
      </div>
      <p className="text-sm text-slate-400 mt-1">Uptime: 99.9%</p>
    </div>
  </motion.div>
);

const Dashboard = () => {
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
          <ServiceCard name="Identity Service" icon={ShieldCheck} color="blue" />
          <ServiceCard name="RAG Engine" icon={Database} color="purple" />
          <ServiceCard name="Execution Bridge" icon={Cpu} color="orange" />
          <ServiceCard name="Storage Hub" icon={HardDrive} color="emerald" />
          <ServiceCard name="Logging Service" icon={ScrollText} color="pink" />
          <ServiceCard name="Workspace Runtime" icon={Terminal} color="cyan" />
        </div>
      </section>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
        <section className="glass-panel p-8">
          <h2 className="text-xl font-bold text-white mb-6">Family Activity</h2>
          <div className="space-y-4">
            {[1, 2, 3].map((i) => (
              <div key={i} className="flex gap-4 p-4 rounded-xl hover:bg-white/5 transition-colors">
                <div className="w-10 h-10 rounded-full bg-slate-800" />
                <div>
                  <p className="text-sm text-white font-medium">Nextcloud notification</p>
                  <p className="text-xs text-slate-400">New photo shared in "Family Trip" folder</p>
                  <p className="text-[10px] text-slate-500 mt-1">2 hours ago</p>
                </div>
              </div>
            ))}
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
