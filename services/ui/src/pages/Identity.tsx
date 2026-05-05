import { useState } from 'react';
import { 
  Cloud, 
  Music, 
  Mic, 
  Lock,
  Eye,
  EyeOff,
  ChevronRight,
  UserPlus,
  RefreshCcw,
  Shield,
  Key,
  Home,
  Server,
  Save,
  X
} from 'lucide-react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../services/api';
import { useAuth } from '../context/AuthContext';

const Modal = ({ isOpen, onClose, title, children }: any) => {
  if (!isOpen) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm">
      <div className="glass-panel w-full max-w-2xl max-h-[90vh] overflow-hidden flex flex-col">
        <div className="p-6 border-b border-white/5 flex items-center justify-between">
          <h3 className="text-xl font-bold text-white">{title}</h3>
          <button onClick={onClose} className="text-slate-400 hover:text-white transition-colors">
            <X size={24} />
          </button>
        </div>
        <div className="p-6 overflow-y-auto flex-1">
          {children}
        </div>
      </div>
    </div>
  );
};

const UserManagement = () => {
  const { user: currentUser } = useAuth();
  const queryClient = useQueryClient();
  const [newPassword, setNewPassword] = useState('');
  const [discoveredUsers, setDiscoveredUsers] = useState<any[]>([]);
  const [showDiscoveryModal, setShowDiscoveryModal] = useState(false);

  const { data: users, isLoading } = useQuery({
    queryKey: ['users'],
    queryFn: () => fetch('/api/users', { 
      headers: { 'Authorization': `Bearer ${localStorage.getItem('jarvis_api_key')}` }
    }).then(r => r.json()),
    enabled: !!currentUser?.is_admin
  });

  const discoverMutation = useMutation({
    mutationFn: () => api.discoverUsers(),
    onSuccess: (data) => {
      setDiscoveredUsers(data);
      setShowDiscoveryModal(true);
    }
  });

  const changePasswordMutation = useMutation({
    mutationFn: (pwd: string) => api.changePassword(pwd),
    onSuccess: () => {
      setNewPassword('');
      alert('Password updated successfully');
    }
  });

  if (!currentUser?.is_admin) return null;

  return (
    <>
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

      <Modal 
        isOpen={showDiscoveryModal} 
        onClose={() => setShowDiscoveryModal(false)}
        title="Discovered Family Members"
      >
        <div className="space-y-4">
          {discoveredUsers.length === 0 ? (
            <p className="text-slate-400 italic">No new users found on your network.</p>
          ) : (
            discoveredUsers.map((du, idx) => (
              <div key={idx} className="flex items-center justify-between p-4 glass-card bg-white/5">
                <div className="flex items-center gap-4">
                  <div className="p-2 rounded-lg bg-blue-500/20 text-blue-400">
                    {du.source === 'Home Assistant' ? <Home size={20} /> : <Cloud size={20} />}
                  </div>
                  <div>
                    <p className="font-bold text-white">{du.display_name || du.username}</p>
                    <p className="text-[10px] text-slate-500 uppercase">Source: {du.source}</p>
                  </div>
                </div>
                <button className="glass-button text-[10px] py-1 px-3 bg-blue-600/40 border-blue-500/30">
                  Import Account
                </button>
              </div>
            ))
          )}
        </div>
      </Modal>
    </>
  );
};

