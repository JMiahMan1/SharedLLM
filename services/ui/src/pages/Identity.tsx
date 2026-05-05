import { useState, useRef, FC, ReactNode } from 'react';
import { 
  Cloud, 
  Music, 
  Mic, 
  Lock,
  RefreshCcw,
  Home,
  Server,
  Save,
  X,
  Plus,
  Trash2,
  Edit3,
  Activity,
  CheckCircle,
  AlertCircle,
  Globe,
  LockKeyhole,
  Smartphone,
  User,
  UserPlus,
  LucideIcon
} from 'lucide-react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../services/api';
import type { UserProfile, APIKey } from '../services/api';
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
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-md">
      <div className="glass-panel w-full max-w-2xl max-h-[90vh] overflow-hidden flex flex-col">
        <div className="p-6 border-b border-white/5 flex items-center justify-between bg-white/5">
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

interface IntegrationTileProps {
  name: string;
  icon: LucideIcon;
  color: string;
  configKeys: Record<string, string>;
  userData?: UserProfile | null;
}

const IntegrationTile: FC<IntegrationTileProps> = ({ name, icon: Icon, color, configKeys, userData }) => {
  const [isOpen, setIsOpen] = useState(false);
  const [form, setForm] = useState<Record<string, string | boolean>>({});
  const [testResult, setTestResult] = useState<{ status: 'SUCCESS' | 'ERROR', message?: string } | null>(null);
  const [isTesting, setIsTesting] = useState(false);
  const { role } = useAuth();
  const queryClient = useQueryClient();

  const connectionKey = Object.values(configKeys)[0];
  const isConnected = !!(userData && (userData as Record<string, unknown>)[connectionKey]);

  const defaultShare = role === 'admin';

  const updateMutation = useMutation({
    mutationFn: (data: Partial<UserProfile>) => api.updateProfile({ 
      ...data, 
      share_with_all: (data.share_with_all as boolean) ?? defaultShare 
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['me'] });
      setIsOpen(false);
      toast.success(`${name} configuration updated`);
    },
    onError: (err: unknown) => {
      if (err instanceof Error) {
        toast.error(err.message || 'Update failed');
      } else {
        toast.error('Update failed');
      }
    }
  });

  const handleTest = async () => {
    setIsTesting(true);
    setTestResult(null);
    try {
      const result = await api.testConnection(name, form as Record<string, unknown>);
      setTestResult(result);
      if (result.status === 'SUCCESS') toast.success('Connection verified');
    } catch (err: unknown) {
      let message = 'Connection failed';
      if (err && typeof err === 'object' && 'response' in err) {
         const resp = (err as { response: { data: { detail?: string } } }).response;
         message = resp?.data?.detail || (err as Error).message || message;
      }
      setTestResult({ 
        status: 'ERROR', 
        message
      });
      toast.error('Connection failed');
    } finally {
      setIsTesting(false);
    }
  };

  const handleOpen = () => {
    const initialForm: Record<string, string | boolean> = { share_with_all: userData?.share_with_all ?? defaultShare };
    Object.values(configKeys).forEach((key) => {
      initialForm[key] = (userData as Record<string, string | boolean>)?.[key] || '';
    });
    setForm(initialForm);
    setIsOpen(true);
    setTestResult(null);
  };

  return (
    <>
      <div className="glass-panel p-6 flex flex-col items-center text-center gap-4 group hover:border-purple-500/30 transition-all duration-500 hover:shadow-2xl hover:shadow-purple-500/10">
        <div className={`p-4 rounded-2xl bg-${color}-500/20 text-${color}-400 group-hover:scale-110 transition-transform duration-500`}>
          <Icon size={32} />
        </div>
        <div>
          <h3 className="font-bold text-white">{name}</h3>
          <p className="text-[10px] text-slate-500 mt-1 truncate max-w-[150px] font-mono">
            {isConnected ? ((userData as Record<string, string>)?.[connectionKey]?.replace(/^https?:\/\//, '')) : 'DISCONNECTED'}
          </p>
        </div>
        <button onClick={handleOpen} className="glass-button w-full text-[10px] uppercase font-bold tracking-widest mt-2 py-2">
          {isConnected ? 'Manage Integration' : 'Connect Service'}
        </button>
        <div className="flex items-center gap-2 mt-2">
          <div className={`w-1.5 h-1.5 rounded-full ${isConnected ? 'bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.6)]' : 'bg-slate-600'}`} />
          <span className="text-[9px] uppercase font-black tracking-tighter text-slate-500">
            {isConnected ? 'Linked' : 'Not Linked'}
          </span>
        </div>
      </div>

      <Modal isOpen={isOpen} onClose={() => setIsOpen(false)} title={`Secure Vault: ${name}`}>
        <div className="space-y-6">
          <div className="p-4 bg-indigo-500/10 border border-indigo-500/20 rounded-xl flex gap-4">
             <LockKeyhole className="text-indigo-400 shrink-0" size={24} />
             <div className="text-xs text-slate-300 leading-relaxed">
               <p className="font-bold text-white mb-1">Identity Encryption Active</p>
               All credentials are encrypted at rest using AES-256 Fernet. They are only decrypted in-memory during service orchestration.
             </div>
          </div>

          <div className="grid gap-4">
            {Object.entries(configKeys).map(([label, key]) => (
              <div key={key}>
                <label className="text-[10px] text-slate-400 uppercase font-black block mb-2 tracking-widest">{label}</label>
                <input 
                  type={label.toLowerCase().includes('pass') || label.toLowerCase().includes('token') || label.toLowerCase().includes('secret') ? 'password' : 'text'}
                  value={(form[key] as string) || ''}
                  onChange={(e) => setForm({ ...form, [key]: e.target.value })}
                  className="glass-input w-full text-sm py-3 bg-black/20 focus:bg-black/40"
                  placeholder={`Enter ${label}...`}
                />
              </div>
            ))}
          </div>

          <div className="p-4 glass-card border-white/5 bg-white/5 rounded-xl">
             <div className="flex items-center justify-between">
                <div>
                   <p className="text-xs font-bold text-white">Data Sharing Rule</p>
                   <p className="text-[10px] text-slate-500 mt-1">Allow Jarvis to index this service for all family members (RAG).</p>
                </div>
                <label className="relative inline-flex items-center cursor-pointer">
                  <input 
                    type="checkbox" 
                    checked={form.share_with_all as boolean}
                    onChange={(e) => setForm({...form, share_with_all: e.target.checked})}
                    className="sr-only peer" 
                  />
                  <div className="w-10 h-5 bg-slate-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full rtl:peer-checked:after:-translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:start-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-purple-600"></div>
                </label>
             </div>
          </div>

          {testResult && (
            <div className={`p-4 rounded-xl flex items-center gap-3 border ${
              testResult.status === 'SUCCESS' ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400' : 'bg-red-500/10 border-red-500/20 text-red-400'
            }`}>
              {testResult.status === 'SUCCESS' ? <CheckCircle size={18} /> : <AlertCircle size={18} />}
              <p className="text-xs font-medium">{testResult.message || (testResult.status === 'SUCCESS' ? 'Connection Successful' : 'Connection Failed')}</p>
            </div>
          )}

          <div className="flex gap-3 pt-4">
            <button 
              onClick={handleTest}
              disabled={isTesting}
              className="glass-button px-6 py-3 flex items-center gap-2 text-[10px] font-black uppercase tracking-widest"
            >
              <RefreshCcw size={16} className={isTesting ? 'animate-spin' : ''} />
              {isTesting ? 'Verifying...' : 'Test Sync'}
            </button>
            <button 
              onClick={() => updateMutation.mutate(form as Partial<UserProfile>)}
              disabled={updateMutation.isPending}
              className="glass-button flex-1 py-3 bg-purple-600/40 border-purple-500/30 flex items-center justify-center gap-2 text-[10px] font-black uppercase tracking-widest"
            >
              <Save size={18} />
              {updateMutation.isPending ? 'Syncing Vault...' : 'Commit Changes'}
            </button>
          </div>
        </div>
      </Modal>
    </>
  );
};

const VoiceEnrollmentCard: FC<{ enrolled: boolean }> = ({ enrolled }) => {
  const [isRecording, setIsRecording] = useState(false);
  const [countdown, setCountdown] = useState(0);
  const [status, setStatus] = useState<string | null>(null);
  const mediaRecorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const queryClient = useQueryClient();

  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      mediaRecorder.current = new MediaRecorder(stream);
      chunks.current = [];
      
      mediaRecorder.current.ondataavailable = (e) => {
        if (e.data.size > 0) chunks.current.push(e.data);
      };

      mediaRecorder.current.onstop = async () => {
        const blob = new Blob(chunks.current, { type: 'audio/webm' });
        setStatus('Processing Biometrics...');
        try {
          await api.enrollVoice(blob);
          setStatus('Enrollment Successful!');
          toast.success('Voice profile verified');
          queryClient.invalidateQueries({ queryKey: ['me'] });
        } catch {
          setStatus('Enrollment Failed');
          toast.error('Biometric processing failed');
        }
        stream.getTracks().forEach(track => track.stop());
      };

      mediaRecorder.current.start();
      setIsRecording(true);
      setCountdown(10);
      setStatus('Recording Voice Sample...');
      
      const timer = setInterval(() => {
        setCountdown(prev => {
          if (prev <= 1) {
            clearInterval(timer);
            stopRecording();
            return 0;
          }
          return prev - 1;
        });
      }, 1000);

    } catch {
      toast.error('Microphone access denied');
    }
  };

  const stopRecording = () => {
    if (mediaRecorder.current && isRecording) {
      mediaRecorder.current.stop();
      setIsRecording(false);
    }
  };

  return (
    <div className={`glass-panel p-8 flex flex-col items-center text-center gap-6 border-2 transition-all duration-500 ${enrolled ? 'border-emerald-500/30 bg-emerald-500/5 shadow-lg shadow-emerald-500/5' : 'border-dashed border-slate-700 bg-black/10'}`}>
      <div className={`p-5 rounded-full relative ${isRecording ? 'bg-red-500/20 text-red-400' : enrolled ? 'bg-emerald-500/20 text-emerald-400' : 'bg-slate-800 text-slate-500'}`}>
        {isRecording && <div className="absolute inset-0 rounded-full border-2 border-red-500 animate-ping opacity-40" />}
        <Mic size={40} />
      </div>
      <div>
        <h4 className="font-bold text-white text-lg">Biometric Voice Profile</h4>
        <p className="text-sm text-slate-400 mt-2 max-w-sm leading-relaxed">
          {isRecording 
            ? `Speak naturally... Jarvis is mapping your vocal frequency. (${countdown}s remaining)` 
            : enrolled 
              ? "Your voice is registered. Jarvis can now identify you across all media players." 
              : "Enroll your voice to enable personalized reminders, secure tool usage, and biometric identification."}
        </p>
      </div>
      <div className="flex flex-col items-center gap-4 w-full max-w-xs">
         <button 
           onClick={isRecording ? stopRecording : startRecording}
           disabled={status === 'Processing Biometrics...'}
           className={`glass-button w-full py-4 flex items-center justify-center gap-2 text-[10px] font-black uppercase tracking-widest transition-all ${isRecording ? 'bg-red-500/30 border-red-500/50 text-red-400' : 'bg-indigo-600/30 border-indigo-500/50 text-indigo-400 hover:bg-indigo-600/50'}`}
         >
           {isRecording ? <X size={16} /> : <Activity size={16} />}
           {isRecording ? `Stop Recording` : enrolled ? "Retrain Profile" : "Begin Enrollment"}
         </button>
         
         {status && (
           <p className={`text-[9px] uppercase font-black tracking-widest animate-pulse ${status.includes('Failed') ? 'text-red-400' : 'text-indigo-400'}`}>
             {status}
           </p>
         )}
      </div>
      {enrolled && !isRecording && (
        <div className="flex items-center gap-2 bg-emerald-500/10 px-3 py-1 rounded-full border border-emerald-500/20">
          <CheckCircle size={12} className="text-emerald-500" />
          <span className="text-[9px] uppercase font-black tracking-widest text-emerald-500">Identity Authenticated</span>
        </div>
      )}
    </div>
  );
};

