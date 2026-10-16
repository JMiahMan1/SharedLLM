import { useCallback, useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
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
  Archive,
  Cloud
} from 'lucide-react';
import toast from 'react-hot-toast';
import { ARTIFACT_RE, artifactKind, downloadBlobUrl } from '../lib/artifactKinds';
import { api, type Workspace } from '../services/api';
import type { WorkspaceSyncMode } from '../types/api';
import { formatDateTime } from '../lib/utils';
import Modal from '../components/ui/Modal';
import Toggle from '../components/ui/Toggle';
import WorkspaceIDE from '../components/workspace/WorkspaceIDE';
import { WorkspaceSecrets } from '../components/workspace/WorkspaceSecrets';
import {
  hydrateWorkspaceSessions,
  mergeWorkspaceSessions,
  normaliseWorkspaceSession,
  readWorkspaceSession,
  type WorkspaceSession,
} from '../components/workspace/workspaceSession';

/** A webhook secret: 32 random bytes from the platform CSPRNG, as hex. */
const generateWebhookToken = () =>
  Array.from(crypto.getRandomValues(new Uint8Array(32)), (b) => b.toString(16).padStart(2, '0')).join('');

/** Icon buttons reach a 44px touch target on phones without growing on desktop. */
const ICON_BUTTON = 'p-2 rounded-xl transition-colors pointer-coarse:min-h-11 pointer-coarse:min-w-11 inline-flex items-center justify-center';