const IntegrationTile = ({ name, icon: Icon, color, configKeys, userData }: any) => {
  const [isOpen, setIsOpen] = useState(false);
  const [form, setForm] = useState<any>({});
  const queryClient = useQueryClient();

  const connectionKey = Object.values(configKeys)[0] as string;
  const isConnected = !!userData?.[connectionKey];

  const updateMutation = useMutation({
    mutationFn: (data: any) => api.updateProfile(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['me'] });
      setIsOpen(false);
    }
  });

  const handleOpen = () => {
    const initialForm: any = {};
    Object.values(configKeys).forEach((key: any) => {
      initialForm[key] = userData?.[key] || '';
    });
    setForm(initialForm);
    setIsOpen(true);
  };

  return (
    <>
      <div className="glass-card p-6 flex flex-col gap-4 group">
        <div className="flex items-center justify-between">
          <div className={`p-3 rounded-xl bg-${color}-500/20 text-${color}-400 border border-${color}-500/20 group-hover:scale-110 transition-transform`}>
            <Icon size={24} />
          </div>
          <span className={`text-[10px] uppercase font-bold px-2 py-1 rounded-md ${isConnected ? 'bg-green-500/10 text-green-400' : 'bg-red-500/10 text-red-400'}`}>
            {isConnected ? 'Connected' : 'Disconnected'}
          </span>
        </div>
        <div>
          <h3 className="font-bold text-white">{name}</h3>
          <p className="text-xs text-slate-500 mt-1">
            {isConnected ? (userData?.[connectionKey]?.replace(/^https?:\/\//, '')) : 'Not Configured'}
          </p>
        </div>
        <button onClick={handleOpen} className="glass-button w-full text-xs mt-2">
          {isConnected ? 'Manage' : 'Connect'} <ChevronRight size={14} />
        </button>
      </div>

      <Modal isOpen={isOpen} onClose={() => setIsOpen(false)} title={`${name} Configuration`}>
        <div className="space-y-6">
          <div className="grid gap-4">
            {Object.entries(configKeys).map(([label, key]: [string, any]) => (
              <div key={key}>
                <label className="text-[10px] text-slate-400 uppercase font-bold mb-1.5 block">{label}</label>
                <input 
                  type={label.toLowerCase().includes('pass') || label.toLowerCase().includes('token') ? 'password' : 'text'}
                  value={form[key] || ''}
                  onChange={(e) => setForm({ ...form, [key]: e.target.value })}
                  className="glass-input w-full text-sm py-3"
                  placeholder={`Enter ${label}...`}
                />
              </div>
            ))}
          </div>
          <div className="flex gap-3 pt-4">
            <button 
              onClick={() => updateMutation.mutate(form)}
              disabled={updateMutation.isPending}
              className="glass-button flex-1 py-3 bg-purple-600/40 border-purple-500/30 flex items-center justify-center gap-2"
            >
              <Save size={18} />
              {updateMutation.isPending ? 'Saving...' : 'Save Configuration'}
            </button>
            <button onClick={() => setIsOpen(false)} className="glass-button px-6 py-3">Cancel</button>
          </div>
        </div>
      </Modal>
    </>
  );
};

const Identity = () => {
  const { user } = useAuth();
  
  // Fetch full user data including URLs
  const { data: fullUser } = useQuery({
    queryKey: ['me'],
    queryFn: () => api.getMe(),
    enabled: !!user
  });

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
          <IntegrationTile 
            name="Home Assistant" 
            icon={Home} 
            color="blue" 
            userData={fullUser}
            configKeys={{
              "HA URL": "ha_url",
              "Access Token": "ha_token"
            }}
          />
          <IntegrationTile 
            name="Nextcloud" 
            icon={Cloud} 
            color="sky" 
            userData={fullUser}
            configKeys={{
              "NC URL": "nextcloud_url",
              "Username": "nextcloud_user",
              "Password": "nextcloud_pass"
            }}
          />
          <IntegrationTile 
            name="GitHub" 
            icon={Server} 
            color="slate" 
            userData={fullUser}
            configKeys={{
              "Instance URL": "github_url",
              "Username": "github_user",
              "Access Token": "github_token"
            }}
          />
          <IntegrationTile 
            name="Audiobookshelf" 
            icon={Music} 
            color="orange" 
            userData={fullUser}
            configKeys={{
              "ABS URL": "audiobookshelf_url",
              "Username": "audiobookshelf_user",
              "Password": "audiobookshelf_pass"
            }}
          />
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
        </section>

        <section className="glass-panel p-8">
          <h3 className="font-bold text-white mb-6 flex items-center gap-2">
            <Lock size={18} className="text-emerald-400" />
            Security Context
          </h3>
          <div className="p-6 bg-white/5 rounded-2xl border border-white/5">
             <div className="flex items-center gap-3 mb-4">
                <Server size={20} className="text-emerald-400" />
                <span className="text-sm font-bold text-white">Encrypted Vault</span>
             </div>
             <p className="text-xs text-slate-400 leading-relaxed mb-4">
                All credentials are encrypted using AES-256 (Fernet) before being persisted to the identity database. 
                Tokens are only decrypted in-memory during service resolution.
             </p>
             <div className="flex items-center gap-2 text-[10px] font-mono text-emerald-500">
                <Shield size={12} /> SYSTEM_VAULT_ACTIVE
             </div>
          </div>
        </section>
      </div>
    </div>
  );
};

export default Identity;
