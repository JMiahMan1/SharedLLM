import { useState, FC } from 'react';
import { motion } from 'framer-motion';
import { 
  Monitor, 
  Lightbulb, 
  Thermometer, 
  Lock, 
  UserPlus,
  ArrowRightLeft,
  Shield,
  Zap,
  MoreVertical,
  Trash2,
  Edit3,
  ShieldCheck,
  User as UserIcon,
  Smartphone,
  Cpu
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../services/api';
import type { UserProfile } from '../services/api';
import toast from 'react-hot-toast';
import HelpTooltip from '../components/ui/HelpTooltip';

interface DeviceCardProps {
  name: string;
  type: string;
  icon: LucideIcon;
  assignedTo?: string | null;
  onAssign: () => void;
}

const DeviceCard: FC<DeviceCardProps> = ({ name, type, icon: Icon, assignedTo, onAssign }) => (
  <motion.div 
    layout
    whileHover={{ scale: 1.02 }}
    className={`glass-card p-4 flex flex-col items-center gap-3 text-center relative group overflow-hidden border-2 transition-all ${assignedTo ? 'border-purple-500/30 bg-purple-500/5' : 'border-white/5 bg-white/5'}`}
  >
    <div className={`p-3 rounded-2xl ${assignedTo ? 'bg-purple-600/20 text-purple-400' : 'bg-slate-800 text-slate-500'} group-hover:scale-110 transition-transform`}>
      <Icon size={20} />
    </div>
    <div className="z-10">
      <p className="text-[11px] font-bold text-white truncate w-24 tracking-tight">{name}</p>
      <p className="text-[9px] text-slate-500 uppercase font-black tracking-widest mt-0.5">{type}</p>
    </div>
    
    <button 
      onClick={() => onAssign()}
      className={`mt-2 w-full py-1.5 rounded-lg text-[9px] font-black uppercase tracking-widest transition-all ${assignedTo ? 'bg-purple-600/20 text-purple-400' : 'bg-white/5 text-slate-500 hover:bg-white/10 hover:text-white'}`}
    >
      {assignedTo ? `Owner: ${assignedTo}` : 'Unassigned'}
    </button>

    {assignedTo && (
      <div className="absolute top-1 right-1">
         <ShieldCheck size={12} className="text-purple-500" />
      </div>
    )}
  </motion.div>
);

interface UserRowProps {
  user: UserProfile;
  onEdit: (u: UserProfile) => void;
  onDelete: (u: UserProfile) => void;
}

const UserRow: FC<UserRowProps> = ({ user, onEdit, onDelete }) => (
  <div className="glass-panel p-5 flex items-center justify-between group hover:border-purple-500/20 transition-all">
    <div className="flex items-center gap-4">
      <div className={`w-12 h-12 rounded-2xl flex items-center justify-center text-lg font-black shadow-lg ${user.is_admin ? 'bg-indigo-600 text-white shadow-indigo-500/20' : 'bg-slate-800 text-slate-400 shadow-black/20'}`}>
        {user.username[0].toUpperCase()}
      </div>
      <div>
        <p className="text-sm font-bold text-white tracking-tight">{user.full_name || user.username}</p>
        <div className="flex items-center gap-2 mt-1">
          <span className={`text-[8px] uppercase font-black px-2 py-0.5 rounded-full border ${user.is_admin ? 'bg-indigo-500/10 text-indigo-400 border-indigo-500/20' : 'bg-slate-800 text-slate-500 border-white/5'}`}>
            {user.is_admin ? 'System Admin' : 'Family Member'}
          </span>
          <span className="text-[8px] text-slate-600 uppercase font-bold">ID: {user.id.slice(0,8)}</span>
        </div>
      </div>
    </div>
    <div className="flex items-center gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
       <button onClick={() => onEdit(user)} className="p-2 hover:bg-white/5 rounded-xl text-slate-500 hover:text-white transition-all"><Edit3 size={16} /></button>
       {!user.is_system_default && (
         <button onClick={() => onDelete(user)} className="p-2 hover:bg-red-500/10 rounded-xl text-slate-500 hover:text-red-400 transition-all"><Trash2 size={16} /></button>
       )}
    </div>
  </div>
);

const Admin = () => {
  const queryClient = useQueryClient();
  const [selectedUserForAssignment, setSelectedUserForAssignment] = useState<UserProfile | null>(null);

  const { data: users } = useQuery<UserProfile[]>({
    queryKey: ['users'],
    queryFn: () => api.getUsers(),
  });

  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const { data: _assignments } = useQuery({
    queryKey: ['device-assignments'],
    queryFn: () => api.getSettings(), 
  });

  const updateAssignmentMutation = useMutation({
    mutationFn: (data: { user_id: string, entity_id: string }) => api.updateDeviceAssignment(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['device-assignments'] });
      toast.success('Matrix synchronization complete');
    },
    onError: () => toast.error('Assignment failed')
  });

  const devices = [
    { id: 'light.kitchen', name: 'Kitchen Main', type: 'LIGHT', icon: Lightbulb },
    { id: 'media_player.living_tv', name: 'Living Room TV', type: 'MEDIA', icon: Monitor },
    { id: 'climate.office', name: 'Office HVAC', type: 'CLIMATE', icon: Thermometer },
    { id: 'lock.front_door', name: 'Front Entrance', type: 'LOCK', icon: Lock },
    { id: 'light.bedroom_l', name: 'Nightstand Left', type: 'LIGHT', icon: Lightbulb },
    { id: 'light.bedroom_r', name: 'Nightstand Right', type: 'LIGHT', icon: Lightbulb },
    { id: 'media_player.kitchen_echo', name: 'Kitchen Echo', type: 'MEDIA', icon: Smartphone },
    { id: 'switch.coffee_maker', name: 'Coffee Pulse', type: 'SWITCH', icon: Zap },
  ];

  const deleteUserMutation = useMutation({
    mutationFn: (username: string) => api.deleteUser(username),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
      toast.success('Member removed from matrix');
    },
    onError: () => toast.error('Termination failed')
  });

  const handleAssign = (deviceId: string) => {
    if (!selectedUserForAssignment) {
      toast('Select a user first to map devices', { icon: '👤' });
      return;
    }
    updateAssignmentMutation.mutate({
      user_id: selectedUserForAssignment.id,
      entity_id: deviceId
    });
  };

  const handleDelete = (user: UserProfile) => {
    if (window.confirm(`Are you sure you want to remove ${user.username}? This cannot be undone.`)) {
      deleteUserMutation.mutate(user.username);
    }
  };

  return (
    <div className="h-full flex flex-col gap-10 pb-12">
      <header className="flex flex-col md:flex-row md:items-end justify-between gap-6">
        <div>
          <h2 className="text-4xl font-black text-white tracking-tighter uppercase">Command Center</h2>
          <p className="text-slate-400 mt-2">Administrative override, system policies, and device matrix orchestration</p>
        </div>
        <div className="flex gap-3">
           <div className="flex items-center gap-2 px-4 py-2 glass-panel border-indigo-500/20 bg-indigo-500/5">
              <Shield size={16} className="text-indigo-400" />
              <span className="text-[10px] font-black uppercase tracking-widest text-indigo-400">Restricted Admin Access</span>
           </div>
        </div>
      </header>

      <div className="grid grid-cols-1 xl:grid-cols-12 gap-10 flex-1 min-h-0">
        <div className="xl:col-span-8 space-y-10 overflow-y-auto pr-2 custom-scrollbar">
          <section>
            <div className="flex items-center justify-between mb-8">
              <h3 className="text-xl font-bold text-white flex items-center gap-3">
                <Cpu size={24} className="text-purple-400" />
                Device Matrix
                <HelpTooltip docName="architecture.md" sectionTitle="Core Components" label="Device Matrix" />
              </h3>
              <div className="flex items-center gap-4">
                 <p className="text-[10px] text-slate-500 uppercase font-black tracking-widest">Active Mapping:</p>
                 <div className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-white/5 border border-white/10 text-[10px] text-white font-bold">
                    {selectedUserForAssignment ? (
                      <>
                        <UserIcon size={12} className="text-purple-400" />
                        Assigning to {selectedUserForAssignment.username}
                      </>
                    ) : 'Select a user →'}
                 </div>
              </div>
            </div>
            
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-6">
              {devices.map(dev => (
                <DeviceCard 
                  key={dev.id} 
                  {...dev} 
                  assignedTo={null} 
                  onAssign={() => handleAssign(dev.id)}
                />
              ))}
            </div>
          </section>

          <section className="glass-panel p-8">
            <h3 className="text-xl font-bold text-white mb-8 flex items-center gap-3">
               <ArrowRightLeft size={24} className="text-orange-400" />
               Audit Trail
            </h3>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="text-slate-500 border-b border-white/5 font-black uppercase text-[10px] tracking-widest">
                    <th className="pb-4">Operator</th>
                    <th className="pb-4">Orchestration</th>
                    <th className="pb-4">Timestamp</th>
                    <th className="pb-4">Response</th>
                  </tr>
                </thead>
                <tbody className="text-slate-300 font-mono text-[11px]">
                  {[1, 2, 3].map((i) => (
                    <tr key={i} className="border-b border-white/5 last:border-0 group hover:bg-white/5 transition-colors">
                      <td className="py-5 font-bold text-white">@jeremiah</td>
                      <td className="py-5 text-indigo-400">SET_DEVICE_OWNER(light.office, user_12)</td>
                      <td className="py-5 text-slate-500">2026-05-05 08:24:12</td>
                      <td className="py-5">
                        <span className="px-3 py-1 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 font-black tracking-tighter">SUCCESS_SYNC</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </div>

        <div className="xl:col-span-4 space-y-10">
          <section className="glass-panel p-8 relative overflow-hidden group">
            <div className="absolute top-0 right-0 w-32 h-32 bg-purple-500/5 rounded-full blur-3xl group-hover:bg-purple-500/10 transition-colors" />
            <div className="flex items-center justify-between mb-8">
              <h3 className="font-bold text-white flex items-center gap-3">
                <UserPlus size={20} className="text-purple-400" />
                Identity Mgmt
              </h3>
              <button className="p-2 hover:bg-white/5 rounded-xl transition-all text-slate-500 hover:text-purple-400"><MoreVertical size={20} /></button>
            </div>
            <div className="space-y-4">
              {users?.map((u) => (
                <div 
                  key={u.id}
                  onClick={() => setSelectedUserForAssignment(u)}
                  className={`cursor-pointer transition-all ${selectedUserForAssignment?.id === u.id ? 'ring-2 ring-purple-500/50 rounded-2xl' : ''}`}
                >
                  <UserRow 
                    user={u} 
                    onEdit={() => toast('Edit feature coming in v1.2', { icon: '⚒️' })} 
                    onDelete={handleDelete} 
                  />
                </div>
              ))}
              <button className="w-full py-5 border-2 border-dashed border-white/10 rounded-2xl text-slate-500 hover:text-white hover:border-white/30 transition-all text-xs font-black uppercase tracking-widest flex items-center justify-center gap-3 bg-black/10">
                <UserPlus size={18} /> Invite New Member
              </button>
            </div>
          </section>

          <section className="glass-panel p-8 bg-blue-900/10 border-blue-500/20 shadow-2xl shadow-blue-500/5">
             <div className="flex items-center gap-3 mb-4">
                <Shield size={20} className="text-blue-400" />
                <h3 className="font-bold text-white text-sm uppercase tracking-widest">Policy Engine</h3>
                <HelpTooltip docName="roadmap.md" sectionTitle="Completed Milestones" label="Policy Engine" />
             </div>
             <p className="text-xs text-slate-400 leading-relaxed italic">
               "Device ownership determines the 'Personal Context' used by the Gateway Intent Engine. When @Alice says 'Turn on my light', the system resolves <code>owner_id = Alice</code> and routes the command to her assigned entities."
             </p>
             <div className="mt-6 pt-6 border-t border-white/5 grid grid-cols-2 gap-4">
                <div className="p-3 bg-black/20 rounded-xl border border-white/5">
                   <p className="text-[9px] text-slate-500 uppercase font-black tracking-widest mb-1">Mesh Status</p>
                   <p className="text-xs text-emerald-400 font-bold tracking-tighter">SYNCHRONIZED</p>
                </div>
                <div className="p-3 bg-black/20 rounded-xl border border-white/5">
                   <p className="text-[9px] text-slate-500 uppercase font-black tracking-widest mb-1">Active Rules</p>
                   <p className="text-xs text-blue-400 font-bold tracking-tighter">24 POLICIES</p>
                </div>
             </div>
          </section>
        </div>
      </div>
    </div>
  );
};

export default Admin;
