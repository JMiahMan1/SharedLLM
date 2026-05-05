import { useState } from 'react';
import { 
  Cloud, 
  GitBranch, 
  Music, 
  Mic, 
  FolderLock,
  Eye,
  EyeOff,
  ChevronRight,
  UserPlus,
  RefreshCcw,
  Shield,
  Key
} from 'lucide-react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../services/api';
import { useAuth } from '../context/AuthContext';

const UserManagement = () => {
  const { user: currentUser } = useAuth();
  const queryClient = useQueryClient();
  const [newPassword, setNewPassword] = useState('');
  const [isChangingPassword, setIsChangingPassword] = useState(false);

  const { data: users, isLoading } = useQuery({
    queryKey: ['users'],
    queryFn: () => api.getLogs(50).then(() => fetch('/api/users', { headers: { 'Authorization': `Bearer ${localStorage.getItem('jarvis_api_key')}` }}).then(r => r.json())),
    enabled: !!currentUser?.is_admin
  });

  const discoverMutation = useMutation({
    mutationFn: () => api.discoverUsers(),
    onSuccess: (data) => {
      console.log('Discovered users:', data);
    }
  });

  const changePasswordMutation = useMutation({
    mutationFn: (pwd: string) => api.changePassword(pwd),
    onSuccess: () => {
      setIsChangingPassword(false);
      setNewPassword('');
      alert('Password updated successfully');
    }
  });

  if (!currentUser?.is_admin) return null;

  return (
    <section className="glass-panel p-8">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h3 className="text-xl font-bold text-white flex items-center gap-2">
            <Shield size={22} className="text-purple-400" />
            User Management
          </h3>
          <p className="text-sm text-slate-400 mt-1">Manage family members and system access</p>
        </div>
        <div className="flex gap-2">
          <button 
            onClick={() => discoverMutation.mutate()}
            disabled={discoverMutation.isPending}
            className="glass-button text-xs py-2 px-4 flex items-center gap-2"
          >
            <RefreshCcw size={14} className={discoverMutation.isPending ? 'animate-spin' : ''} />
            {discoverMutation.isPending ? 'Scanning...' : 'Discover Users'}
          </button>
          <button className="glass-button text-xs py-2 px-4 flex items-center gap-2 bg-purple-600/40 border-purple-500/30">
            <UserPlus size={14} /> Add User
          </button>
        </div>
      </div>

      <div className="space-y-4">
        {isLoading ? (
          <p className="text-slate-500 italic animate-pulse">Loading system users...</p>
        ) : (
          users?.map((u: any) => (
            <div key={u.id} className="flex items-center justify-between p-4 glass-card group">
              <div className="flex items-center gap-4">
                <div className={`w-10 h-10 rounded-full flex items-center justify-center font-bold border ${
                  u.is_system_default ? 'bg-purple-500/20 text-purple-400 border-purple-500/30' : 'bg-blue-500/20 text-blue-400 border-blue-500/30'
                }`}>
                  {u.username[0].toUpperCase()}
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <span className="font-semibold text-white">{u.display_name || u.username}</span>
                    {u.is_admin && <span className="text-[10px] bg-purple-500/20 text-purple-400 px-1.5 py-0.5 rounded border border-purple-500/20">Admin</span>}
                  </div>
                  <p className="text-[10px] text-slate-500 uppercase tracking-wider">{u.is_system_default ? 'System Default' : 'Family Member'}</p>
                </div>
              </div>
              <button className="text-xs text-slate-500 hover:text-white transition-colors">Edit Settings</button>
            </div>
          ))
        )}
      </div>

      <div className="mt-8 pt-8 border-t border-white/5">
        <h4 className="text-sm font-bold text-white mb-4 flex items-center gap-2">
          <Key size={16} className="text-yellow-400" />
          Security
        </h4>
        <div className="flex gap-4">
          <input 
            type="password"
            placeholder="New password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            className="glass-input flex-1 text-sm"
          />
          <button 
            onClick={() => changePasswordMutation.mutate(newPassword)}
            disabled={!newPassword || changePasswordMutation.isPending}
            className="glass-button px-6 bg-yellow-500/20 text-yellow-400 border-yellow-500/30 hover:bg-yellow-500/30 disabled:opacity-50"
          >
            Update Password
          </button>
        </div>
      </div>
    </section>
  );
};

