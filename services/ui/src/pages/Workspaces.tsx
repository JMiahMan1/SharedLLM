import { useCallback, useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useAuth } from '../context/AuthContext';
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
  FolderOpen,
  Globe,
  ShieldCheck,
  ShieldAlert,
  RotateCcw,
  AlertTriangle,
  Star,
  Brain,
  Calendar,
  KeyRound,
  Package,
  RefreshCcw,
  Download,
  Eye,
  Archive
} from 'lucide-react';
import toast from 'react-hot-toast';
import { ARTIFACT_RE, artifactKind, downloadBlobUrl } from '../lib/artifactKinds';
import { api, type Workspace } from '../services/api';
import { formatDateTime } from '../lib/utils';
import Modal from '../components/ui/Modal';
import WorkspaceIDE from '../components/workspace/WorkspaceIDE';
import { WorkspaceSecrets } from '../components/workspace/WorkspaceSecrets';

const generateWebhookToken = () =>
  Math.random().toString(36).substring(2, 15) + Math.random().toString(36).substring(2, 15);

function AllArtifacts({ workspaces, onOpenInIDE }: { workspaces: Workspace[]; onOpenInIDE?: (ws: Workspace, path: string) => void }) {
  const [entriesByWs, setEntriesByWs] = useState<Record<string, { path: string; size: number }[]>>({});
  const [blobs, setBlobs] = useState<Record<string, { url: string; text?: string }>>({});
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [zipping, setZipping] = useState(false);
  const initialLoadDone = useRef(false);

  const loadAll = useCallback(async () => {
    setLoading(true);
    const out: Record<string, { path: string; size: number }[]> = {};
    for (const ws of workspaces) {
      try {
        const resp = await api.listWorkspaceFiles(ws.id, '.', true, 8);
        const entries = (resp.entries ?? [])
          .filter((e) => !e.is_dir && ARTIFACT_RE.test(e.path))
          .map((e) => ({ path: e.path, size: e.size ?? 0 }));
        if (entries.length > 0) out[ws.id] = entries;
      } catch {
        /* workspace may be unreachable — skip */
      }
    }
    setEntriesByWs(out);
    setLoading(false);
  }, [workspaces]);

  useEffect(() => {
    if (!initialLoadDone.current && workspaces.length > 0) {
      initialLoadDone.current = true;
      void loadAll();
    }
  }, [workspaces, loadAll]);

  const toggle = (wsId: string, path: string) => {
    setExpanded((prev) => ({ ...prev, [`${wsId}:${path}`]: !prev[`${wsId}:${path}`] }));
  };

  const getBlob = async (wsId: string, path: string) => {
    const key = `${wsId}:${path}`;
    if (blobs[key]) return blobs[key];
    const blob = await api.fetchWorkspaceFileRaw(wsId, path);
    const url = URL.createObjectURL(blob);
    let text: string | undefined;
    if (artifactKind(path) === 'text') {
      text = (await blob.text()).slice(0, 2000);
    }
    const entry = { url, text };
    setBlobs((prev) => ({ ...prev, [key]: entry }));
    return entry;
  };

  const loadBlob = async (wsId: string, path: string) => {
    const key = `${wsId}:${path}`;
    if (blobs[key]) {
      toggle(wsId, path);
      return;
    }
    try {
      await getBlob(wsId, path);
      setExpanded((prev) => ({ ...prev, [key]: true }));
    } catch (err) {
      console.error('Failed to load preview:', err);
      toast.error('Failed to load preview');
    }
  };

  const handleDownload = async (wsId: string, path: string) => {
    try {
      const entry = await getBlob(wsId, path);
      downloadBlobUrl(entry.url, path);
    } catch (err) {
      console.error('Failed to download file:', err);
      toast.error('Failed to download file');
    }
  };

  const downloadSelectedZip = async () => {
    const picks: { wsId: string; path: string }[] = [];
    for (const [key, isSel] of Object.entries(selected)) {
      if (isSel) {
        const sep = key.indexOf(':');
        picks.push({ wsId: key.slice(0, sep), path: key.slice(sep + 1) });
      }
    }
    if (picks.length === 0) {
      toast.error('Select at least one artifact to download');
      return;
    }
    setZipping(true);
    try {
      const blob = await api.zipWorkspaceFiles(
        picks.map((p) => ({ workspace_id: p.wsId, relative_path: p.path }))
      );
      downloadBlobUrl(URL.createObjectURL(blob), 'mission-artifacts.zip');
    } catch (err) {
      console.error('Failed to create zip:', err);
      toast.error('Failed to create zip archive');
    } finally {
      setZipping(false);
    }
  };

  const artifactCount = Object.values(entriesByWs).reduce((n, arr) => n + arr.length, 0);
  const selectedCount = Object.values(selected).filter(Boolean).length;

  return (
    <div className="glass-panel rounded-2xl border border-slate-700/50 p-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Package size={18} className="text-indigo-400" />
          <h3 className="text-lg font-bold text-white">All Mission Artifacts</h3>
          <span className="text-xs text-slate-400">({artifactCount} files across {Object.keys(entriesByWs).length} workspaces)</span>
        </div>
        <button
          onClick={() => void loadAll()}
          disabled={loading}
          className="glass-button px-4 py-1.5 bg-slate-800/40 border-slate-700/50 text-slate-300 font-bold text-sm flex items-center gap-2 disabled:opacity-40"
        >
          <RefreshCcw size={14} />
          {loading ? 'Scanning...' : 'Refresh'}
        </button>
        <button
          onClick={() => void downloadSelectedZip()}
          disabled={zipping || selectedCount === 0}
          className="glass-button px-4 py-1.5 bg-indigo-600/30 border-indigo-500/40 text-indigo-200 font-bold text-sm flex items-center gap-2 disabled:opacity-40 hover:bg-indigo-600/40"
          title="Download selected artifacts as a zip archive"
        >
          <Archive size={14} />
          {zipping ? 'Zipping...' : `Download ${selectedCount > 0 ? `${selectedCount} ` : ''}(zip)`}
        </button>
      </div>
      {loading ? (
        <p className="mt-4 text-sm text-slate-400">Scanning workspaces for audio, video, image, PDF and document artifacts...</p>
      ) : artifactCount === 0 ? (
        <p className="mt-4 text-sm text-slate-400">No artifacts found yet — run a Raven mission that produces files.</p>
      ) : (
        <div className="mt-4 space-y-3">
          {Object.entries(entriesByWs).map(([wsId, files]) => (
            <div key={wsId} className="rounded-xl border border-slate-700/40 bg-slate-900/40">
              <div className="px-3 py-2 border-b border-slate-700/40 font-mono text-xs text-indigo-300">{wsId}</div>
              <div className="divide-y divide-slate-800/60">
                {files.map((f) => {
                  const key = `${wsId}:${f.path}`;
                  const kind = artifactKind(f.path);
                  const b = blobs[key];
                  const isOpen = !!expanded[key];
                  const ws = workspaces.find((w) => w.id === wsId);
                  return (
                    <div key={key} className="px-3 py-2">
                      <div className="flex items-center gap-2">
                        <input
                          type="checkbox"
                          checked={!!selected[key]}
                          onChange={(e) => setSelected((prev) => ({ ...prev, [key]: e.target.checked }))}
                          className="accent-indigo-500 w-3.5 h-3.5"
                          title="Select for zip download"
                        />
                        <span className="text-slate-400 text-xs">{kind}</span>
                        <button
                          onClick={() => {
                            if (onOpenInIDE && ws) {
                              onOpenInIDE(ws, f.path);
                            } else {
                              void loadBlob(wsId, f.path);
                            }
                          }}
                          className="truncate text-sm text-slate-200 hover:text-white text-left flex-1"
                          title={onOpenInIDE ? 'Open in IDE' : 'Preview'}
                        >
                          {f.path.split('/').pop()}
                        </button>
                        <span className="text-xs text-slate-500">{(f.size / 1024).toFixed(1)} KB</span>
                        {onOpenInIDE && ws && (
                          <button
                            onClick={() => onOpenInIDE(ws, f.path)}
                            className="px-2 py-1 text-[10px] text-indigo-300 border border-indigo-500/30 hover:bg-indigo-500/10 rounded font-semibold"
                            title={`Open ${f.path.split('/').pop()} in ${ws.display_name || ws.id}`}
                          >
                            Open in IDE
                          </button>
                        )}
                        <button
                          onClick={() => void loadBlob(wsId, f.path)}
                          className="p-1 text-slate-400 hover:text-white hover:bg-white/10 rounded"
                          title="Preview"
                        >
                          <Eye size={14} />
                        </button>
                        <button
                          onClick={() => void handleDownload(wsId, f.path)}
                          className="p-1 text-slate-400 hover:text-white hover:bg-white/10 rounded"
                          title="Download"
                        >
                          <Download size={14} />
                        </button>
                      </div>
                      {isOpen && b?.url && (
                        <div className="mt-2">
                          {kind === 'audio' && <audio controls src={b.url} className="w-full max-w-md" />}
                          {kind === 'video' && <video controls src={b.url} className="max-h-56" />}
                          {kind === 'image' && <img src={b.url} alt={f.path} className="max-h-56 object-contain rounded-lg" />}
                          {kind === 'pdf' && <iframe src={b.url} title={f.path} className="h-48 w-full rounded-lg" />}
                          {kind === 'text' && <pre className="max-h-40 overflow-auto text-xs text-slate-300 whitespace-pre-wrap">{b.text ?? ''}</pre>}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

const Workspaces = () => {
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const isAdmin = user?.is_admin ?? false;
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [editingWs, setEditingWs] = useState<Workspace | null>(null);
  const [ideWs, setIdeWs] = useState<Workspace | null>(null);
  const [ideInitialPath, setIdeInitialPath] = useState<string | null>(null);
  const [secretsWs, setSecretsWs] = useState<Workspace | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const [form, setForm] = useState<Partial<Workspace>>({
    id: '',
    display_name: '',
    local_path: '',
    git_remote: 'origin',
    default_branch: 'main',
    sync_mode: 'local_git_authoritative',
    auto_pull_enabled: false,
    webhook_token: '',
    repo_url: ''
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
      return api.createWorkspace(data as Partial<Workspace> & { id: string });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['workspaces'] });
      setIsModalOpen(false);
      setEditingWs(null);
      toast.success(editingWs ? 'Workspace updated' : 'Workspace created');
    },
    onError: (err: Error) => toast.error(err.message || 'Failed to save workspace'),
  });

  // Inline toggles (star / share) patch a single field — they must UPDATE, never
  // create, even when the edit modal is closed.
  const updateMutation = useMutation({
    mutationFn: (data: { id: string } & Partial<Workspace>) => {
      const { id, ...patch } = data;
      return api.updateWorkspace(id, patch);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['workspaces'] });
    },
    onError: (err: Error) => toast.error(err.message || 'Failed to update workspace'),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.deleteWorkspace(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['workspaces'] });
      toast.success('Workspace deleted');
    },
    onError: (err: Error) => toast.error(err.message || 'Failed to delete workspace'),
  });

  const pullMutation = useMutation({
    mutationFn: (id: string) => api.pullWorkspace(id),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['workspaces'] });
      const note = data.recovery_note ? ` (${data.recovery_note})` : '';
      toast.success(`Successfully pulled latest changes on ${data.branch}${note}`);
    },
    onError: (err: Error) => toast.error(err.message || 'Git pull failed'),
  });

  const revertMutation = useMutation({
    mutationFn: (id: string) => api.revertWorkspace(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['workspaces'] });
      toast.success('Workspace reverted to previous stable state');
    },
    onError: (err: Error) => toast.error(err.message || 'Revert failed'),
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
      webhook_token: generateWebhookToken(),
      repo_url: ''
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

  const draftWorkspaceId = editingWs?.id || form.id || 'workspace-id';
  const draftWebhookToken = form.webhook_token || '';
  const draftWebhookUrl = getWebhookUrl(draftWorkspaceId, draftWebhookToken);

  return (
    <div className="space-y-8 pb-12 animate-in fade-in duration-500">
      <header className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
        <div>
          <h2 className="text-3xl font-bold tracking-tight text-white">{isAdmin ? 'Workspaces' : 'My Workspaces'}</h2>
          <p className="mt-1 text-slate-400">{isAdmin ? 'Manage repository locations and automated sync triggers.' : 'View your workspaces and shared resources.'}</p>
        </div>
        {isAdmin && (
          <div className="flex items-center gap-3">
            <button 
              onClick={() => {
                toast.promise(
                  Promise.all(workspaces.map(ws => api.pullWorkspace(ws.id))),
                  {
                    loading: 'Synchronizing all repositories...',
                    success: 'All workspaces up to date',
                    error: 'One or more pulls failed'
                  }
                ).then(() => queryClient.invalidateQueries({ queryKey: ['workspaces'] }));
              }}
              className="glass-button px-6 py-2.5 bg-slate-800/40 border-slate-700/50 text-slate-300 font-bold flex items-center gap-2"
            >
              <GitPullRequest size={18} />
              Sync All
            </button>
            <button 
              onClick={openCreate}
              className="glass-button px-6 py-2.5 bg-indigo-600/20 border-indigo-500/30 text-indigo-300 font-bold flex items-center gap-2"
            >
              <Plus size={18} />
              Add Repository
            </button>
          </div>
        )}
      </header>

      <AllArtifacts
        workspaces={workspaces}
        onOpenInIDE={(ws, path) => {
          setIdeWs(ws);
          setIdeInitialPath(path);
        }}
      />

      {isAdmin && (
        <div className="glass-card p-4 border-l-4 border-l-purple-500 flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-xl bg-purple-500/10">
              <Brain size={20} className="text-purple-400" />
            </div>
            <div>
              <h3 className="text-sm font-bold text-white">Raven Autonomous Scan</h3>
              <p className="text-[10px] text-slate-400">Deploy Raven to scan workspaces for anomalies, security issues, and stability.</p>
            </div>
          </div>
          <button
            onClick={() => {
              toast.promise(
                api.createUserMission('System diagnostic and maintenance scan', 2),
                {
                  loading: 'Launching Raven mission...',
                  success: (data) => {
                    return `Raven mission #${data.mission.id} deployed`;
                  },
                  error: 'Failed to launch Raven mission'
                }
              );
            }}
            className="glass-button px-6 py-2.5 bg-purple-600/20 border-purple-500/30 text-purple-300 font-bold flex items-center gap-2 hover:bg-purple-600/30 transition-colors"
          >
            <Brain size={18} />
            Launch Scan
          </button>
        </div>
      )}

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
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <h3 className="text-xl font-bold text-white truncate">{ws.display_name}</h3>
                          {ws.is_default && (
                            <span className="flex items-center gap-1 bg-emerald-500/10 text-emerald-400 px-2 py-0.5 rounded text-[10px] font-black uppercase tracking-widest border border-emerald-500/20">
                              <Star size={10} /> Default
                            </span>
                          )}
                          {ws.owner_user === 'default' && (
                            <span className="flex items-center gap-1 bg-blue-500/10 text-blue-400 px-2 py-0.5 rounded text-[10px] font-black uppercase tracking-widest border border-blue-500/20">
                              <Globe size={10} /> Shared
                            </span>
                          )}
                          {ws.quarantined && (
                            <span className="flex items-center gap-1 bg-red-500/10 text-red-400 px-2 py-0.5 rounded text-[10px] font-black uppercase tracking-widest border border-red-500/20 animate-pulse">
                              <ShieldAlert size={10} /> Quarantined
                            </span>
                          )}
                        </div>
                        <p className="text-xs font-mono text-slate-500 truncate">ID: {ws.id}</p>
                      </div>
                    </div>
                    {isAdmin && (
                      <div className="flex items-center gap-2">
                        <button 
                          onClick={() => {
                            updateMutation.mutate({ id: ws.id, is_default: !ws.is_default });
                          }}
                          className={`p-2 rounded-xl transition-colors ${ws.is_default ? 'text-emerald-400 hover:bg-emerald-500/10' : 'text-slate-500 hover:text-emerald-400 hover:bg-white/5'}`}
                          title={ws.is_default ? 'Unset as default' : 'Set as default'}
                        >
                          <Star size={18} />
                        </button>
                        <button 
                          onClick={() => {
                            updateMutation.mutate({ id: ws.id, owner_user: ws.owner_user === 'default' ? null : 'default' });
                          }}
                          className={`p-2 rounded-xl transition-colors ${ws.owner_user === 'default' ? 'text-blue-400 hover:bg-blue-500/10' : 'text-slate-500 hover:text-blue-400 hover:bg-white/5'}`}
                          title={ws.owner_user === 'default' ? 'Unshare (make private)' : 'Share with all users'}
                        >
                          <Globe size={18} />
                        </button>
                        <button 
                          onClick={() => setIdeWs(ws)}
                          className="p-2 rounded-xl text-slate-500 hover:text-emerald-400 hover:bg-white/5 transition-colors"
                          title="Open workspace files (IDE)"
                        >
                          <FolderOpen size={18} />
                        </button>
                        <button
                          onClick={() => setSecretsWs(ws)}
                          className="p-2 rounded-xl text-slate-500 hover:text-indigo-400 hover:bg-white/5 transition-colors"
                          title="Secrets & Environment"
                        >
                          <KeyRound size={18} />
                        </button>
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
                    )}
                  </div>

                  <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-1">
                      <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">Path</p>
                      <div className="flex items-center gap-2 text-sm text-slate-300">
                        <Folder size={14} className="text-slate-600" />
                        <span className="font-mono truncate">{ws.local_path}</span>
                      </div>
                    </div>
                    <div className="space-y-1">
                      <div className="flex items-center justify-between">
                        <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">Git Status</p>
                        <button 
                          onClick={() => pullMutation.mutate(ws.id)}
                          disabled={pullMutation.isPending}
                          className="text-[10px] font-black uppercase tracking-widest text-indigo-400 hover:text-indigo-300 transition-colors disabled:opacity-50"
                        >
                          {pullMutation.isPending ? 'Pulling...' : 'Pull Now'}
                        </button>
                      </div>
                      <div className="flex items-center gap-2 text-sm text-slate-300">
                        <GitPullRequest size={14} className="text-slate-600" />
                        <span>{ws.git_remote}/{ws.default_branch}</span>
                      </div>
                    </div>
                    <div className="space-y-1">
                      <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">Created</p>
                      <div className="flex items-center gap-2 text-sm text-slate-300">
                        <Calendar size={14} className="text-slate-600" />
                        <span>{formatDateTime(ws.created_at) ?? 'Unknown'}</span>
                      </div>
                    </div>
                    {ws.repo_url && (
                      <div className="space-y-1 col-span-2">
                        <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">Source Repository</p>
                        <div className="flex items-center gap-2 text-sm text-slate-300">
                          <Globe size={14} className="text-slate-600" />
                          <span className="truncate">{ws.repo_url}</span>
                        </div>
                      </div>
                    )}
                    {Array.isArray(ws.excludes) && ws.excludes.length > 0 && (
                      <div className="space-y-1 col-span-2">
                        <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">Sync Exclusions</p>
                        <div className="flex flex-wrap gap-1.5">
                          {ws.excludes.map((ex, idx) => (
                            <span key={idx} className="bg-white/5 border border-white/10 px-1.5 py-0.5 rounded text-[9px] font-bold text-slate-400">
                              {ex}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>

                  {ws.quarantined && (
                    <div className="mt-6 p-4 rounded-2xl bg-red-500/5 border border-red-500/10 flex flex-col sm:flex-row items-center justify-between gap-4">
                      <div className="flex items-center gap-3">
                        <AlertTriangle className="text-red-400" size={20} />
                        <div>
                          <p className="text-xs font-bold text-white uppercase tracking-widest">Autonomous Lockdown</p>
                          <p className="text-[10px] text-slate-400">Raven flagged this workspace after Mission #{ws.last_raven_mission_id}. Operations are restricted.</p>
                        </div>
                      </div>
                  <div className="flex items-center gap-2">
                     <button 
                       onClick={() => {
                         if (confirm('Are you sure you want to revert this workspace to its previous stable state? This will discard the last Raven patch.')) {
                           revertMutation.mutate(ws.id);
                         }
                       }}
                       disabled={revertMutation.isPending}
                       className="glass-button bg-emerald-500/10 border-emerald-500/20 text-emerald-300 hover:bg-emerald-500/20 px-4 py-2 flex items-center gap-2 text-[10px] font-black uppercase tracking-widest"
                     >
                       <RotateCcw size={12} className={revertMutation.isPending ? 'animate-spin' : ''} />
                       {revertMutation.isPending ? 'Reverting...' : 'Rollback & Restore'}
                     </button>
                     {isAdmin && (
                       <button 
                         onClick={() => {
                           toast.promise(
                             api.createUserMission(`Investigate and fix quarantined workspace: ${ws.display_name}`, 3),
                             {
                               loading: 'Sending Raven to investigate...',
                               success: (data) => `Raven mission #${data.mission.id} deployed`,
                               error: 'Failed to launch Raven mission'
                             }
                           );
                         }}
                         className="glass-button bg-purple-500/10 border-purple-500/20 text-purple-300 hover:bg-purple-500/20 px-4 py-2 flex items-center gap-2 text-[10px] font-black uppercase tracking-widest"
                       >
                         <Brain size={12} />
                         Send Raven
                       </button>
                     )}
                   </div>
                    </div>
                  )}
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
                            <span className="truncate flex-1 break-all">{getWebhookUrl(ws.id, ws.webhook_token)}</span>
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

      {ideWs && (
        <WorkspaceIDE
          workspace={ideWs}
          initialPath={ideInitialPath}
          onClose={() => {
            setIdeWs(null);
            setIdeInitialPath(null);
          }}
        />
      )}
      {secretsWs && (
        <WorkspaceSecrets workspace={secretsWs} onClose={() => setSecretsWs(null)} />
      )}

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
                placeholder="project-id"
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
            <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Repository URL (GitHub/GitLab)</span>
            <div className="relative">
              <Globe size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-600" />
              <input 
                type="text" 
                value={form.repo_url || ''}
                onChange={(e) => setForm({ ...form, repo_url: e.target.value })}
                placeholder="https://github.com/user/repo.git"
                className="glass-input w-full pl-10"
              />
            </div>
            <p className="text-[10px] text-slate-600 italic">Required for autonomous bootstrapping and fresh pulls.</p>
          </label>

          <label className="space-y-2">
            <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Path</span>
            <div className="relative">
              <Folder size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-600" />
              <input 
                type="text" 
                value={form.local_path || ''}
                onChange={(e) => setForm({ ...form, local_path: e.target.value })}
                placeholder="your/repository/folder (relative) or /absolute/path (system)"
                className="glass-input w-full pl-10"
              />
            </div>
            <p className="text-[10px] text-slate-600 italic">Relative path for user workspaces, absolute path for system workspaces (e.g. /host-repo). Used for all internal operations.</p>
          </label>

          <div className="grid gap-4 md:grid-cols-2">
            <label className="space-y-2">
              <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Git Remote</span>
              <input 
                type="text" 
                value={form.git_remote || ''}
                onChange={(e) => setForm({ ...form, git_remote: e.target.value })}
                placeholder="origin"
                className="glass-input w-full"
              />
            </label>
            <label className="space-y-2">
              <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Default Branch</span>
              <input 
                type="text" 
                value={form.default_branch || ''}
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
                  type="button"
                  aria-label="Toggle automated sync"
                  onClick={() => setForm({ ...form, auto_pull_enabled: !form.auto_pull_enabled })}
                  className={`w-12 h-6 rounded-full transition-colors relative ${form.auto_pull_enabled ? 'bg-indigo-500' : 'bg-slate-800'}`}
                >
                  <div className={`absolute top-1 w-4 h-4 rounded-full bg-white transition-all ${form.auto_pull_enabled ? 'left-7' : 'left-1'}`} />
                </button>
             </div>

             <div className="flex items-center justify-between">
                <div>
                   <h4 className="text-sm font-bold text-white flex items-center gap-2">
                      <Save size={16} className="text-indigo-400" />
                      Nextcloud Backup
                   </h4>
                   <p className="text-xs text-slate-500">Mirror local changes to Nextcloud provider.</p>
                </div>
                <button 
                  type="button"
                  aria-label="Toggle nextcloud backup"
                  onClick={() => setForm({ ...form, auto_backup_enabled: !form.auto_backup_enabled })}
                  className={`w-12 h-6 rounded-full transition-colors relative ${form.auto_backup_enabled ? 'bg-indigo-500' : 'bg-slate-800'}`}
                >
                  <div className={`absolute top-1 w-4 h-4 rounded-full bg-white transition-all ${form.auto_backup_enabled ? 'left-7' : 'left-1'}`} />
                </button>
             </div>

              <div className="pt-2 border-t border-white/5">
                <p className="text-[10px] font-black uppercase tracking-widest text-slate-500 mb-2">Sync Exclusions</p>
                <div className="flex flex-wrap gap-2 mb-3">
                   {(Array.isArray(form.excludes) ? form.excludes : []).map((ex, idx) => (
                      <span key={idx} className="flex items-center gap-1.5 px-2 py-1 rounded-lg bg-indigo-500/10 text-indigo-300 text-[10px] font-bold border border-indigo-500/20">
                         {ex}
                         <button 
                            type="button"
                            onClick={() => setForm({ ...form, excludes: (Array.isArray(form.excludes) ? form.excludes : []).filter((_, i) => i !== idx) })}
                            className="hover:text-red-400"
                         >
                            <Trash2 size={10} />
                         </button>
                      </span>
                   ))}
                   {(!Array.isArray(form.excludes) || form.excludes.length === 0) && (
                      <p className="text-[10px] text-slate-600 italic">No custom exclusions set.</p>
                   )}
                </div>
                <div className="flex gap-2">
                   <input 
                      type="text"
                      placeholder="Add directory to exclude (e.g. .git)"
                      className="glass-input flex-1 text-[11px] py-2"
                      onKeyDown={(e) => {
                         if (e.key === 'Enter') {
                            e.preventDefault();
                            const val = e.currentTarget.value.trim();
                            const currentExcludes = Array.isArray(form.excludes) ? form.excludes : [];
                            if (val && !currentExcludes.includes(val)) {
                               setForm({ ...form, excludes: [...currentExcludes, val] });
                               e.currentTarget.value = '';
                            }
                         }
                      }}
                   />
                </div>
              </div>

             {form.auto_pull_enabled && (
               <div className="space-y-4 animate-in slide-in-from-top-2 duration-300">
                  <label className="space-y-2 block">
                    <span className="text-[10px] font-black uppercase tracking-widest text-slate-400">Webhook Secret Token</span>
                    <div className="relative">
                      <ShieldCheck size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-600" />
                      <input 
                        type="text" 
                        value={form.webhook_token || ''}
                        onChange={(e) => setForm({ ...form, webhook_token: e.target.value })}
                        placeholder="Secret key for GitHub or GitLab"
                        className="glass-input w-full pl-10 pr-24"
                      />
                      <button
                        type="button"
                        onClick={() => setForm({ ...form, webhook_token: generateWebhookToken() })}
                        className="absolute right-2 top-1/2 -translate-y-1/2 rounded-md px-2 py-1 text-[10px] font-black uppercase tracking-widest text-indigo-300 hover:bg-white/5"
                      >
                        Regenerate
                      </button>
                    </div>
                  </label>

                  <div className="space-y-3 rounded-xl border border-white/10 bg-black/20 p-4">
                    <div className="flex items-center justify-between gap-2">
                      <div>
                        <p className="text-[10px] font-black uppercase tracking-widest text-slate-400">Webhook Setup</p>
                        <p className="text-xs text-slate-500">Use these values when creating the repository webhook.</p>
                      </div>
                      <button
                        type="button"
                        onClick={() => copyToClipboard(draftWebhookUrl, `draft-${draftWorkspaceId}`)}
                        className="rounded-md p-2 text-slate-500 transition-colors hover:bg-white/5 hover:text-white"
                      >
                        {copiedId === `draft-${draftWorkspaceId}` ? <Check size={14} className="text-emerald-400" /> : <Copy size={14} />}
                      </button>
                    </div>

                    <div className="space-y-2">
                      <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">Payload URL</p>
                      <div className="flex items-center gap-2 rounded-lg border border-white/5 bg-black/40 p-2">
                        <div className="min-w-0 flex-1 font-mono text-[10px] text-slate-300 break-all">
                          {draftWebhookUrl}
                        </div>
                        <button
                          type="button"
                          onClick={() => copyToClipboard(draftWebhookUrl, `draft-url-${draftWorkspaceId}`)}
                          className="rounded-md p-2 text-slate-500 transition-colors hover:bg-white/5 hover:text-white"
                          aria-label="Copy payload URL"
                        >
                          {copiedId === `draft-url-${draftWorkspaceId}` ? <Check size={14} className="text-emerald-400" /> : <Copy size={14} />}
                        </button>
                      </div>
                    </div>

                    <div className="space-y-2">
                      <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">Secret Token</p>
                      <div className="flex items-center gap-2 rounded-lg border border-white/5 bg-black/40 p-2">
                        <div className="min-w-0 flex-1 font-mono text-[10px] text-slate-300 break-all">
                          {draftWebhookToken || 'Generate or enter a token to use as the webhook secret.'}
                        </div>
                        <button
                          type="button"
                          onClick={() => copyToClipboard(draftWebhookToken || '', `draft-secret-${draftWorkspaceId}`)}
                          className="rounded-md p-2 text-slate-500 transition-colors hover:bg-white/5 hover:text-white"
                          aria-label="Copy secret token"
                          disabled={!draftWebhookToken}
                        >
                          {copiedId === `draft-secret-${draftWorkspaceId}` ? <Check size={14} className="text-emerald-400" /> : <Copy size={14} />}
                        </button>
                      </div>
                    </div>

                    <div className="grid gap-3 md:grid-cols-2">
                      <div className="rounded-lg border border-white/5 bg-white/[0.03] p-3">
                        <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">GitHub</p>
                        <p className="mt-1 text-xs text-slate-400">Content type: `application/json`</p>
                        <p className="text-xs text-slate-400">Event: `Pushes`</p>
                        <p className="text-xs text-slate-400">Secret: use the token above</p>
                      </div>
                      <div className="rounded-lg border border-white/5 bg-white/[0.03] p-3">
                        <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">GitLab</p>
                        <p className="mt-1 text-xs text-slate-400">Trigger: `Push events`</p>
                        <p className="text-xs text-slate-400">Secret token: use the token above</p>
                        <p className="text-xs text-slate-400">SSL verification: leave enabled</p>
                      </div>
                    </div>

                    <p className="text-[10px] text-slate-500">
                      Save the workspace first if you are creating a new one. The final webhook URL uses the workspace ID shown above.
                    </p>
                  </div>
               </div>
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