function AllArtifacts({ workspaces, onOpenInIDE }: { workspaces: Workspace[]; onOpenInIDE?: (ws: Workspace, path: string) => void }) {
  const [entriesByWs, setEntriesByWs] = useState<Record<string, { path: string; size: number }[]>>({});
  const [blobs, setBlobs] = useState<Record<string, { url: string; text?: string }>>({});
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [zipping, setZipping] = useState(false);

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
        /* workspace may be unreachable; skip */
      }
    }
    setEntriesByWs(out);
    setLoading(false);
  }, [workspaces]);

  // Load artifacts when workspace list changes (tracked by ID comparison)
  const workspaceIds = useRef<string[]>([]);
  useEffect(() => {
    const currentIds = workspaces.map((w) => w.id).sort();
    const lastIds = workspaceIds.current;
    if (currentIds.length !== lastIds.length || currentIds.some((id, i) => id !== lastIds[i])) {
      workspaceIds.current = currentIds;
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
    <div className="glass-panel rounded-2xl border border-slate-700/50 p-4 sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
          <Package size={18} className="text-indigo-400 shrink-0" />
          <h3 className="text-lg font-bold text-white">All Mission Artifacts</h3>
          <span className="text-xs text-slate-400">({artifactCount} files across {Object.keys(entriesByWs).length} workspaces)</span>
        </div>
        <div className="flex flex-wrap items-center gap-2">
        <button
          onClick={() => void loadAll()}
          disabled={loading}
          className="glass-button px-4 py-1.5 pointer-coarse:min-h-11 bg-slate-800/40 border-slate-700/50 text-slate-300 font-bold text-sm flex items-center gap-2 disabled:opacity-40"
        >
          <RefreshCcw size={14} />
          {loading ? 'Scanning...' : 'Refresh'}
        </button>
        <button
          onClick={() => void downloadSelectedZip()}
          disabled={zipping || selectedCount === 0}
          className="glass-button px-4 py-1.5 pointer-coarse:min-h-11 bg-indigo-600/30 border-indigo-500/40 text-indigo-200 font-bold text-sm flex items-center gap-2 disabled:opacity-40 hover:bg-indigo-600/40"
          title="Download selected artifacts as a zip archive"
        >
          <Archive size={14} />
          {zipping ? 'Zipping...' : `Download ${selectedCount > 0 ? `${selectedCount} ` : ''}(zip)`}
        </button>
        </div>
      </div>
      {loading ? (
        <p className="mt-4 text-sm text-slate-400">Scanning workspaces for audio, video, image, PDF and document artifacts...</p>
      ) : artifactCount === 0 ? (
        <p className="mt-4 text-sm text-slate-400">No artifacts found yet. Run a Raven mission that produces files.</p>
      ) : (
        <div className="mt-4 space-y-3">
          {Object.entries(entriesByWs).map(([wsId, files]) => (
            <div key={wsId} className="rounded-xl border border-slate-700/40 bg-slate-900/40">
              <div className="px-3 py-2 border-b border-slate-700/40 font-mono text-xs text-indigo-300 truncate">{wsId}</div>
              <div className="divide-y divide-slate-800/60">
                {files.map((f) => {
                  const key = `${wsId}:${f.path}`;
                  const kind = artifactKind(f.path);
                  const b = blobs[key];
                  const isOpen = !!expanded[key];
                  const ws = workspaces.find((w) => w.id === wsId);
                  return (
                    <div key={key} className="px-3 py-2">
                      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                        <label className="inline-flex items-center justify-center pointer-coarse:min-h-11 pointer-coarse:min-w-11">
                          <input
                            type="checkbox"
                            checked={!!selected[key]}
                            onChange={(e) => setSelected((prev) => ({ ...prev, [key]: e.target.checked }))}
                            className="accent-indigo-500 w-3.5 h-3.5 pointer-coarse:w-5 pointer-coarse:h-5"
                            aria-label={`Select ${f.path} for zip download`}
                          />
                        </label>
                        <span className="text-slate-400 text-xs">{kind}</span>
                        <button
                          onClick={() => {
                            if (onOpenInIDE && ws) {
                              onOpenInIDE(ws, f.path);
                            } else {
                              void loadBlob(wsId, f.path);
                            }
                          }}
                          className="truncate text-sm text-slate-200 hover:text-white text-left flex-1 min-w-[8rem] pointer-coarse:min-h-11"
                          title={onOpenInIDE ? 'Open in IDE' : 'Preview'}
                        >
                          {f.path.split('/').pop()}
                        </button>
                        <span className="text-xs text-slate-500">{(f.size / 1024).toFixed(1)} KB</span>
                        {onOpenInIDE && ws && (
                          <button
                            onClick={() => onOpenInIDE(ws, f.path)}
                            className="px-2 py-1 pointer-coarse:min-h-11 text-[10px] text-indigo-300 border border-indigo-500/30 hover:bg-indigo-500/10 rounded font-semibold"
                            title={`Open ${f.path.split('/').pop()} in ${ws.display_name || ws.id}`}
                          >
                            Open in IDE
                          </button>
                        )}
                        <button
                          onClick={() => void loadBlob(wsId, f.path)}
                          className={`${ICON_BUTTON} p-1 text-slate-400 hover:text-white hover:bg-white/10`}
                          title="Preview"
                          aria-label={`Preview ${f.path}`}
                        >
                          <Eye size={14} />
                        </button>
                        <button
                          onClick={() => void handleDownload(wsId, f.path)}
                          className={`${ICON_BUTTON} p-1 text-slate-400 hover:text-white hover:bg-white/10`}
                          title="Download"
                          aria-label={`Download ${f.path}`}
                        >
                          <Download size={14} />
                        </button>
                      </div>
                      {isOpen && b?.url && (
                        <div className="mt-2">
                          {kind === 'audio' && <audio controls src={b.url} className="w-full max-w-md" />}
                          {kind === 'video' && <video controls src={b.url} className="max-h-56 max-w-full" />}
                          {kind === 'image' && <img src={b.url} alt={f.path} className="max-h-56 max-w-full object-contain rounded-lg" />}
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

const STORAGE_OPTIONS: { value: WorkspaceSyncMode; label: string; hint: string }[] = [
  { value: 'local_git_authoritative', label: 'Git', hint: 'A git checkout whose own history is the source of truth. Push webhooks are ignored; pull by hand. A Nextcloud folder, if set, can receive backups.' },
  { value: 'git', label: 'Git (follows remote)', hint: 'A git checkout that fast-forwards from its remote whenever a push webhook arrives.' },
  { value: 'nextcloud', label: 'Nextcloud', hint: 'No git. The workspace is a two-way synced copy of a Nextcloud folder, synced after every change and every few minutes.' },
  { value: 'git_and_nextcloud', label: 'Git + Nextcloud', hint: 'A git checkout that follows its remote and is also two-way synced with a Nextcloud folder (.git itself never syncs).' },
];

const usesNextcloud = (mode?: string | null) => mode === 'nextcloud' || mode === 'git_and_nextcloud';
const usesGit = (mode?: string | null) => mode !== 'nextcloud';
/** Whether a push webhook pulls this workspace (the runtime skips authoritative checkouts). */
const followsRemote = (mode?: string | null) => mode === 'git' || mode === 'git_and_nextcloud';
const storageValue = (mode?: string | null): WorkspaceSyncMode =>
  (mode as WorkspaceSyncMode) || 'local_git_authoritative';

/**
 * The fields the form may change. The list response also carries values the
 * runtime computes (resolved paths, effective capabilities, sync status...);
 * sending those back would store them as if an admin had set them.
 */
const EDITABLE_FIELDS = [
  'display_name', 'local_path', 'nextcloud_path', 'repo_url', 'git_remote', 'default_branch',
  'sync_mode', 'auto_pull_enabled', 'auto_backup_enabled', 'excludes',
] as const;

function workspacePayload(form: Partial<Workspace>, creating: boolean): Partial<Workspace> {
  const out: Partial<Workspace> = {};
  for (const key of EDITABLE_FIELDS) {
    if (form[key] !== undefined) (out as Record<string, unknown>)[key] = form[key];
  }
  // An empty token field on edit means "keep the saved one" (it is never sent to the browser).
  if (form.webhook_token) out.webhook_token = form.webhook_token;
  if (creating) {
    out.id = form.id;
    out.scope = form.scope;
    if (!out.local_path) delete out.local_path; // the runtime derives it from the id
  }
  return out;
}

/** The form a new workspace starts from, with a fresh webhook secret. */
const newWorkspaceForm = (): Partial<Workspace> => ({
  id: '',
  display_name: '',
  local_path: '',
  git_remote: 'origin',
  default_branch: 'main',
  sync_mode: 'local_git_authoritative',
  auto_pull_enabled: false,
  auto_backup_enabled: false,
  webhook_token: generateWebhookToken(),
  repo_url: '',
  scope: 'user',
});

/** Last Nextcloud sync of a workspace, as recorded by the runtime. */
function NextcloudSyncStatus({ ws }: { ws: Workspace }) {
  if (!usesNextcloud(ws.sync_mode) && !ws.auto_backup_enabled) {
    return <p className="text-sm text-slate-500">Backup off</p>;
  }
  if (!ws.last_sync_at) {
    return <p className="text-sm text-slate-500">Not synced yet</p>;
  }
  const when = formatDateTime(ws.last_sync_at) ?? ws.last_sync_at;
  if (ws.last_sync_status === 'error') {
    return (
      <p className="text-sm text-red-300 break-words" title={ws.last_sync_error ?? undefined}>
        Failed {when}{ws.last_sync_error ? `: ${ws.last_sync_error}` : ''}
      </p>
    );
  }
  return (
    <p className={`text-sm ${ws.last_sync_status === 'conflicts' ? 'text-amber-300' : 'text-slate-300'}`}>
      {ws.last_sync_status === 'conflicts' ? 'Synced with conflicts' : 'Synced'} {when}
    </p>
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
  const [ideSession, setIdeSession] = useState<WorkspaceSession | null>(null);

  /**
   * Open the IDE where this reader last left off.
   *
   * The server copy is asked for because it is the one every device shares,
   * and the browser copy is both the fallback and the thing it is compared
   * against: whichever was saved more recently wins, so a device that has been
   * offline is not silently reverted. A failure to reach the server is logged
   * and opening carries on -- a workspace must never be blocked by the sync.
   */
  const openIde = useCallback(async (ws: Workspace) => {
    let merged = readWorkspaceSession(ws.id);
    try {
      const remote = await api.getWorkspaceSessions();
      hydrateWorkspaceSessions(remote.sessions);
      merged = mergeWorkspaceSessions(merged, normaliseWorkspaceSession(remote.sessions[ws.id]));
    } catch (error) {
      console.warn('[workspace] could not read the saved sessions from the server:', error);
    }
    setIdeSession(merged);
    setIdeWs(ws);
  }, []);
  const [secretsWs, setSecretsWs] = useState<Workspace | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const [form, setForm] = useState<Partial<Workspace>>(newWorkspaceForm);

  const [pathError, setPathError] = useState<string | null>(null);
  const [excludeDraft, setExcludeDraft] = useState('');

  const addExclude = () => {
    const val = excludeDraft.trim();
    const current = Array.isArray(form.excludes) ? form.excludes : [];
    if (val && !current.includes(val)) setForm({ ...form, excludes: [...current, val] });
    setExcludeDraft('');
  };

  const { data: workspaces = [], isLoading } = useQuery({
    queryKey: ['workspaces'],
    queryFn: () => api.getWorkspaces(),
  });

  // Deep-link from the dashboard Workspaces widget (?select=<id>)
  const [searchParams, setSearchParams] = useSearchParams();
  useEffect(() => {
    const select = searchParams.get('select');
    if (!select || ideWs) return;
    const ws = workspaces.find((w) => w.id === select);
    if (ws) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- deep-link selection on load
      void openIde(ws);
      setSearchParams({}, { replace: true });
    }
  }, [searchParams, workspaces, ideWs, setSearchParams, openIde]);

  const saveMutation = useMutation({
    mutationFn: (data: Partial<Workspace>) => {
      if (editingWs) {
        return api.updateWorkspace(editingWs.id, data);
      }
      return api.createWorkspace(data as Partial<Workspace> & { id: string });
    },
    onSuccess: (_res, data) => {
      queryClient.invalidateQueries({ queryKey: ['workspaces'] });
      setIsModalOpen(false);
      setEditingWs(null);
      toast.success(editingWs ? 'Workspace updated' : 'Workspace created');
      // A new Nextcloud-backed workspace starts empty: pull its folder now
      // instead of waiting for the background sync.
      if (!editingWs && data.id && usesNextcloud(data.sync_mode)) {
        const id = data.id;
        toast.promise(
          api.syncWorkspace(id).then(() => queryClient.invalidateQueries({ queryKey: ['workspaces'] })),
          {
            loading: `Syncing ${id} with Nextcloud…`,
            success: `${id} synced with Nextcloud`,
            error: (e: Error) => `First Nextcloud sync failed: ${e.message}`,
          },
        );
      }
    },
    onError: (err: Error) => toast.error(err.message || 'Failed to save workspace'),
  });

  // Inline toggles (star / share) patch a single field; they must UPDATE, never
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

  // A Nextcloud-backed workspace has no git remote to pull: "Sync now" runs
  // its two-way sync instead.
  const syncMutation = useMutation({
    mutationFn: (id: string) => api.syncWorkspace(id),
    onSuccess: (data, id) => {
      queryClient.invalidateQueries({ queryKey: ['workspaces'] });
      const r = data.result;
      const conflicts = r?.conflicts?.length ?? 0;
      toast.success(
        conflicts
          ? `${id} synced with ${conflicts} conflict${conflicts === 1 ? '' : 's'} (both copies kept)`
          : `${id} synced with Nextcloud`,
      );
    },
    onError: (err: Error) => toast.error(err.message || 'Nextcloud sync failed'),
  });

  /** Bring every workspace up to date the way its storage works: pull git, sync Nextcloud. */
  const syncAll = () => {
    const runs = workspaces.map((ws) =>
      usesGit(ws.sync_mode) ? api.pullWorkspace(ws.id) : api.syncWorkspace(ws.id),
    );
    toast.promise(
      Promise.allSettled(runs).then((results) => {
        const failed = results.filter((r) => r.status === 'rejected').length;
        if (failed) throw new Error(`${failed} of ${results.length} workspaces failed to update`);
      }).finally(() => queryClient.invalidateQueries({ queryKey: ['workspaces'] })),
      {
        loading: 'Updating every workspace...',
        success: 'All workspaces up to date',
        error: (e: Error) => e.message,
      },
    );
  };

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
    setPathError(null);
    setExcludeDraft('');
    setIsModalOpen(true);
  };

  const openCreate = () => {
    setEditingWs(null);
    setForm(newWorkspaceForm());
    setPathError(null);
    setExcludeDraft('');
    setIsModalOpen(true);
  };

  const copyToClipboard = async (text: string, id: string) => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      toast.error('Could not copy: select the text and copy it by hand');
      return;
    }
    setCopiedId(id);
    toast.success('Copied to clipboard');
    setTimeout(() => setCopiedId(null), 2000);
  };

  // The token is not part of the URL: GitHub signs each delivery with it and
  // GitLab sends it in a header. The server never returns a saved token, so
  // only a freshly generated one can be shown (and copied) here.
  const getWebhookUrl = (id: string) => `${window.location.origin}/api/webhook/git-pull/${encodeURIComponent(id)}`;

  const draftWorkspaceId = editingWs?.id || form.id || 'workspace-id';
  const draftWebhookToken = form.webhook_token || '';
  const draftWebhookUrl = getWebhookUrl(draftWorkspaceId);

  return (
    <div className="space-y-8 pb-12 animate-in fade-in duration-500">
      <header className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
        <div>
          <h2 className="text-3xl font-bold tracking-tight text-white">{isAdmin ? 'Workspaces' : 'My Workspaces'}</h2>
          <p className="mt-1 text-slate-400">{isAdmin ? 'Manage repository locations and automated sync triggers.' : 'View your workspaces and shared resources.'}</p>
        </div>
        {isAdmin && (
          <div className="flex flex-wrap items-center gap-3">
            <button
              onClick={syncAll}
              disabled={workspaces.length === 0}
              title="Pull every git workspace and sync every Nextcloud workspace"
              className="glass-button px-5 py-2.5 pointer-coarse:min-h-11 bg-slate-800/40 border-slate-700/50 text-slate-300 font-bold flex items-center gap-2 disabled:opacity-40"
            >
              <GitPullRequest size={18} />
              Sync All
            </button>
            <button
              onClick={openCreate}
              className="glass-button px-5 py-2.5 pointer-coarse:min-h-11 bg-indigo-600/20 border-indigo-500/30 text-indigo-300 font-bold flex items-center gap-2"
            >
              <Plus size={18} />
              Add Workspace
            </button>
          </div>
        )}
      </header>

      <AllArtifacts
        workspaces={workspaces}
        onOpenInIDE={(ws, path) => {
          setIdeInitialPath(path);
          void openIde(ws);
        }}
      />

      {isAdmin && (
        <div className="glass-card p-4 border-l-4 border-l-purple-500 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div className="flex items-center gap-3 min-w-0">
            <div className="p-2 rounded-xl bg-purple-500/10">
              <Brain size={20} className="text-purple-400" />
            </div>
            <div>
              <h3 className="text-sm font-bold text-white">Raven Autonomous Scan</h3>
              <p className="text-xs text-slate-400">Deploy Raven to scan workspaces for anomalies, security issues, and stability.</p>
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
            className="glass-button px-6 py-2.5 pointer-coarse:min-h-11 bg-purple-600/20 border-purple-500/30 text-purple-300 font-bold flex items-center justify-center gap-2 hover:bg-purple-600/30 transition-colors shrink-0"
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
              {isAdmin
                ? 'Add a workspace backed by a git repository, a Nextcloud folder, or both.'
                : 'No workspace is shared with you yet. Ask Raven in chat to create one, or ask an admin to share one.'}
            </p>
            {isAdmin && (
              <button
                onClick={openCreate}
                className="mt-6 glass-button px-8 py-3 pointer-coarse:min-h-11 font-bold"
              >
                Get Started
              </button>
            )}
          </div>
        ) : (
          workspaces.map((ws) => {
            const isPulling = pullMutation.isPending && pullMutation.variables === ws.id;
            const isSyncing = syncMutation.isPending && syncMutation.variables === ws.id;
            const mode = storageValue(ws.sync_mode);
            const storageLabel = STORAGE_OPTIONS.find((o) => o.value === mode)?.label ?? mode;
            return (
            <div key={ws.id} className="glass-panel group overflow-hidden border-white/5 hover:border-white/10 transition-colors">
              <div className="flex flex-col xl:flex-row">
                {/* Info Section */}
                <div className="flex-1 min-w-0 p-4 sm:p-6 border-b xl:border-b-0 xl:border-r border-white/5">
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between mb-4">
                    <div className="flex items-center gap-3 min-w-0">
                      <div className="p-3 rounded-2xl bg-indigo-500/10 text-indigo-400 shrink-0">
                        <Database size={24} />
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <h3 className="text-xl font-bold text-white truncate max-w-full">{ws.display_name}</h3>
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
                    <div className="flex flex-wrap items-center gap-1 sm:gap-2">
                      <button
                        onClick={() => void openIde(ws)}
                        className={`${ICON_BUTTON} text-slate-500 hover:text-emerald-400 hover:bg-white/5`}
                        title="Open workspace files (IDE)"
                        aria-label={`Open ${ws.display_name}`}
                      >
                        <FolderOpen size={18} />
                      </button>
                      {isAdmin && (
                        <>
                          <button
                            onClick={() => updateMutation.mutate({ id: ws.id, is_default: !ws.is_default })}
                            className={`${ICON_BUTTON} ${ws.is_default ? 'text-emerald-400 hover:bg-emerald-500/10' : 'text-slate-500 hover:text-emerald-400 hover:bg-white/5'}`}
                            title={ws.is_default ? 'Unset as default' : 'Set as default'}
                            aria-label={ws.is_default ? 'Unset as default' : 'Set as default'}
                            aria-pressed={!!ws.is_default}
                          >
                            <Star size={18} />
                          </button>
                          <button
                            onClick={() => updateMutation.mutate({ id: ws.id, owner_user: ws.owner_user === 'default' ? null : 'default' })}
                            className={`${ICON_BUTTON} ${ws.owner_user === 'default' ? 'text-blue-400 hover:bg-blue-500/10' : 'text-slate-500 hover:text-blue-400 hover:bg-white/5'}`}
                            title={ws.owner_user === 'default' ? 'Unshare (make private)' : 'Share with all users'}
                            aria-label={ws.owner_user === 'default' ? 'Unshare (make private)' : 'Share with all users'}
                            aria-pressed={ws.owner_user === 'default'}
                          >
                            <Globe size={18} />
                          </button>
                          <button
                            onClick={() => setSecretsWs(ws)}
                            className={`${ICON_BUTTON} text-slate-500 hover:text-indigo-400 hover:bg-white/5`}
                            title="Secrets & Environment"
                            aria-label="Secrets & Environment"
                          >
                            <KeyRound size={18} />
                          </button>
                          <button
                            onClick={() => openEdit(ws)}
                            className={`${ICON_BUTTON} text-slate-500 hover:text-white hover:bg-white/5`}
                            title="Configure"
                            aria-label={`Configure ${ws.display_name}`}
                          >
                            <Edit3 size={18} />
                          </button>
                          <button
                            onClick={() => {
                              if (confirm(`Delete ${ws.display_name}? Its registration and sandbox are removed.`)) {
                                deleteMutation.mutate(ws.id);
                              }
                            }}
                            className={`${ICON_BUTTON} text-slate-500 hover:text-red-400 hover:bg-red-500/10`}
                            title="Delete"
                            aria-label={`Delete ${ws.display_name}`}
                          >
                            <Trash2 size={18} />
                          </button>
                        </>
                      )}
                    </div>
                  </div>

                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                    <div className="space-y-1 min-w-0">
                      <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">Path</p>
                      <div className="flex items-center gap-2 text-sm text-slate-300 min-w-0">
                        <Folder size={14} className="text-slate-600 shrink-0" />
                        <span className="font-mono truncate">{ws.local_path}</span>
                      </div>
                    </div>
                    <div className="space-y-1 min-w-0">
                      <div className="flex items-center justify-between gap-2">
                        <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">{storageLabel}</p>
                        {usesGit(ws.sync_mode) ? (
                          <button
                            onClick={() => pullMutation.mutate(ws.id)}
                            disabled={isPulling}
                            className="pointer-coarse:min-h-11 px-1 text-[10px] font-black uppercase tracking-widest text-indigo-400 hover:text-indigo-300 transition-colors disabled:opacity-50"
                          >
                            {isPulling ? 'Pulling...' : 'Pull Now'}
                          </button>
                        ) : (
                          <button
                            onClick={() => syncMutation.mutate(ws.id)}
                            disabled={isSyncing}
                            className="pointer-coarse:min-h-11 px-1 text-[10px] font-black uppercase tracking-widest text-indigo-400 hover:text-indigo-300 transition-colors disabled:opacity-50"
                          >
                            {isSyncing ? 'Syncing...' : 'Sync Now'}
                          </button>
                        )}
                      </div>
                      <div className="flex items-center gap-2 text-sm text-slate-300 min-w-0">
                        {usesGit(ws.sync_mode) ? (
                          <>
                            <GitPullRequest size={14} className="text-slate-600 shrink-0" />
                            <span className="truncate">{ws.git_remote || 'origin'}/{ws.default_branch || 'main'}</span>
                          </>
                        ) : (
                          <>
                            <Cloud size={14} className="text-slate-600 shrink-0" />
                            <span className="font-mono truncate">{ws.nextcloud_path}</span>
                          </>
                        )}
                      </div>
                    </div>
                    <div className="space-y-1 min-w-0">
                      <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">Created</p>
                      <div className="flex items-center gap-2 text-sm text-slate-300">
                        <Calendar size={14} className="text-slate-600 shrink-0" />
                        <span>{formatDateTime(ws.created_at) ?? 'Unknown'}</span>
                      </div>
                    </div>
                    {ws.nextcloud_path && (
                      <div className="space-y-1 min-w-0">
                        <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">Nextcloud Sync</p>
                        <NextcloudSyncStatus ws={ws} />
                      </div>
                    )}
                    {ws.repo_url && usesGit(ws.sync_mode) && (
                      <div className="space-y-1 sm:col-span-2 min-w-0">
                        <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">Source Repository</p>
                        <div className="flex items-center gap-2 text-sm text-slate-300 min-w-0">
                          <Globe size={14} className="text-slate-600 shrink-0" />
                          <span className="truncate">{ws.repo_url}</span>
                        </div>
                      </div>
                    )}
                    {Array.isArray(ws.excludes) && ws.excludes.length > 0 && (
                      <div className="space-y-1 sm:col-span-2">
                        <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">Sync Exclusions</p>
                        <div className="flex flex-wrap gap-1.5">
                          {ws.excludes.map((ex, idx) => (
                            <span key={idx} className="bg-white/5 border border-white/10 px-1.5 py-0.5 rounded text-[10px] font-bold text-slate-400 break-all">
                              {ex}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>

                  {ws.quarantined && (
                    <div className="mt-6 p-4 rounded-2xl bg-red-500/5 border border-red-500/10 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                      <div className="flex items-center gap-3">
                        <AlertTriangle className="text-red-400 shrink-0" size={20} />
                        <div>
                          <p className="text-xs font-bold text-white uppercase tracking-widest">Autonomous Lockdown</p>
                          <p className="text-xs text-slate-400">
                            {ws.last_raven_mission_id
                              ? `Raven flagged this workspace after Mission #${ws.last_raven_mission_id}.`
                              : 'Raven flagged this workspace after repeated failed edits.'}{' '}
                            Operations are restricted.
                          </p>
                        </div>
                      </div>
                      <div className="flex flex-wrap items-center gap-2">
                        <button
                          onClick={() => {
                            if (confirm('Revert this workspace to its previous stable state? This discards the last Raven patch.')) {
                              revertMutation.mutate(ws.id);
                            }
                          }}
                          disabled={revertMutation.isPending}
                          className="glass-button pointer-coarse:min-h-11 bg-emerald-500/10 border-emerald-500/20 text-emerald-300 hover:bg-emerald-500/20 px-4 py-2 flex items-center gap-2 text-[10px] font-black uppercase tracking-widest"
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
                            className="glass-button pointer-coarse:min-h-11 bg-purple-500/10 border-purple-500/20 text-purple-300 hover:bg-purple-500/20 px-4 py-2 flex items-center gap-2 text-[10px] font-black uppercase tracking-widest"
                          >
                            <Brain size={12} />
                            Send Raven
                          </button>
                        )}
                      </div>
                    </div>
                  )}
                </div>

                {/* Webhook Section: only a git workspace has anything to pull. */}
                {usesGit(ws.sync_mode) && (
                <div className="w-full xl:w-96 p-4 sm:p-6 bg-white/[0.01] min-w-0">
                   <div className="flex items-center justify-between mb-4">
                      <h4 className="text-xs font-black uppercase tracking-widest text-slate-400">Push Webhook</h4>
                      <div className={`px-2 py-0.5 rounded-full text-[10px] font-black uppercase tracking-widest ${ws.auto_pull_enabled && followsRemote(ws.sync_mode) ? 'bg-emerald-500/10 text-emerald-400' : 'bg-white/5 text-slate-500'}`}>
                        {!ws.auto_pull_enabled ? 'Disabled' : followsRemote(ws.sync_mode) ? 'Active' : 'Ignored'}
                      </div>
                   </div>

                   {ws.auto_pull_enabled && !followsRemote(ws.sync_mode) ? (
                     <p className="text-xs text-slate-400">
                       This checkout's own history is authoritative, so pushes to the remote are not pulled here.
                       {isAdmin && ' Choose "Git (follows remote)" under Configure to pull on every push.'}
                     </p>
                   ) : ws.auto_pull_enabled ? (
                     <div className="space-y-4">
                        <div>
                          <p className="text-[10px] text-slate-500 mb-1.5 ml-1">Payload URL</p>
                          <div className="flex items-center gap-2 p-2 rounded-lg bg-black/40 border border-white/5 font-mono text-[10px] text-slate-400 min-w-0">
                            <span className="truncate flex-1 min-w-0">{getWebhookUrl(ws.id)}</span>
                            <button
                              onClick={() => void copyToClipboard(getWebhookUrl(ws.id), ws.id)}
                              className={`${ICON_BUTTON} p-1.5 rounded-md hover:bg-white/10 text-slate-500 hover:text-white`}
                              aria-label="Copy payload URL"
                            >
                              {copiedId === ws.id ? <Check size={14} className="text-emerald-400" /> : <Copy size={14} />}
                            </button>
                          </div>
                        </div>
                        <div className="flex items-center gap-2 text-xs text-slate-500 bg-white/5 p-3 rounded-lg border border-white/5">
                           <ShieldCheck size={14} className="text-emerald-500 shrink-0" />
                           <span>Deliveries must carry the workspace's secret token. It is stored encrypted and never shown again; regenerate it under Configure.</span>
                        </div>
                     </div>
                   ) : (
                     <div className="flex flex-col items-center justify-center py-4 text-center">
                        <GitPullRequest size={32} className="text-slate-700 mb-2" />
                        <p className="text-xs text-slate-500">Pushes to the remote are not pulled automatically.</p>
                        {isAdmin && (
                          <button
                            onClick={() => openEdit(ws)}
                            className="mt-2 pointer-coarse:min-h-11 px-2 text-[10px] font-bold text-indigo-400 hover:text-indigo-300 uppercase tracking-widest"
                          >
                            Enable Now
                          </button>
                        )}
                     </div>
                   )}
                </div>
                )}
              </div>
            </div>
            );
          })
        )}
      </div>

      {ideWs && (
        <WorkspaceIDE
          key={ideWs.id}
          workspace={ideWs}
          initialPath={ideInitialPath}
          initialSession={ideSession}
          onClose={() => {
            setIdeWs(null);
            setIdeInitialPath(null);
            setIdeSession(null);
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

          <div className="space-y-2">
            <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Storage</span>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2" role="radiogroup" aria-label="Workspace storage">
              {STORAGE_OPTIONS.map((opt) => {
                const selected = storageValue(form.sync_mode) === opt.value;
                return (
                  <button
                    key={opt.value}
                    type="button"
                    role="radio"
                    aria-checked={selected}
                    onClick={() => setForm({ ...form, sync_mode: opt.value })}
                    className={`rounded-xl border px-2 py-2 text-xs font-semibold transition-colors pointer-coarse:min-h-11 ${
                      selected ? 'border-indigo-400/60 bg-indigo-500/20 text-indigo-200' : 'border-white/10 bg-white/5 text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    {opt.label}
                  </button>
                );
              })}
            </div>
            <p className="text-xs text-slate-500">
              {STORAGE_OPTIONS.find((o) => o.value === storageValue(form.sync_mode))?.hint}
            </p>
          </div>

          <label className="space-y-2">
            <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">
              Nextcloud Folder{usesNextcloud(form.sync_mode) ? '' : ' (optional backup target)'}
            </span>
            <div className="relative">
              <Cloud size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-600" />
              <input
                type="text"
                value={form.nextcloud_path || ''}
                onChange={(e) => setForm({ ...form, nextcloud_path: e.target.value })}
                placeholder="/Documents/My Project"
                className="glass-input w-full pl-10"
                aria-label="Nextcloud folder"
              />
            </div>
            <p className="text-xs text-slate-500">
              Path inside the Nextcloud account of whoever syncs it first (set up under Identity). Changing it later starts a fresh sync history.
            </p>
          </label>

          {form.sync_mode !== 'nextcloud' && (
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
            <p className="text-xs text-slate-500">Cloned into an empty workspace, and pulled from by webhooks.</p>
          </label>
          )}

          <label className="space-y-2">
            <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Path</span>
            <div className="relative">
              <Folder size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-600" />
              <input 
                type="text" 
                value={form.local_path || ''}
                onChange={(e) => {
                  const val = e.target.value;
                  setForm({ ...form, local_path: val });
                  if (form.scope === 'user' && /^\//.test(val)) {
                    setPathError('User workspaces must use relative paths (e.g. "my-project")');
                  } else {
                    setPathError(null);
                  }
                }}
                placeholder={editingWs ? 'my-project' : 'Leave empty to use the workspace ID'}
                className={`glass-input w-full pl-10 ${pathError ? 'border-red-500/50' : ''}`}
              />
            </div>
            <p className="text-xs text-slate-500">Folder under the workspace root. Only system workspaces may use an absolute path inside it.</p>
            {pathError && <p className="text-xs text-red-400 mt-1">{pathError}</p>}
          </label>

          {form.sync_mode !== 'nextcloud' && (
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
          )}

          <div className="p-4 rounded-2xl bg-indigo-500/5 border border-indigo-500/10 space-y-4">
             {usesGit(form.sync_mode) && (
             <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                   <h4 className="text-sm font-bold text-white flex items-center gap-2">
                      <GitPullRequest size={16} className="text-indigo-400 shrink-0" />
                      Pull on Push
                   </h4>
                   <p className="text-xs text-slate-500">
                     {followsRemote(form.sync_mode)
                       ? 'A push webhook from GitHub or GitLab fast-forwards this checkout.'
                       : 'Ignored while local git is authoritative; choose "Git (follows remote)" to use it.'}
                   </p>
                </div>
                <Toggle
                  ariaLabel="Toggle automated sync"
                  checked={!!form.auto_pull_enabled}
                  onChange={(on) => setForm({ ...form, auto_pull_enabled: on })}
                />
             </div>
             )}

             {usesGit(form.sync_mode) && !usesNextcloud(form.sync_mode) && (
             <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                   <h4 className="text-sm font-bold text-white flex items-center gap-2">
                      <Save size={16} className="text-indigo-400 shrink-0" />
                      Nextcloud Backup
                   </h4>
                   <p className="text-xs text-slate-500">After each successful pull, copy the checkout to the Nextcloud folder above.</p>
                </div>
                <Toggle
                  ariaLabel="Toggle nextcloud backup"
                  checked={!!form.auto_backup_enabled}
                  onChange={(on) => setForm({ ...form, auto_backup_enabled: on })}
                />
             </div>
             )}

              {(usesNextcloud(form.sync_mode) || form.auto_backup_enabled) && (
              <div className={usesGit(form.sync_mode) ? 'pt-2 border-t border-white/5' : ''}>
                <p className="text-[10px] font-black uppercase tracking-widest text-slate-500 mb-1">Nextcloud Sync Exclusions</p>
                <p className="text-xs text-slate-500 mb-2">Files or folders (glob patterns) that never travel to or from Nextcloud. .git is always excluded.</p>
                <div className="flex flex-wrap gap-2 mb-3">
                   {(Array.isArray(form.excludes) ? form.excludes : []).map((ex, idx) => (
                      <span key={idx} className="flex items-center gap-1 pl-2 rounded-lg bg-indigo-500/10 text-indigo-300 text-xs font-bold border border-indigo-500/20 break-all">
                         {ex}
                         <button
                            type="button"
                            aria-label={`Remove exclusion ${ex}`}
                            onClick={() => setForm({ ...form, excludes: (Array.isArray(form.excludes) ? form.excludes : []).filter((_, i) => i !== idx) })}
                            className={`${ICON_BUTTON} p-1.5 hover:text-red-400`}
                         >
                            <Trash2 size={12} />
                         </button>
                      </span>
                   ))}
                   {(!Array.isArray(form.excludes) || form.excludes.length === 0) && (
                      <p className="text-xs text-slate-500">No custom exclusions set.</p>
                   )}
                </div>
                <div className="flex gap-2">
                   <input
                      type="text"
                      value={excludeDraft}
                      onChange={(e) => setExcludeDraft(e.target.value)}
                      placeholder="e.g. node_modules or *.tmp"
                      aria-label="Exclusion to add"
                      className="glass-input flex-1 min-w-0 text-sm py-2"
                      onKeyDown={(e) => {
                         if (e.key === 'Enter') {
                            e.preventDefault();
                            addExclude();
                         }
                      }}
                   />
                   <button
                     type="button"
                     onClick={addExclude}
                     disabled={!excludeDraft.trim()}
                     className="glass-button px-4 pointer-coarse:min-h-11 text-sm font-bold disabled:opacity-40"
                   >
                     Add
                   </button>
                </div>
              </div>
              )}

             {usesGit(form.sync_mode) && form.auto_pull_enabled && followsRemote(form.sync_mode) && (
               <div className="space-y-4 animate-in slide-in-from-top-2 duration-300">
                  <label className="space-y-2 block">
                    <span className="text-[10px] font-black uppercase tracking-widest text-slate-400">Webhook Secret Token</span>
                    <div className="relative">
                      <ShieldCheck size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-600" />
                      <input
                        type="text"
                        value={form.webhook_token || ''}
                        onChange={(e) => setForm({ ...form, webhook_token: e.target.value })}
                        placeholder={editingWs ? 'Saved token kept (hidden)' : 'Secret for GitHub or GitLab'}
                        className="glass-input w-full pl-10 pr-28"
                      />
                      <button
                        type="button"
                        onClick={() => setForm({ ...form, webhook_token: generateWebhookToken() })}
                        className="absolute right-1 top-1/2 -translate-y-1/2 rounded-md px-2 py-1 pointer-coarse:min-h-11 text-[10px] font-black uppercase tracking-widest text-indigo-300 hover:bg-white/5"
                      >
                        {editingWs && !form.webhook_token ? 'New token' : 'Regenerate'}
                      </button>
                    </div>
                    {editingWs && !form.webhook_token && (
                      <p className="text-xs text-slate-500">The saved token is never shown again. Leave this empty to keep it, or make a new one and update the hook.</p>
                    )}
                  </label>

                  <div className="space-y-3 rounded-xl border border-white/10 bg-black/20 p-4">
                    <div>
                      <p className="text-[10px] font-black uppercase tracking-widest text-slate-400">Webhook Setup</p>
                      <p className="text-xs text-slate-500">Use these values when creating the repository webhook.</p>
                    </div>

                    <div className="space-y-2">
                      <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">Payload URL</p>
                      <div className="flex items-center gap-2 rounded-lg border border-white/5 bg-black/40 p-2">
                        <div className="min-w-0 flex-1 font-mono text-[10px] text-slate-300 break-all">
                          {draftWebhookUrl}
                        </div>
                        <button
                          type="button"
                          onClick={() => void copyToClipboard(draftWebhookUrl, `draft-url-${draftWorkspaceId}`)}
                          className={`${ICON_BUTTON} rounded-md text-slate-500 hover:bg-white/5 hover:text-white`}
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
                          {draftWebhookToken || (editingWs ? 'Hidden: the saved token is unchanged.' : 'Generate or enter a token to use as the webhook secret.')}
                        </div>
                        <button
                          type="button"
                          onClick={() => void copyToClipboard(draftWebhookToken || '', `draft-secret-${draftWorkspaceId}`)}
                          className={`${ICON_BUTTON} rounded-md text-slate-500 hover:bg-white/5 hover:text-white disabled:opacity-30`}
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
                        <p className="mt-1 text-xs text-slate-400">Content type: application/json</p>
                        <p className="text-xs text-slate-400">Event: Just the push event</p>
                        <p className="text-xs text-slate-400">Secret: the token above</p>
                      </div>
                      <div className="rounded-lg border border-white/5 bg-white/[0.03] p-3">
                        <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">GitLab</p>
                        <p className="mt-1 text-xs text-slate-400">Trigger: Push events</p>
                        <p className="text-xs text-slate-400">Secret token: the token above</p>
                        <p className="text-xs text-slate-400">SSL verification: leave enabled</p>
                      </div>
                    </div>

                    <p className="text-xs text-slate-500">
                      Other senders can append <span className="font-mono">?token=&lt;secret&gt;</span> to the URL. Save the workspace before the first delivery.
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
                if (!form.id || !form.display_name || (editingWs && !form.local_path)) {
                  toast.error(editingWs ? 'Required fields: Name, Path' : 'Required fields: ID, Name');
                  return;
                }
                if (form.scope === 'user' && form.local_path && /^\//.test(form.local_path)) {
                  toast.error('User workspaces must use relative paths (e.g. "my-project"), not absolute paths');
                  return;
                }
                if (usesNextcloud(form.sync_mode) && !form.nextcloud_path?.trim()) {
                  toast.error('Nextcloud storage needs a Nextcloud folder');
                  return;
                }
                if (form.auto_backup_enabled && !usesNextcloud(form.sync_mode) && !form.nextcloud_path?.trim()) {
                  toast.error('Nextcloud backup needs a Nextcloud folder');
                  return;
                }
                saveMutation.mutate(workspacePayload(form, !editingWs));
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