const APIKeyRows = () => {
  const queryClient = useQueryClient();
  const { data: keys, isLoading } = useQuery<APIKey[]>({
    queryKey: ['api-keys'],
    queryFn: () => api.getAPIKeys(),
  });

  const revokeMutation = useMutation({
    mutationFn: (id: string) => api.revokeAPIKey(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['api-keys'] });
      toast.success('Key access revoked');
    },
    onError: () => toast.error('Revocation failed')
  });

  if (isLoading) return <tr><td colSpan={4} className="px-6 py-8 text-center text-slate-500 animate-pulse">Scanning Vault...</td></tr>;
  if (!keys?.length) return <tr><td colSpan={4} className="px-6 py-8 text-center text-slate-500 italic">No active external keys found.</td></tr>;

  return (
    <>
      {keys.map((k) => (
        <tr key={k.id} className="group hover:bg-white/5 transition-colors">
           <td className="px-6 py-4">
              <div className="flex items-center gap-3">
                 <div className="p-1.5 rounded-lg bg-emerald-500/10 text-emerald-400"><Smartphone size={14} /></div>
                 <span className="font-bold text-white tracking-tight">{k.label}</span>
              </div>
           </td>
           <td className="px-6 py-4 font-mono text-slate-500 text-[10px]">sk-nexus-••••{k.prefix}</td>
           <td className="px-6 py-4">
              <span className="px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 text-[8px] font-black uppercase tracking-widest">Active</span>
           </td>
           <td className="px-6 py-4 text-right">
              <button 
                onClick={() => confirm(`Permanently revoke access for ${k.label}?`) && revokeMutation.mutate(k.id)}
                className="p-2 text-slate-500 hover:text-red-400 hover:bg-red-500/10 rounded-lg transition-all"
              >
                 <Trash2 size={16} />
              </button>
           </td>
        </tr>
      ))}
    </>
  );
};

