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
  X,
  Users,
  Plus,
  ExternalLink,
  Trash2,
  Edit3
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

const IntegrationTile = ({ name, icon: Icon, color, configKeys, userData }: any) => {
  const [isOpen, setIsOpen] = useState(false);
  const [form, setForm] = useState<any>({});
  const queryClient = useQueryClient();

  const connectionKey = Object.values(configKeys)[0] as string;
  const isConnected = !!userData?.[connectionKey];

  const updateMutation = useMutation({
    mutationFn: (data: any) => {
      // Validate URLs
      for (const [label, value] of Object.entries(data)) {
        const key = label.toLowerCase();
        if (key.includes('url') && value && typeof value === 'string') {
          if (!value.startsWith('http://') && !value.startsWith('https://')) {
             throw new Error(`${label} must start with http:// or https://`);
          }
        }
      }
      return api.updateProfile(data);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['me'] });
      setIsOpen(false);
    },
    onError: (err: any) => {
      alert(err.message);
    }
  });

  const handleOpen = () => {
    // Pre-fill form with existing (non-sensitive) values
    const initialForm: any = {};
    Object.values(configKeys).forEach((key: any) => {
      initialForm[key] = userData?.[key] || '';
    });
    setForm(initialForm);
    setIsOpen(true);
  };

  return (
    <>
      <div className="glass-panel p-6 flex flex-col items-center text-center gap-4 group hover:border-purple-500/30 transition-colors">
        <div className={`p-4 rounded-2xl bg-${color}-500/20 text-${color}-400 group-hover:scale-110 transition-transform`}>
          <Icon size={32} />
        </div>
        <div>
          <h3 className="font-bold text-white">{name}</h3>
          <p className="text-xs text-slate-500 mt-1">
            {isConnected ? (userData?.[connectionKey]?.replace(/^https?:\/\//, '')) : 'Not Configured'}
          </p>
        </div>
        <button onClick={handleOpen} className="glass-button w-full text-xs mt-2">
          {isConnected ? 'Manage Integration' : 'Configure Now'}
        </button>
        <div className="flex items-center gap-2 mt-2">
          <div className={`w-2 h-2 rounded-full ${isConnected ? 'bg-emerald-500 animate-pulse' : 'bg-slate-600'}`} />
          <span className="text-[10px] uppercase font-bold tracking-widest text-slate-500">
            {isConnected ? 'Connected' : 'Disconnected'}
          </span>
        </div>
      </div>

      <Modal isOpen={isOpen} onClose={() => setIsOpen(false)} title={`Configure ${name}`}>
        <div className="space-y-6">
          <div className="p-4 bg-purple-500/10 border border-purple-500/20 rounded-xl flex gap-4">
             <Shield className="text-purple-400 shrink-0" size={24} />
             <p className="text-xs text-slate-300 leading-relaxed">
               Your credentials are encrypted using AES-256 (Fernet) before being stored in our secure identity vault. They are only decrypted at runtime when a service specifically requests them.
             </p>
          </div>

          <div className="grid gap-4">
            {Object.entries(configKeys).map(([label, key]: [string, any]) => (
              <div key={key}>
                <div className="flex justify-between items-end mb-1.5">
                  <label className="text-[10px] text-slate-400 uppercase font-bold block">{label}</label>
                  {label.toLowerCase().includes('url') && <span className="text-[9px] text-slate-500 italic">Include http:// or https://</span>}
                </div>
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

const DiscoveryModal = () => {
  const [isOpen, setIsOpen] = useState(false);
  const queryClient = useQueryClient();

  // Expose to window for global trigger
  (window as any).showDiscoveryModal = () => setIsOpen(true);

  const { data: discovered, isLoading } = useQuery({
    queryKey: ['discovery'],
    queryFn: () => api.discoverUsers(),
    enabled: isOpen
  });

  const importMutation = useMutation({
    mutationFn: (user: any) => api.createUser({
      username: user.username,
      display_name: user.display_name,
      is_admin: false,
      is_system_default: false
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
      alert('User imported successfully!');
    }
  });

  return (
    <Modal isOpen={isOpen} onClose={() => setIsOpen(false)} title="Discover New Users">
      <div className="space-y-6">
        <p className="text-sm text-slate-400">Jarvis OS is scanning your connected Home Assistant and Nextcloud instances for family members to import.</p>
        
        {isLoading ? (
          <div className="flex flex-col items-center py-12 gap-4">
             <RefreshCcw className="animate-spin text-purple-500" size={32} />
             <p className="text-xs text-slate-500 uppercase tracking-widest font-bold">Scanning Services...</p>
          </div>
        ) : (
          <div className="grid gap-3">
            {discovered?.length ? discovered.map((u: any, i: number) => (
              <div key={i} className="glass-panel p-4 flex items-center justify-between">
                <div>
                  <p className="text-sm font-bold text-white">{u.display_name || u.username}</p>
                  <p className="text-[10px] text-slate-500 uppercase font-bold mt-1">Source: {u.source}</p>
                </div>
                <button 
                  onClick={() => importMutation.mutate(u)}
                  disabled={importMutation.isPending}
                  className="glass-button text-[10px] px-3 py-1.5 bg-indigo-500/20 text-indigo-400 border-indigo-500/20"
                >
                  Import Account
                </button>
              </div>
            )) : (
              <p className="text-center py-8 text-slate-500 italic text-sm">No new users discovered.</p>
            )}
          </div>
        )}
      </div>
    </Modal>
  );
};

const Identity = () => {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  
  // Fetch full user data including URLs
  const { data: fullUser } = useQuery({
    queryKey: ['me'],
    queryFn: () => api.getMe(),
    enabled: !!user
  });

  const { data: usersList } = useQuery({
    queryKey: ['users'],
    queryFn: () => api.getUsers(),
    enabled: !!fullUser?.is_admin
  });

  const deleteMutation = useMutation({
    mutationFn: (username: string) => api.deleteUser(username),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['users'] })
  });

  console.log('[Identity] fullUser:', fullUser);

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

      <section className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
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
          color="emerald" 
          userData={fullUser}
          configKeys={{
            "Cloud URL": "nextcloud_url",
            "Username": "nextcloud_user",
            "App Password": "nextcloud_pass"
          }}
        />
        <IntegrationTile 
          name="Git Repos" 
          icon={Server} 
          color="orange" 
          userData={fullUser}
          configKeys={{
            "GitHub URL": "github_url",
            "GitHub User": "github_user",
            "GitHub Token": "github_token",
            "GitLab URL": "gitlab_url",
            "GitLab User": "gitlab_user",
            "GitLab Token": "gitlab_token"
          }}
        />
      </section>

      <section>
        <div className="flex items-center justify-between mb-8">
           <div className="flex items-center gap-4">
              <div className="p-3 rounded-2xl bg-indigo-500/20 text-indigo-400">
                <Users size={24} />
              </div>
              <div>
                <h3 className="text-xl font-bold text-white">Family & Users</h3>
                <p className="text-sm text-slate-400">Manage access for other members</p>
              </div>
           </div>
           {fullUser?.is_admin && (
             <button 
               onClick={() => (window as any).showDiscoveryModal?.()}
               className="glass-button flex items-center gap-2 text-xs py-2 px-4"
             >
               <Plus size={16} /> Discover Users
             </button>
           )}
        </div>
        
        <div className="grid gap-4">
           {usersList?.map((u: any) => (
             <div key={u.id} className="glass-panel p-6 flex items-center justify-between group">
                <div className="flex items-center gap-4">
                   <div className="w-10 h-10 rounded-full bg-slate-800 flex items-center justify-center text-indigo-400 font-bold uppercase">
                      {u.username[0]}
                   </div>
                   <div>
                      <p className="text-sm font-bold text-white">{u.display_name || u.username}</p>
                      <p className="text-[10px] text-slate-500 uppercase tracking-widest font-bold">
                        {u.is_admin ? 'Admin' : 'Member'} {u.is_system_default && '/ System'}
                      </p>
                   </div>
                </div>
                <div className="flex items-center gap-3">
                   <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 uppercase tracking-widest">Active</span>
                   
                   {fullUser?.is_admin && !u.is_system_default && (
                     <div className="flex items-center gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                        <button className="p-2 hover:text-indigo-400 transition-colors">
                           <Edit3 size={16} />
                        </button>
                        <button 
                          onClick={() => confirm(`Delete ${u.username}?`) && deleteMutation.mutate(u.username)}
                          className="p-2 hover:text-red-400 transition-colors"
                        >
                           <Trash2 size={16} />
                        </button>
                     </div>
                   )}
                </div>
             </div>
           ))}
        </div>
      </section>

      <DiscoveryModal />
    </div>
  );
};

export default Identity;
