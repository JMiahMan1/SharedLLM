import { motion } from 'framer-motion';
import { 
  Monitor, 
  Lightbulb, 
  Thermometer, 
  Lock, 
  UserPlus,
  ArrowRightLeft
} from 'lucide-react';

const DeviceCard = ({ name, type, icon: Icon }: any) => (
  <motion.div 
    drag
    dragConstraints={{ left: 0, right: 0, top: 0, bottom: 0 }}
    dragElastic={0.1}
    whileDrag={{ scale: 1.05, zIndex: 50 }}
    className="glass-card p-4 cursor-grab active:cursor-grabbing flex flex-col items-center gap-3 text-center"
  >
    <div className="p-3 rounded-full bg-white/5 text-slate-300">
      <Icon size={20} />
    </div>
    <div>
      <p className="text-xs font-semibold text-white truncate w-24">{name}</p>
      <p className="text-[10px] text-slate-500 uppercase">{type}</p>
    </div>
  </motion.div>
);

const UserBubble = ({ name }: { name: string }) => (
  <div className="flex items-center gap-3 p-3 glass-card bg-purple-500/10 border-purple-500/20">
    <div className="w-8 h-8 rounded-full bg-purple-600 flex items-center justify-center text-xs font-bold">
      {name[0]}
    </div>
    <span className="text-sm font-medium">{name}</span>
    <div className="ml-auto text-[10px] text-slate-500">Drop here</div>
  </div>
);

const Admin = () => {
  return (
    <div className="h-full flex flex-col gap-8">
      <header>
        <h2 className="text-2xl font-bold text-white">Command Center</h2>
        <p className="text-slate-400">Manage device assignments and system policy</p>
      </header>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8 flex-1">
        <div className="lg:col-span-2 space-y-6">
          <div className="glass-panel p-6">
            <div className="flex items-center justify-between mb-6">
              <h3 className="font-bold text-white flex items-center gap-2">
                <Monitor size={18} className="text-blue-400" />
                Device Matrix
              </h3>
              <span className="text-xs text-slate-500">Drag to assign</span>
            </div>
            
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-4">
              <DeviceCard name="Kitchen Light" type="light" icon={Lightbulb} />
              <DeviceCard name="Living TV" type="media" icon={Monitor} />
              <DeviceCard name="Office Stat" type="climate" icon={Thermometer} />
              <DeviceCard name="Front Door" type="lock" icon={Lock} />
              <DeviceCard name="Bed Light L" type="light" icon={Lightbulb} />
              <DeviceCard name="Bed Light R" type="light" icon={Lightbulb} />
              <DeviceCard name="Hallway" type="light" icon={Lightbulb} />
              <DeviceCard name="Garage" type="lock" icon={Lock} />
            </div>
          </div>

          <div className="glass-panel p-6">
            <h3 className="font-bold text-white mb-4 flex items-center gap-2">
               <ArrowRightLeft size={18} className="text-orange-400" />
               Recent Assignments
            </h3>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="text-slate-500 border-b border-white/5">
                    <th className="pb-3 font-medium">User</th>
                    <th className="pb-3 font-medium">Device</th>
                    <th className="pb-3 font-medium">Time</th>
                    <th className="pb-3 font-medium">Status</th>
                  </tr>
                </thead>
                <tbody className="text-slate-300">
                  {[1, 2].map((i) => (
                    <tr key={i} className="border-b border-white/5 last:border-0">
                      <td className="py-4">Jeremiah</td>
                      <td className="py-4 font-mono text-xs">light.office_desk</td>
                      <td className="py-4 text-xs">Just now</td>
                      <td className="py-4">
                        <span className="px-2 py-0.5 rounded-full bg-blue-500/10 text-blue-400 border border-blue-500/20 text-[10px]">Active</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        <div className="space-y-6">
          <div className="glass-panel p-6 flex flex-col gap-6">
            <h3 className="font-bold text-white flex items-center gap-2">
              <UserPlus size={18} className="text-purple-400" />
              Users
            </h3>
            <div className="space-y-3">
              <UserBubble name="Jeremiah" />
              <UserBubble name="Alice" />
              <UserBubble name="Bob (Kid)" />
              <button className="w-full py-3 border border-dashed border-white/10 rounded-xl text-slate-500 hover:text-white hover:border-white/30 transition-all text-sm">
                + Add User
              </button>
            </div>
          </div>

          <div className="glass-panel p-6 bg-blue-900/10 border-blue-500/20">
             <h3 className="font-bold text-white mb-2">Policy Note</h3>
             <p className="text-xs text-slate-400 leading-relaxed">
               Device assignments directly update the <code>DeviceAssignment</code> table in the Identity Service, affecting intent resolution.
             </p>
          </div>
        </div>
      </div>
    </div>
  );
};

export default Admin;