const Identity = () => {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  
  const { data: fullUser } = useQuery<UserProfile>({
    queryKey: ['me'],
    queryFn: () => api.getMe(),
    enabled: !!user
  });

  const { data: usersList } = useQuery<UserProfile[]>({
    queryKey: ['users'],
    queryFn: () => api.getUsers(),
    enabled: !!fullUser?.is_admin
  });

  const updateProfileMutation = useMutation({
    mutationFn: (data: Partial<UserProfile>) => api.updateProfile(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['me'] });
      toast.success('Persona updated');
    }
  });

  return (
    <div className="space-y-12 pb-12">
      <header className="flex flex-col md:flex-row md:items-end justify-between gap-6">
        <div>
          <h2 className="text-4xl font-black text-white tracking-tighter uppercase">IDENTITY HUB</h2>
          <p className="text-slate-400 mt-2 max-w-md">Manage your digital footprint, encrypted credentials, and biometric profiles for the SharedLLM mesh.</p>
        </div>
        <div className="flex items-center gap-4 bg-white/5 p-3 rounded-2xl border border-white/5">
           <div className="w-12 h-12 rounded-xl bg-purple-600 flex items-center justify-center text-xl font-black text-white shadow-lg shadow-purple-500/20">
              {user?.username[0].toUpperCase()}
           </div>
           <div>
              <p className="text-[10px] text-slate-500 uppercase font-black tracking-widest">Active Operator</p>
              <p className="text-white font-mono font-bold text-sm">@{user?.username}</p>
           </div>
        </div>
      </header>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-8">
        <div className="xl:col-span-2 space-y-12">
          <section>
            <div className="flex items-center gap-4 mb-8">
              <div className="p-3 rounded-2xl bg-indigo-500/20 text-indigo-400 shadow-inner">
                <RefreshCcw size={24} />
              </div>
              <div>
                <h3 className="text-xl font-bold text-white">Integration Gallery</h3>
                <p className="text-sm text-slate-400">Secure connection points for cloud and home services</p>
              </div>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
              <IntegrationTile 
                name="Home Assistant" 
                icon={Home} 
                color="blue" 
                userData={fullUser}
                configKeys={{
                  "Instance URL": "ha_url",
                  "Long-Lived Token": "ha_token"
                }}
              />
              <IntegrationTile 
                name="Nextcloud" 
                icon={Cloud} 
                color="emerald" 
                userData={fullUser}
                configKeys={{
                  "Cloud Base URL": "nextcloud_url",
                  "Cloud Username": "nextcloud_user",
                  "App Password": "nextcloud_pass"
                }}
              />
              <IntegrationTile 
                name="Audiobookshelf" 
                icon={Music} 
                color="amber" 
                userData={fullUser}
                configKeys={{
                  "Server URL": "audiobookshelf_url",
                  "API Key": "audiobookshelf_token"
                }}
              />
              <IntegrationTile 
                name="GitHub" 
                icon={Globe} 
                color="slate" 
                userData={fullUser}
                configKeys={{
                  "GitHub Username": "github_user",
                  "Personal Access Token": "github_token"
                }}
              />
              <IntegrationTile 
                name="Private Git" 
                icon={Server} 
                color="rose" 
                userData={fullUser}
                configKeys={{
                  "Git Endpoint": "git_url",
                  "Auth Username": "git_user",
                  "Auth Password": "git_token"
                }}
              />
            </div>
          </section>

          <section>
            <div className="flex items-center gap-3 mb-8">
              <div className="p-3 rounded-2xl bg-pink-500/20 text-pink-400">
                <Mic size={24} />
              </div>
              <div>
                <h3 className="text-xl font-bold text-white">Vocal Signature</h3>
                <p className="text-sm text-slate-400">Neural mapping for biometric identification</p>
              </div>
            </div>
            
            <div className="max-w-3xl">
               <VoiceEnrollmentCard enrolled={!!fullUser?.voice_id} />
            </div>
          </section>

          <section>
            <div className="flex items-center gap-4 mb-8">
              <div className="p-3 rounded-2xl bg-emerald-500/20 text-emerald-400">
                <Lock size={24} />
              </div>
              <div>
                <h3 className="text-xl font-bold text-white">External Client Access</h3>
                <p className="text-sm text-slate-400">Manage API keys for OpenWebUI, Lobechat, and other compatible interfaces</p>
              </div>
            </div>
            
            <div className="glass-panel overflow-hidden border-emerald-500/10">
              <div className="p-6 border-b border-white/5 flex items-center justify-between bg-emerald-500/5">
                 <div className="flex items-center gap-2">
                    <Globe size={16} className="text-emerald-400" />
                    <span className="text-[10px] font-black uppercase tracking-widest text-emerald-400">OpenAI-Compatible Endpoints</span>
                 </div>
                 <button 
                   onClick={async () => {
                     const label = prompt('Enter a label for this key (e.g. OpenWebUI):');
                     if (label) {
                        try {
                          const res = await api.generateAPIKey(label);
                          toast.success('Key generated: ' + res.key);
                          queryClient.invalidateQueries({ queryKey: ['api-keys'] });
                        } catch {
                          toast.error('Failed to generate key');
                        }
                     }
                   }}
                   className="glass-button text-[10px] py-1.5 px-4 bg-emerald-600/20 text-emerald-400 border-emerald-500/20"
                 >
                   <Plus size={14} /> Generate New Key
                 </button>
              </div>
              
              <div className="p-0">
                 <table className="w-full text-left text-xs">
                    <thead className="bg-white/5 text-slate-500 font-black uppercase text-[9px] tracking-widest">
                       <tr>
                          <th className="px-6 py-4">Client Label</th>
                          <th className="px-6 py-4">API Key Prefix</th>
                          <th className="px-6 py-4">Status</th>
                          <th className="px-6 py-4 text-right">Actions</th>
                       </tr>
                    </thead>
                    <tbody className="divide-y divide-white/5">
                       <APIKeyRows />
                    </tbody>
                 </table>
              </div>
            </div>
          </section>
        </div>

        <div className="space-y-8">
          <section className="glass-panel p-8 bg-purple-900/10 border-purple-500/20">
             <div className="flex items-center gap-3 mb-6">
                <User size={20} className="text-purple-400" />
                <h3 className="font-bold text-white uppercase text-xs tracking-widest">Digital Persona</h3>
             </div>
             
             <div className="space-y-6">
                <div className="flex flex-col items-center">
                   <div className="w-24 h-24 rounded-2xl bg-white/5 border border-white/10 flex items-center justify-center relative group cursor-pointer overflow-hidden mb-4">
                      {fullUser?.avatar_url ? (
                        <img src={fullUser.avatar_url} className="w-full h-full object-cover" alt="Avatar" />
                      ) : (
                        <Plus size={32} className="text-slate-600 group-hover:text-purple-400 transition-colors" />
                      )}
                      <div className="absolute inset-0 bg-black/40 opacity-0 group-hover:opacity-100 flex items-center justify-center transition-opacity">
                         <Edit3 size={20} className="text-white" />
                      </div>
                   </div>
                   <p className="text-[10px] text-slate-500 uppercase font-black tracking-widest mb-1">Avatar & Identity</p>
                   <p className="text-white font-bold">{fullUser?.full_name || 'Set Full Name'}</p>
                </div>

                <div className="space-y-4 pt-4 border-t border-white/5">
                   <div>
                      <label className="text-[9px] text-slate-500 uppercase font-black block mb-2 tracking-widest">Display Name</label>
                      <input 
                        type="text" 
                        defaultValue={fullUser?.full_name}
                        onBlur={(e) => updateProfileMutation.mutate({ full_name: e.target.value })}
                        className="glass-input w-full text-xs" 
                        placeholder="John Doe" 
                      />
                   </div>
                   <div>
                      <label className="text-[9px] text-slate-500 uppercase font-black block mb-2 tracking-widest">Voice ID</label>
                      <input 
                        type="text" 
                        value={fullUser?.voice_id || 'NOT_ASSIGNED'} 
                        disabled 
                        className="glass-input w-full text-xs opacity-50 cursor-not-allowed" 
                      />
                   </div>
                </div>
             </div>
          </section>

          {fullUser?.is_admin && (
            <section className="glass-panel p-6 border-blue-500/20">
               <h3 className="font-bold text-white mb-4 text-xs tracking-widest uppercase">System Hierarchy</h3>
               <div className="space-y-3">
                  {usersList?.map((u) => (
                    <div key={u.id} className="flex items-center justify-between p-3 glass-card text-xs">
                       <div className="flex items-center gap-3">
                          <div className={`w-2 h-2 rounded-full ${u.is_admin ? 'bg-indigo-500 shadow-[0_0_8px_rgba(99,102,241,0.6)]' : 'bg-slate-600'}`} />
                          <span className="font-bold text-slate-300">@{u.username}</span>
                       </div>
                       <span className="text-[8px] uppercase font-black px-2 py-0.5 rounded-full bg-white/5 text-slate-500">{u.is_admin ? 'Admin' : 'User'}</span>
                    </div>
                  ))}
               </div>
               <button className="glass-button w-full mt-6 py-3 text-[9px] font-black uppercase tracking-widest bg-blue-600/20 border-blue-500/20">
                  <UserPlus size={14} /> Add System User
               </button>
            </section>
          )}
        </div>
      </div>
    </div>
  );
};

export default Identity;
