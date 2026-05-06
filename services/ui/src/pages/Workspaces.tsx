import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { 
  Database, 
  Plus, 
  Edit3, 
  Trash2, 
  Save, 
  Copy, 
  Check, 
  GitPullRequest, 
  Folder, 
  Globe, 
  ShieldCheck,
  ExternalLink,
  ChevronRight
} from 'lucide-react';
import toast from 'react-hot-toast';
import { api, type Workspace } from '../services/api';
import Modal from '../components/ui/Modal';

const Workspaces = () => {
  const queryClient = useQueryClient();
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [editingWs, setEditingWs] = useState<Workspace | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const [form, setForm] = useState<Partial<Workspace>>({
    id: '',
    display_name: '',
    local_path: '',
    git_remote: 'origin',
    default_branch: 'main',
    sync_mode: 'local_git_authoritative',
    auto_pull_enabled: false,
    webhook_token: ''
  });

  const { data: workspaces = [], isLoading } = useQuery({
    queryKey: ['workspaces'],
    queryFn: () => api.getWorkspaces(),
  });

  const saveMutation = useMutation({
    mutationFn: (data: Partial<Workspace>) => {
      if (editingWs) {
        return api.updateWorkspace(editingWs.id, data);
      }
      return api.createWorkspace(data as any);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['workspaces'] });
      setIsModalOpen(false);
      setEditingWs(null);
      toast.success(editingWs ? 'Workspace updated' : 'Workspace created');
    },
    onError: (err: Error) => toast.error(err.message || 'Failed to save workspace'),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.deleteWorkspace(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['workspaces'] });
      toast.success('Workspace deleted');
    },
    onError: (err: Error) => toast.error(err.message || 'Failed to delete workspace'),
  });

  const openEdit = (ws: Workspace) => {
    setEditingWs(ws);
    setForm(ws);
    setIsModalOpen(true);
  };

  const openCreate = () => {
    setEditingWs(null);
    setForm({
      id: '',
      display_name: '',
      local_path: '',
      git_remote: 'origin',
      default_branch: 'main',
      sync_mode: 'local_git_authoritative',
      auto_pull_enabled: false,
      webhook_token: Math.random().toString(36).substring(2, 15) + Math.random().toString(36).substring(2, 15)
    });
    setIsModalOpen(true);
  };

  const copyToClipboard = (text: string, id: string) => {
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    toast.success('Copied to clipboard');
    setTimeout(() => setCopiedId(null), 2000);
  };

  const getWebhookUrl = (id: string, token?: string | null) => {
    const base = window.location.origin;
    return `${base}/api/webhook/git-pull/${id}${token ? `?token=${token}` : ''}`;
  };

  return (
    <div className="space-y-8 pb-12 animate-in fade-in duration-500">
      <header className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
        <div>
          <h2 className="text-3xl font-bold tracking-tight text-white">Workspaces</h2>
          <p className="mt-1 text-slate-400">Manage repository locations and automated sync triggers.</p>
        </div>
        <button 
          onClick={openCreate}
          className="glass-button px-6 py-2.5 bg-indigo-600/20 border-indigo-500/30 text-indigo-300 font-bold flex items-center gap-2"
        >
          <Plus size={18} />
          Add Repository
        </button>
      </header>

      <div className="grid gap-6">
        {isLoading ? (
          <div className="glass-panel p-12 flex items-center justify-center">
            <div className="flex flex-col items-center gap-4">
               <div className="w-12 h-12 border-4 border-indigo-500/30 border-t-indigo-500 rounded-full animate-spin" />
               <p className="text-slate-500 font-medium">Loading system registries...</p>
            </div>
          </div>
        ) : workspaces.length === 0 ? (
          <div className="glass-panel p-12 text-center">
            <Database size={48} className="mx-auto text-slate-700 mb-4" />
            <h3 className="text-xl font-bold text-white">No Workspaces Found</h3>
            <p className="text-slate-500 mt-2 max-w-md mx-auto">
              You haven't configured any repository workspaces yet. 
              Add one to enable Git operations and RAG synchronization.
            </p>
            <button 
              onClick={openCreate}
              className="mt-6 glass-button px-8 py-3 font-bold"
            >
              Get Started
            </button>
          </div>
        ) : (
          workspaces.map((ws) => (
            <div key={ws.id} className="glass-panel group overflow-hidden border-white/5 hover:border-white/10 transition-colors">
              <div className="flex flex-col xl:flex-row">
                {/* Info Section */}
                <div className="flex-1 p-6 border-b xl:border-b-0 xl:border-r border-white/5">
                  <div className="flex items-start justify-between mb-4">
                    <div className="flex items-center gap-3">
                      <div className="p-3 rounded-2xl bg-indigo-500/10 text-indigo-400">
                        <Database size={24} />
                      </div>
                      <div>
                        <h3 className="text-xl font-bold text-white">{ws.display_name}</h3>
                        <p className="text-xs font-mono text-slate-500">ID: {ws.id}</p>
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <button 
                        onClick={() => openEdit(ws)}
                        className="p-2 rounded-xl text-slate-500 hover:text-white hover:bg-white/5 transition-colors"
                      >
                        <Edit3 size={18} />
                      </button>
                      <button 
                        onClick={() => {
                          if (confirm(`Are you sure you want to delete ${ws.display_name}?`)) {
                            deleteMutation.mutate(ws.id);
                          }
                        }}
                        className="p-2 rounded-xl text-slate-500 hover:text-red-400 hover:bg-red-500/10 transition-colors"
                      >
                        <Trash2 size={18} />
                      </button>
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-1">
                      <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">Host Path</p>
                      <div className="flex items-center gap-2 text-sm text-slate-300">
                        <Folder size={14} className="text-slate-600" />
                        <span className="font-mono">{ws.local_path}</span>
                      </div>
                    </div>
                    <div className="space-y-1">
                      <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">Git Status</p>
                      <div className="flex items-center gap-2 text-sm text-slate-300">
                        <GitPullRequest size={14} className="text-slate-600" />
                        <span>{ws.git_remote}/{ws.default_branch}</span>
                      </div>
                    </div>
                  </div>
                </div>

                {/* Webhook Section */}
                <div className="w-full xl:w-96 p-6 bg-white/[0.01]">
                   <div className="flex items-center justify-between mb-4">
                      <h4 className="text-xs font-black uppercase tracking-widest text-slate-400">Webhook Integration</h4>
                      <div className={`px-2 py-0.5 rounded-full text-[10px] font-black uppercase tracking-widest ${ws.auto_pull_enabled ? 'bg-emerald-500/10 text-emerald-400' : 'bg-white/5 text-slate-600'}`}>
                        {ws.auto_pull_enabled ? 'Active' : 'Disabled'}
                      </div>
                   </div>

                   {ws.auto_pull_enabled ? (
                     <div className="space-y-4">
                        <div className="relative group/url">
                          <p className="text-[10px] text-slate-500 mb-1.5 ml-1">Payload URL</p>
                          <div className="flex items-center gap-2 p-2 rounded-lg bg-black/40 border border-white/5 font-mono text-[10px] text-slate-400 overflow-hidden">
                            <span className="truncate flex-1">{getWebhookUrl(ws.id, ws.webhook_token)}</span>
                            <button 
                              onClick={() => copyToClipboard(getWebhookUrl(ws.id, ws.webhook_token), ws.id)}
                              className="p-1.5 rounded-md hover:bg-white/10 text-slate-500 hover:text-white transition-colors"
                            >
                              {copiedId === ws.id ? <Check size={14} className="text-emerald-400" /> : <Copy size={14} />}
                            </button>
                          </div>
                        </div>
                        <div className="flex items-center gap-2 text-[10px] text-slate-500 bg-white/5 p-3 rounded-lg border border-white/5">
                           <ShieldCheck size={14} className="text-emerald-500" />
                           <span>Includes secure authentication token</span>
                        </div>
                     </div>
                   ) : (
                     <div className="h-full flex flex-col items-center justify-center py-4 text-center">
                        <GitPullRequest size={32} className="text-slate-800 mb-2" />
                        <p className="text-xs text-slate-600">Auto-pull is disabled for this workspace.</p>
                        <button 
                          onClick={() => openEdit(ws)}
                          className="mt-2 text-[10px] font-bold text-indigo-400 hover:text-indigo-300 uppercase tracking-widest"
                        >
                          Enable Now
                        </button>
                     </div>
                   )}
                </div>
              </div>
            </div>
          ))
        )}
      </div>

      <Modal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        title={editingWs ? `Configure ${editingWs.display_name}` : 'Register New Workspace'}
      >
        <div className="space-y-6">
          <div className="grid gap-4 md:grid-cols-2">
            <label className="space-y-2">
              <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Workspace ID</span>
              <input 
                type="text" 
                value={form.id}
                disabled={Boolean(editingWs)}
                onChange={(e) => setForm({ ...form, id: e.target.value })}
                placeholder="sharedllm"
                className="glass-input w-full disabled:opacity-50"
              />
            </label>
            <label className="space-y-2">
              <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Display Name</span>
              <input 
                type="text" 
                value={form.display_name}
                onChange={(e) => setForm({ ...form, display_name: e.target.value })}
                placeholder="My Project"
                className="glass-input w-full"
              />
            </label>
          </div>

          <label className="space-y-2">
            <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Local Path (Relative to Root)</span>
            <div className="relative">
              <Folder size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-600" />
              <input 
                type="text" 
                value={form.local_path}
                onChange={(e) => setForm({ ...form, local_path: e.target.value })}
                placeholder="Code/MyProject"
                className="glass-input w-full pl-10"
              />
            </div>
            <p className="text-[10px] text-slate-600 italic">This path is relative to the backend's workspace directory.</p>
          </label>

          <div className="grid gap-4 md:grid-cols-2">
            <label className="space-y-2">
              <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Git Remote</span>
              <input 
                type="text" 
                value={form.git_remote}
                onChange={(e) => setForm({ ...form, git_remote: e.target.value })}
                placeholder="origin"
                className="glass-input w-full"
              />
            </label>
            <label className="space-y-2">
              <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Default Branch</span>
              <input 
                type="text" 
                value={form.default_branch}
                onChange={(e) => setForm({ ...form, default_branch: e.target.value })}
                placeholder="main"
                className="glass-input w-full"
              />
            </label>
          </div>

          <div className="p-4 rounded-2xl bg-indigo-500/5 border border-indigo-500/10 space-y-4">
             <div className="flex items-center justify-between">
                <div>
                   <h4 className="text-sm font-bold text-white flex items-center gap-2">
                      <GitPullRequest size={16} className="text-indigo-400" />
                      Automated Sync
                   </h4>
                   <p className="text-xs text-slate-500">Trigger `git pull` via webhook notification.</p>
                </div>
                <button 
                  onClick={() => setForm({ ...form, auto_pull_enabled: !form.auto_pull_enabled })}
                  className={`w-12 h-6 rounded-full transition-colors relative ${form.auto_pull_enabled ? 'bg-indigo-500' : 'bg-slate-800'}`}
                >
                  <div className={`absolute top-1 w-4 h-4 rounded-full bg-white transition-all ${form.auto_pull_enabled ? 'left-7' : 'left-1'}`} />
                </button>
             </div>

             {form.auto_pull_enabled && (
               <label className="space-y-2 block animate-in slide-in-from-top-2 duration-300">
                  <span className="text-[10px] font-black uppercase tracking-widest text-slate-400">Webhook Secret Token</span>
                  <div className="relative">
                    <ShieldCheck size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-600" />
                    <input 
                      type="text" 
                      value={form.webhook_token || ''}
                      onChange={(e) => setForm({ ...form, webhook_token: e.target.value })}
                      placeholder="Secret key for GitHub"
                      className="glass-input w-full pl-10"
                    />
                  </div>
               </label>
             )}
          </div>

          <div className="flex gap-3 pt-2">
            <button 
              onClick={() => setIsModalOpen(false)}
              className="glass-button flex-1 py-3"
            >
              Cancel
            </button>
            <button 
              onClick={() => {
                if (!form.id || !form.display_name || !form.local_path) {
                  toast.error('Required fields: ID, Name, Path');
                  return;
                }
                saveMutation.mutate(form);
              }}
              disabled={saveMutation.isPending}
              className="glass-button flex-1 py-3 bg-indigo-600/30 border-indigo-500/30 text-indigo-300 font-bold"
            >
              {saveMutation.isPending ? 'Saving...' : 'Save Workspace'}
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
};

export default Workspaces;