const IntegrationTile = ({ name, icon: Icon, color, connected }: any) => (
  <div className="glass-card p-6 flex flex-col gap-4 group">
    <div className="flex items-center justify-between">
      <div className={`p-3 rounded-xl bg-${color}-500/20 text-${color}-400 border border-${color}-500/20 group-hover:scale-110 transition-transform`}>
        <Icon size={24} />
      </div>
      <span className={`text-[10px] uppercase font-bold px-2 py-1 rounded-md ${connected ? 'bg-green-500/10 text-green-400' : 'bg-red-500/10 text-red-400'}`}>
        {connected ? 'Connected' : 'Disconnected'}
      </span>
    </div>
    <div>
      <h3 className="font-bold text-white">{name}</h3>
      <p className="text-xs text-slate-500 mt-1">AES-256 Encrypted</p>
    </div>
    <button className="glass-button w-full text-xs mt-2">
      {connected ? 'Manage' : 'Connect'} <ChevronRight size={14} />
    </button>
  </div>
);

const Identity = () => {
  const { user } = useAuth();

  return (
    <div className="space-y-12">
      <header className="flex justify-between items-end">
        <div>
          <h2 className="text-3xl font-bold text-white tracking-tight">Identity Hub</h2>
          <p className="text-slate-400 mt-2">Manage your personal credentials and system access</p>
        </div>
        <div className="text-right">
          <p className="text-[10px] text-slate-500 uppercase tracking-widest font-bold">Logged in as</p>
          <p className="text-purple-400 font-mono font-bold">{user?.username}</p>
        </div>
      </header>

      {user?.is_admin && <UserManagement />}

      <section>
        <h3 className="text-xl font-bold text-white mb-6">Service Integrations</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          <IntegrationTile name="Nextcloud" icon={Cloud} color="blue" connected={true} />
          <IntegrationTile name="GitHub" icon={GitBranch} color="slate" connected={true} />
          <IntegrationTile name="Audiobookshelf" icon={Music} color="orange" connected={false} />
        </div>
      </section>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
        <section className="glass-panel p-8">
          <div className="flex items-center justify-between mb-6">
            <h3 className="font-bold text-white flex items-center gap-2">
              <Mic size={18} className="text-pink-400" />
              Voice Persona Gallery
            </h3>
          </div>
          <div className="grid grid-cols-3 gap-4">
            {[
              { id: 'v1', name: 'Standard', color: 'purple' },
              { id: 'v2', name: 'Soft', color: 'blue' },
              { id: 'v3', name: 'Energetic', color: 'pink' }
            ].map((v) => (
              <div key={v.id} className="p-4 glass-card text-center cursor-pointer border-purple-500/0 hover:border-purple-500/50">
                <div className={`w-12 h-12 rounded-full bg-${v.color}-500/20 mx-auto mb-3 flex items-center justify-center text-${v.color}-400`}>
                   <Mic size={20} />
                </div>
                <p className="text-xs font-medium text-white">{v.name}</p>
              </div>
            ))}
          </div>
          <div className="mt-6 p-4 bg-white/5 rounded-xl">
             <label className="text-[10px] text-slate-500 uppercase block mb-2">Active Voice ID mapping</label>
             <code className="text-xs text-purple-300">elevenlabs://standard_v1_jeremiah</code>
          </div>
        </section>

        <section className="glass-panel p-8">
          <h3 className="font-bold text-white mb-6 flex items-center gap-2">
            <FolderLock size={18} className="text-emerald-400" />
            Memory Toggles
          </h3>
          <div className="space-y-4">
            {[
              { folder: '/nextcloud/photos/family', shared: true },
              { folder: '/nextcloud/docs/private', shared: false },
              { folder: '/github/projects/shared', shared: true }
            ].map((item, idx) => (
              <div key={idx} className="flex items-center justify-between p-4 glass-card">
                <div className="flex items-center gap-3">
                  {item.shared ? <Eye size={16} className="text-emerald-400" /> : <EyeOff size={16} className="text-slate-500" />}
                  <span className="text-xs font-mono text-slate-300">{item.folder}</span>
                </div>
                <button className={`text-[10px] px-3 py-1 rounded-full border ${item.shared ? 'border-emerald-500/30 text-emerald-400 bg-emerald-500/5' : 'border-slate-500/30 text-slate-400'}`}>
                  {item.shared ? 'Shared' : 'Private'}
                </button>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
};

export default Identity;
