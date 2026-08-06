import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import {
  X,
  Folder,
  FolderOpen,
  File as FileIcon,
  FileText,
  Save,
  Download,
  Upload,
  Trash2,
  RefreshCw,
  GitBranch,
  GitCommit,
  UploadCloud,
  GitPullRequest,
  Play,
  Terminal,
  FolderPlus,
  FilePlus,
  CloudUpload,
  Loader2,
  ChevronRight,
  ChevronUp,
  Wrench,
  Code2,
  Copy,
  Pencil,
  AlertTriangle,
  MessageSquare,
  Send,
  Bot,
  Image as ImageIcon,
  Music,
  KeyRound,
  Wand2,
  Maximize2,
  Brush,
  Eye,
} from 'lucide-react';
import toast from 'react-hot-toast';
import { api, type RavenMission, type Workspace } from '../../services/api';
import type { GitLogEntry, GitStatusResponse, WorkspaceFileEntry } from '../../types/api';
import { detectLanguage } from '../../lib/editorLanguages';
import { CodeEditor } from '../editor/CodeEditor';
import { MarkdownViewer } from './viewers/MarkdownViewer';
import { MarkdownEditor } from './viewers/MarkdownEditor';
import { ImageViewer } from './viewers/ImageViewer';
import { PdfViewer } from './viewers/PdfViewer';
import { DocxEditor } from './viewers/DocxEditor';
import { OdfViewer } from './viewers/OdfViewer';
import { ExcelViewer } from './viewers/ExcelViewer';
import { TerminalPane } from './viewers/TerminalPane';
import { WorkspaceSecrets } from './WorkspaceSecrets';
import { cn } from '../../lib/utils';

interface WorkspaceIDEProps {
  workspace: Workspace;
  onClose: () => void;
  initialPath?: string | null;
}

type View = 'explorer' | 'git' | 'tools' | 'chat' | 'terminal';

// A single open editor tab. Text tabs cache their buffer so switching tabs
// preserves edits; image tabs cache an object URL for preview.
interface OpenTab {
  path: string;
  kind: 'text' | 'image' | 'markdown' | 'pdf' | 'docx' | 'xlsx' | 'odf' | 'audio' | 'video' | 'terminal';
  content: string;
  imageUrl: string | null;
  blobUrl?: string | null;
  dirty: boolean;
  language: string;
  baseContent: string;
}

function formatBytes(n?: number | null): string {
  if (n == null) return '';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function downloadBlob(url: string, filename: string) {
  const a = document.createElement('a');
  a.href = url;
  a.download = filename.split('/').pop() || 'download';
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function apiErr(e: unknown): string {
  const err = e as {
    response?: { data?: { detail?: string } };
    message?: string;
  };
  return err?.response?.data?.detail || err?.message || 'Unknown error';
}

const ACTIVITY: { id: View; icon: typeof FolderOpen; label: string }[] = [
  { id: 'explorer', icon: FolderOpen, label: 'Explorer' },
  { id: 'git', icon: GitBranch, label: 'Source Control' },
  { id: 'tools', icon: Wrench, label: 'Tools' },
  { id: 'terminal', icon: Terminal, label: 'Terminal' },
  { id: 'chat', icon: MessageSquare, label: 'Raven Chat' },
];

interface FileCtxMenuItemProps {
  icon: ReactNode;
  label: string;
  onClick: () => void;
  danger?: boolean;
}

function FileCtxMenuItem({ icon, label, onClick, danger }: FileCtxMenuItemProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'w-full flex items-center gap-2 px-3 py-1.5 text-left hover:bg-white/10',
        danger ? 'text-red-400 hover:bg-red-500/10' : 'text-slate-200',
      )}
    >
      <span className="shrink-0 opacity-70">{icon}</span>
      <span className="truncate">{label}</span>
    </button>
  );
}

export default function WorkspaceIDE({ workspace, onClose, initialPath }: WorkspaceIDEProps) {
  const [activeView, setActiveView] = useState<View>('explorer');
  const [terminalOpen, setTerminalOpen] = useState(false);
  const [terminalPosition, setTerminalPosition] = useState<'sidebar' | 'bottom'>('bottom');
  const [terminalHeight, setTerminalHeight] = useState(250);

  const startResize = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    const startY = e.clientY;
    const startHeight = terminalHeight;
    
    const onMouseMove = (moveEvent: MouseEvent) => {
      const deltaY = moveEvent.clientY - startY;
      const newHeight = Math.max(100, Math.min(window.innerHeight - 150, startHeight - deltaY));
      setTerminalHeight(newHeight);
    };
    
    const onMouseUp = () => {
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mouseup', onMouseUp);
    };
    
    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
  }, [terminalHeight]);

  const terminalRedirectHandledRef = useRef(false);

  useEffect(() => {
    if (activeView === 'terminal' && terminalPosition === 'bottom' && !terminalRedirectHandledRef.current) {
      terminalRedirectHandledRef.current = true;
      setTerminalOpen(true);
      setActiveView('explorer');
    } else if (activeView !== 'terminal') {
      terminalRedirectHandledRef.current = false;
    }
  }, [activeView, terminalPosition]);
  const hasGit = useMemo(
    () => workspace.capabilities.some((c) => c === 'git_status' || c === 'git_write' || c.startsWith('git')),
    [workspace.capabilities],
  );

  const [currentPath, setCurrentPath] = useState('.');
  const [entries, setEntries] = useState<WorkspaceFileEntry[]>([]);
  const [loadingFiles, setLoadingFiles] = useState(false);

  // Right-click context menu for the Explorer file pane. `entry` is null for
  // pane-level actions (new file/folder, upload, refresh) invoked on empty space.
  const [ctxMenu, setCtxMenu] = useState<{ x: number; y: number; entry: WorkspaceFileEntry | null } | null>(null);

  // Tabbed editors: many files can be open at once; `activeTab` is the focused path.
  const [tabs, setTabs] = useState<OpenTab[]>([]);
  const [activeTab, setActiveTab] = useState<string | null>(null);
  const [showSecrets, setShowSecrets] = useState(false);
  const [saving, setSaving] = useState(false);

  const [gitStatus, setGitStatus] = useState<GitStatusResponse | null>(null);
  const [gitLog, setGitLog] = useState<GitLogEntry[]>([]);
  const [commitMsg, setCommitMsg] = useState('');
  const [gitBusy, setGitBusy] = useState(false);
  const [branches, setBranches] = useState<string[]>([]);
  const [branchBusy, setBranchBusy] = useState(false);
  // A workspace may have the git capability but not actually be a git repo on
  // disk (e.g. the default user workspace). Gate actions on a real repo so we
  // don't surface scary errors for non-repo workspaces.
  const gitEnabled = hasGit && gitStatus?.is_git_repo !== false;

  // In-editor diff view (replaces the cramped side-panel <pre>).
  const [showDiff, setShowDiff] = useState(false);
  const [diffText, setDiffText] = useState('');

  // Editor power features: VIM keybindings, validation/problems panel, format.
  const [vimMode, setVimMode] = useState(false);
  const [problems, setProblems] = useState<{ open: boolean; text: string; count: number }>({
    open: false,
    text: '',
    count: 0,
  });

  // Root-relative file path -> short git status letter (M/?/A/D/...). Used to
  // badge files with uncommitted changes in the explorer file chooser.
  const modifiedPaths = useMemo(() => {
    const m = new Map<string, string>();
    for (const line of gitStatus?.porcelain ?? []) {
      if (line.length < 4) continue;
      const x = line[0];
      const y = line[1];
      let path = line.slice(3);
      if (path.includes(' -> ')) path = path.split(' -> ')[1];
      const letter = x !== ' ' ? x : y;
      if (letter === ' ') continue;
      m.set(path, letter === '?' ? '?' : letter === 'M' ? 'M' : letter === 'A' ? 'A' : letter === 'D' ? 'D' : letter === 'R' ? 'R' : letter === 'U' ? 'U' : 'M');
    }
    return m;
  }, [gitStatus]);

  const loadDir = useCallback(
    async (path: string) => {
      setLoadingFiles(true);
      try {
        const res = await api.listWorkspaceFiles(workspace.id, path, false, 1);
        const list: WorkspaceFileEntry[] = res?.entries ?? [];
        list.sort((a, b) => Number(b.is_dir) - Number(a.is_dir) || a.name.localeCompare(b.name));
        setEntries(list);
        setCurrentPath(path);
      } catch (e: unknown) {
        toast.error(`Failed to list files: ${apiErr(e)}`);
      } finally {
        setLoadingFiles(false);
      }
    },
    [workspace.id],
  );

  const refreshGit = useCallback(async () => {
    if (!hasGit) return;
    setGitBusy(true);
    try {
      const [st, log] = await Promise.all([
        api.workspaceGitStatus(workspace.id),
        api.workspaceGitLog(workspace.id, 15),
      ]);
      setGitStatus(st);
      setGitLog(log?.entries ?? []);
    } catch (e: unknown) {
      toast.error(`Git status failed: ${apiErr(e)}`);
    } finally {
      setGitBusy(false);
    }
  }, [hasGit, workspace.id]);

  const refreshBranches = useCallback(async () => {
    if (!hasGit) return;
    try {
      const res = await api.workspaceGitBranches(workspace.id);
      setBranches(res?.local ?? []);
    } catch {
      /* branches optional */
    }
  }, [hasGit, workspace.id]);

  const switchBranch = useCallback(
    async (branch: string, create = false) => {
      if (!branch) return;
      setBranchBusy(true);
      try {
        await api.workspaceGitCheckout(workspace.id, branch, create);
        toast.success(`Switched to ${branch}`);
        await Promise.all([loadDir('.'), refreshGit(), refreshBranches()]);
      } catch (e: unknown) {
        toast.error(`Checkout failed: ${apiErr(e)}`);
      } finally {
        setBranchBusy(false);
      }
    },
    [workspace.id, loadDir, refreshGit, refreshBranches],
  );

  const [toolOutput, setToolOutput] = useState('');
  const [toolBusy, setToolBusy] = useState(false);

  const [missions, setMissions] = useState<RavenMission[]>([]);
  const [missionsLoading, setMissionsLoading] = useState(false);
  const [refineInput, setRefineInput] = useState('');
  const [refineBusy, setRefineBusy] = useState(false);
  const [chatInput, setChatInput] = useState('');
  const [chatBusy, setChatBusy] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    void (async () => {
      await loadDir('.');
      await refreshGit();
      await refreshBranches();
    })();
  }, [loadDir, refreshGit, refreshBranches]);

  const [imageModels, setImageModels] = useState<string[]>([]);
  const [sdModel, setSdModel] = useState('');
  const [sdPrompt, setSdPrompt] = useState('');
  const [sdBusy, setSdBusy] = useState(false);

  const isImagePath = useCallback((p: string | null): boolean => {
    if (!p) return false;
    return /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(p);
  }, []);

  const isAudioPath = useCallback((p: string | null): boolean => {
    if (!p) return false;
    return /\.(mp3|wav|ogg|oga|opus|flac|aac|m4a|wma|aiff|aif)$/i.test(p);
  }, []);

  const isVideoPath = useCallback((p: string | null): boolean => {
    if (!p) return false;
    return /\.(mp4|m4v|mov|mkv|webm|avi|wmv|mpg|mpeg|ts|m2ts)$/i.test(p);
  }, []);

  const isMarkdownPath = useCallback((p: string | null): boolean => {
    if (!p) return false;
    return /\.(md|markdown|mdx)$/i.test(p);
  }, []);

  const isPdfPath = useCallback((p: string | null): boolean => {
    if (!p) return false;
    return /\.pdf$/i.test(p);
  }, []);

  const isDocxPath = useCallback((p: string | null): boolean => {
    if (!p) return false;
    return /\.docx?$/i.test(p);
  }, []);

  const isExcelPath = useCallback((p: string | null): boolean => {
    if (!p) return false;
    return /\.(xlsx?|csv)$/i.test(p);
  }, []);

  const isOdfPath = useCallback((p: string | null): boolean => {
    if (!p) return false;
    return /\.(odt|ods|odp|fodt)$/i.test(p);
  }, []);

  const baseDirOf = useCallback((path: string) => {
    if (path === '.' || !path.includes('/')) return '';
    return path.replace(/\/$/, '') + '/';
  }, []);

  const loadImageModels = useCallback(async () => {
    try {
      const res = await api.listImageModels();
      const models = Array.isArray(res?.models) ? res.models : [];
      setImageModels(models);
      if (models.length && !sdModel) setSdModel(models[0]);
    } catch {
      /* models optional */
    }
  }, [sdModel]);

  const active = useMemo(() => tabs.find((t) => t.path === activeTab) ?? null, [tabs, activeTab]);
  const language = active && active.kind === 'text' ? active.language : 'plaintext';

  // Open a file from the explorer. Directories recurse into the listing; files
  // open a (new) tab without re-fetching if already open.
  const openFile = useCallback(
    async (entry: WorkspaceFileEntry) => {
      if (entry.is_dir) {
        await loadDir(entry.path);
        return;
      }
      if (tabs.some((t) => t.path === entry.path)) {
        setActiveTab(entry.path);
        return;
      }
      if (isImagePath(entry.path)) {
        try {
          const blob = await api.fetchWorkspaceFileRaw(workspace.id, entry.path);
          const url = URL.createObjectURL(blob);
          setTabs((prev) => [
            ...prev,
            { path: entry.path, kind: 'image', content: '', imageUrl: url, dirty: false, language: 'plaintext', baseContent: '' },
          ]);
          setActiveTab(entry.path);
          await loadImageModels();
        } catch (e: unknown) {
          toast.error(`Failed to open image: ${apiErr(e)}`);
        }
        return;
      }
      if (isAudioPath(entry.path) || isVideoPath(entry.path)) {
        try {
          const blob = await api.fetchWorkspaceFileRaw(workspace.id, entry.path);
          const url = URL.createObjectURL(blob);
          setTabs((prev) => [
            ...prev,
            { path: entry.path, kind: isAudioPath(entry.path) ? 'audio' : 'video', content: '', imageUrl: null, blobUrl: url, dirty: false, language: 'plaintext', baseContent: '' },
          ]);
          setActiveTab(entry.path);
        } catch (e: unknown) {
          toast.error(`Failed to open media file: ${apiErr(e)}`);
        }
        return;
      }
      if (isPdfPath(entry.path) || isDocxPath(entry.path) || isExcelPath(entry.path) || isOdfPath(entry.path)) {
        const kind = isPdfPath(entry.path) ? 'pdf' : isDocxPath(entry.path) ? 'docx' : isOdfPath(entry.path) ? 'odf' : 'xlsx';
        try {
          const blob = await api.fetchWorkspaceFileRaw(workspace.id, entry.path);
          const url = URL.createObjectURL(blob);
          setTabs((prev) => [
            ...prev,
            { path: entry.path, kind, content: '', imageUrl: null, blobUrl: url, dirty: false, language: 'plaintext', baseContent: '' },
          ]);
          setActiveTab(entry.path);
        } catch (e: unknown) {
          toast.error(`Failed to open ${kind} file: ${apiErr(e)}`);
        }
        return;
      }
      if (isMarkdownPath(entry.path)) {
        try {
          const res = await api.readWorkspaceFile(workspace.id, entry.path);
          const content = typeof res?.content === 'string' ? res.content : '';
          setTabs((prev) => [
            ...prev,
            { path: entry.path, kind: 'markdown', content, imageUrl: null, dirty: false, language: 'markdown', baseContent: content },
          ]);
          setActiveTab(entry.path);
        } catch (e: unknown) {
          toast.error(`Failed to read file: ${apiErr(e)}`);
        }
        return;
      }
      try {
        const res = await api.readWorkspaceFile(workspace.id, entry.path);
        const content = typeof res?.content === 'string' ? res.content : '';
        setTabs((prev) => [
          ...prev,
          { path: entry.path, kind: 'text', content, imageUrl: null, dirty: false, language: detectLanguage(entry.path), baseContent: content },
        ]);
        setActiveTab(entry.path);
      } catch (e: unknown) {
        toast.error(`Failed to read file: ${apiErr(e)}`);
      }
    },
    [workspace.id, loadDir, isImagePath, isAudioPath, isVideoPath, isMarkdownPath, isPdfPath, isDocxPath, isExcelPath, isOdfPath, loadImageModels, tabs],
  );

  // Programmatic open by path (e.g. a freshly generated image from Stable Diffusion).
  const openByPath = useCallback(
    async (path: string) => {
      if (tabs.some((t) => t.path === path)) {
        setActiveTab(path);
        return;
      }
      if (isImagePath(path)) {
        try {
          const blob = await api.fetchWorkspaceFileRaw(workspace.id, path);
          const url = URL.createObjectURL(blob);
          setTabs((prev) => [
            ...prev,
            { path, kind: 'image', content: '', imageUrl: url, dirty: false, language: 'plaintext', baseContent: '' },
          ]);
          setActiveTab(path);
          await loadImageModels();
        } catch {
          /* preview optional */
        }
        return;
      }
      if (isAudioPath(path) || isVideoPath(path)) {
        try {
          const blob = await api.fetchWorkspaceFileRaw(workspace.id, path);
          const url = URL.createObjectURL(blob);
          setTabs((prev) => [
            ...prev,
            { path, kind: isAudioPath(path) ? 'audio' : 'video', content: '', imageUrl: null, blobUrl: url, dirty: false, language: 'plaintext', baseContent: '' },
          ]);
          setActiveTab(path);
        } catch {
          /* preview optional */
        }
        return;
      }
      if (isPdfPath(path) || isDocxPath(path) || isExcelPath(path) || isOdfPath(path)) {
        const kind = isPdfPath(path) ? 'pdf' : isDocxPath(path) ? 'docx' : isOdfPath(path) ? 'odf' : 'xlsx';
        try {
          const blob = await api.fetchWorkspaceFileRaw(workspace.id, path);
          const url = URL.createObjectURL(blob);
          setTabs((prev) => [
            ...prev,
            { path, kind, content: '', imageUrl: null, blobUrl: url, dirty: false, language: 'plaintext', baseContent: '' },
          ]);
          setActiveTab(path);
        } catch {
          /* preview optional */
        }
        return;
      }
      if (isMarkdownPath(path)) {
        try {
          const res = await api.readWorkspaceFile(workspace.id, path);
          const content = typeof res?.content === 'string' ? res.content : '';
          setTabs((prev) => [
            ...prev,
            { path, kind: 'markdown', content, imageUrl: null, dirty: false, language: 'markdown', baseContent: content },
          ]);
          setActiveTab(path);
        } catch {
          /* open optional */
        }
        return;
      }
      try {
        const res = await api.readWorkspaceFile(workspace.id, path);
        const content = typeof res?.content === 'string' ? res.content : '';
        setTabs((prev) => [
          ...prev,
          { path, kind: 'text', content, imageUrl: null, dirty: false, language: detectLanguage(path), baseContent: content },
        ]);
        setActiveTab(path);
      } catch {
        /* open optional */
      }
    },
    [workspace.id, isImagePath, isAudioPath, isVideoPath, isMarkdownPath, isPdfPath, isDocxPath, isExcelPath, isOdfPath, loadImageModels, tabs],
  );

  // Open a file programmatically on mount (e.g. an artifact from the
  // Workspaces page). Runs once per (workspace, initialPath) pair.
  const initialPathHandledRef = useRef<string | null>(null);
  const docxEditorRef = useRef<{ save: () => Promise<void> } | null>(null);
  const odfViewerRef = useRef<{ save: () => Promise<void> } | null>(null);
  const markdownEditorRef = useRef<{ save: () => Promise<void> } | null>(null);
  const [mdRichPaths, setMdRichPaths] = useState<Record<string, boolean>>({});
  useEffect(() => {
    if (!initialPath || initialPathHandledRef.current === initialPath) return;
    initialPathHandledRef.current = initialPath;
    void (async () => {
      const dir = baseDirOf(initialPath) || '.';
      await Promise.all([loadDir(dir), openByPath(initialPath)]);
    })();
  }, [workspace.id, initialPath, baseDirOf, loadDir, openByPath]);

  // Close a tab (confirm if it has unsaved edits). Revokes image object URLs.
  const closeTab = useCallback(
    (path: string) => {
      const tab = tabs.find((t) => t.path === path);
      if (tab?.dirty && !confirm(`Discard unsaved changes to ${path}?`)) return;
      if (tab?.imageUrl) URL.revokeObjectURL(tab.imageUrl);
      if (tab?.blobUrl) URL.revokeObjectURL(tab.blobUrl);
      setTabs((prev) => {
        const idx = prev.findIndex((t) => t.path === path);
        const next = prev.filter((t) => t.path !== path);
        if (activeTab === path) {
          setActiveTab(next.length ? next[Math.min(idx, next.length - 1)].path : null);
        }
        return next;
      });
    },
    [tabs, activeTab],
  );

  // Drop a tab without prompting (used after the underlying file is deleted).
  const removeTab = useCallback(
    (path: string) => {
      setTabs((prev) => {
        const idx = prev.findIndex((t) => t.path === path);
        const next = prev.filter((t) => t.path !== path);
        if (activeTab === path) {
          setActiveTab(next.length ? next[Math.min(idx, next.length - 1)].path : null);
        }
        return next;
      });
    },
    [activeTab],
  );

  const onEditorChange = useCallback(
    (value: string | undefined) => {
      const val = value ?? '';
      setTabs((prev) =>
        prev.map((t) => (t.path === activeTab ? { ...t, content: val, dirty: val !== t.baseContent } : t)),
      );
    },
    [activeTab],
  );

  // Save a rich-text document tab (docx/odf) via its editor handle.
  const saveDocumentTab = useCallback(async () => {
    if (!active) return;
    const editorRef = active.kind === 'docx' ? docxEditorRef : active.kind === 'odf' ? odfViewerRef : null;
    if (!editorRef?.current) return;
    setSaving(true);
    try {
      await editorRef.current.save();
      setTabs((prev) => prev.map((t) => (t.path === active.path ? { ...t, dirty: false } : t)));
    } catch (e: unknown) {
      toast.error(`Save failed: ${apiErr(e)}`);
    } finally {
      setSaving(false);
    }
  }, [active]);

  const saveFile = useCallback(async () => {
    if (!active) return;
    if (active.kind === 'markdown' && mdRichPaths[active.path]) {
      if (!markdownEditorRef.current) return;
      setSaving(true);
      try {
        await markdownEditorRef.current.save();
        setTabs((prev) => prev.map((t) => (t.path === active.path ? { ...t, dirty: false } : t)));
      } catch (e: unknown) {
        toast.error(`Save failed: ${apiErr(e)}`);
      } finally {
        setSaving(false);
      }
      return;
    }
    if (active.kind !== 'text' && active.kind !== 'markdown') return;
    if (!active.dirty) return;
    setSaving(true);
    try {
      await api.writeWorkspaceFile(workspace.id, active.path, active.content);
      setTabs((prev) => prev.map((t) => (t.path === active.path ? { ...t, dirty: false, baseContent: t.content } : t)));
      toast.success(`Saved ${active.path}`);
    } catch (e: unknown) {
      toast.error(`Save failed: ${apiErr(e)}`);
    } finally {
      setSaving(false);
    }
  }, [active, workspace.id, mdRichPaths]);

  // Run the workspace linter against the active file and surface the results
  // in a VSCode-style Problems panel at the bottom of the editor.
  const validateFile = useCallback(async () => {
    if (!active || active.kind !== 'text') return;
    setProblems((p) => ({ ...p, open: true, text: 'Running validation…', count: 0 }));
    try {
      const res = await api.workspaceLint({ workspace_id: workspace.id, path: active.path });
      const text = res?.output ?? JSON.stringify(res);
      const count = (text.match(/error|warning|issue|❌|⚠️/gi) ?? []).length;
      setProblems({ open: true, text, count });
      if (count === 0) toast.success(`No issues found in ${active.path}`);
    } catch (e: unknown) {
      setProblems({ open: true, text: `Validation failed: ${apiErr(e)}`, count: 0 });
    }
  }, [active, workspace.id]);

  // Ctrl/Cmd+S saves the active tab.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') {
        e.preventDefault();
        void saveFile();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [saveFile]);

  const downloadFile = useCallback(() => {
    if (!active || (active.kind !== 'text' && active.kind !== 'markdown')) return;
    const blob = new Blob([active.content], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = active.path.split('/').pop() || 'file';
    a.click();
    URL.revokeObjectURL(url);
  }, [active]);

  const downloadImage = useCallback(async () => {
    if (!active || active.kind !== 'image') return;
    try {
      const blob = await api.fetchWorkspaceFileRaw(workspace.id, active.path);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = active.path.split('/').pop() || 'image';
      a.click();
      URL.revokeObjectURL(url);
    } catch (e: unknown) {
      toast.error(`Download failed: ${apiErr(e)}`);
    }
  }, [active, workspace.id]);

  const deleteEntry = useCallback(
    async (entry: WorkspaceFileEntry) => {
      if (!confirm(`Delete ${entry.path}? This cannot be undone.`)) return;
      try {
        await api.deleteWorkspaceFile(workspace.id, entry.path);
        toast.success(`Deleted ${entry.path}`);
        removeTab(entry.path);
        await loadDir(currentPath);
      } catch (e: unknown) {
        toast.error(`Delete failed: ${apiErr(e)}`);
      }
    },
    [workspace.id, currentPath, loadDir, removeTab],
  );

  const createNew = useCallback(
    async (kind: 'file' | 'folder') => {
      const name = prompt(`New ${kind} name:`);
      if (!name) return;
      const rel = currentPath === '.' ? name : `${currentPath}/${name}`;
      try {
        if (kind === 'file') {
          await api.writeWorkspaceFile(workspace.id, rel, '');
        } else {
          await api.writeWorkspaceFile(workspace.id, `${rel}/.gitkeep`, '');
        }
        toast.success(`Created ${rel}`);
        await loadDir(currentPath);
      } catch (e: unknown) {
        toast.error(`Create failed: ${apiErr(e)}`);
      }
    },
    [workspace.id, currentPath, loadDir],
  );

  const closeCtxMenu = useCallback(() => setCtxMenu(null), []);

  const copyPath = useCallback(
    (entry: WorkspaceFileEntry) => {
      void navigator.clipboard?.writeText(entry.path);
      toast.success(`Copied path: ${entry.path}`);
      closeCtxMenu();
    },
    [closeCtxMenu],
  );

  const downloadEntry = useCallback(
    async (entry: WorkspaceFileEntry) => {
      closeCtxMenu();
      try {
        const blob = await api.fetchWorkspaceFileRaw(workspace.id, entry.path);
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = entry.name;
        a.click();
        URL.revokeObjectURL(url);
      } catch (e: unknown) {
        toast.error(`Download failed: ${apiErr(e)}`);
      }
    },
    [workspace.id, closeCtxMenu],
  );

  const renameEntry = useCallback(
    async (entry: WorkspaceFileEntry) => {
      closeCtxMenu();
      const idx = entry.path.lastIndexOf('/');
      const parent = idx >= 0 ? entry.path.slice(0, idx) : '.';
      const base = idx >= 0 ? entry.path.slice(idx + 1) : entry.path;
      const name = prompt(`Rename ${entry.path} to:`, base);
      if (!name || name === base) return;
      const newPath = parent === '.' ? name : `${parent}/${name}`;
      try {
        await api.moveWorkspaceFile(workspace.id, entry.path, newPath);
        toast.success(`Renamed to ${newPath}`);
        removeTab(entry.path);
        await loadDir(currentPath);
      } catch (e: unknown) {
        toast.error(`Rename failed: ${apiErr(e)}`);
      }
    },
    [workspace.id, currentPath, loadDir, removeTab, closeCtxMenu],
  );

  const onUpload = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      e.target.value = '';
      if (!file) return;
      const text = await file.text();
      const rel = currentPath === '.' ? file.name : `${currentPath}/${file.name}`;
      try {
        await api.writeWorkspaceFile(workspace.id, rel, text);
        toast.success(`Uploaded ${rel}`);
        await loadDir(currentPath);
      } catch (err: unknown) {
        toast.error(`Upload failed: ${apiErr(err)}`);
      }
    },
    [workspace.id, currentPath, loadDir],
  );

  const gitDiffView = useCallback(async () => {
    setGitBusy(true);
    try {
      const res = await api.workspaceGitDiff(workspace.id);
      setDiffText(res?.diff ?? '');
      setShowDiff(true);
    } catch (e: unknown) {
      toast.error(`Git diff failed: ${apiErr(e)}`);
    } finally {
      setGitBusy(false);
    }
  }, [workspace.id]);

  const gitCommit = useCallback(async () => {
    if (!commitMsg.trim()) {
      toast.error('Enter a commit message');
      return;
    }
    setGitBusy(true);
    try {
      await api.workspaceGitAdd(workspace.id, []);
      const res = await api.workspaceGitCommit(workspace.id, commitMsg.trim());
      toast.success(`Committed: ${res?.message ?? ''}`);
      setCommitMsg('');
      await refreshGit();
    } catch (e: unknown) {
      toast.error(`Commit failed: ${apiErr(e)}`);
    } finally {
      setGitBusy(false);
    }
  }, [workspace.id, commitMsg, refreshGit]);

  const gitPush = useCallback(async () => {
    setGitBusy(true);
    try {
      const res = await api.workspaceGitPush(workspace.id);
      toast.success(res?.message ?? 'Pushed');
      await refreshGit();
    } catch (e: unknown) {
      toast.error(`Push failed: ${apiErr(e)}`);
    } finally {
      setGitBusy(false);
    }
  }, [workspace.id, refreshGit]);

  const gitFetch = useCallback(async () => {
    setGitBusy(true);
    try {
      const res = await api.workspaceGitFetch(workspace.id);
      toast.success(res?.message ?? 'Fetched');
      await refreshGit();
    } catch (e: unknown) {
      toast.error(`Fetch failed: ${apiErr(e)}`);
    } finally {
      setGitBusy(false);
    }
  }, [workspace.id, refreshGit]);

  const runLint = useCallback(async () => {
    setToolBusy(true);
    setToolOutput('Running lint...');
    try {
      const res = await api.workspaceLint({ workspace_id: workspace.id, path: activeTab ?? undefined });
      setToolOutput(res?.output ?? JSON.stringify(res));
    } catch (e: unknown) {
      setToolOutput(`Lint failed: ${apiErr(e)}`);
    } finally {
      setToolBusy(false);
    }
  }, [workspace.id, activeTab]);

  const syncNextcloud = useCallback(async () => {
    if (!workspace.nextcloud_path) {
      toast.error('This workspace has no NextCloud path configured');
      return;
    }
    setToolBusy(true);
    setToolOutput('Syncing to NextCloud...');
    try {
      const res = await api.syncWorkspaceNextcloud({
        remote_path: workspace.nextcloud_path,
        local_path: workspace.resolved_path ?? workspace.local_path,
        excludes: workspace.excludes ?? [],
      });
      setToolOutput(JSON.stringify(res, null, 2));
      toast.success('NextCloud sync complete');
    } catch (e: unknown) {
      setToolOutput(`Sync failed: ${apiErr(e)}`);
    } finally {
      setToolBusy(false);
    }
  }, [workspace]);

  const loadMissions = useCallback(async () => {
    setMissionsLoading(true);
    try {
      const list = await api.getWorkspaceMissions(workspace.id, 25);
      setMissions(Array.isArray(list) ? list : []);
    } catch (e: unknown) {
      toast.error(`Failed to load missions: ${apiErr(e)}`);
    } finally {
      setMissionsLoading(false);
    }
  }, [workspace.id]);

  useEffect(() => {
    if (activeView === 'chat') {
      void (async () => {
        await loadMissions();
      })();
    }
  }, [activeView, loadMissions]);

  const sendChat = useCallback(async () => {
    if (!chatInput.trim()) {
      toast.error('Describe a task for Raven');
      return;
    }
    setChatBusy(true);
    try {
      const res = await api.createWorkspaceMission(workspace.id, chatInput.trim(), 3);
      toast.success(`Raven mission #${res.mission?.id ?? '?'} dispatched`);
      setChatInput('');
      await loadMissions();
    } catch (e: unknown) {
      toast.error(`Dispatch failed: ${apiErr(e)}`);
    } finally {
      setChatBusy(false);
    }
  }, [chatInput, workspace.id, loadMissions]);

  const refineLastMission = useCallback(async () => {
    const last = missions[0];
    if (!last) {
      toast.error('No missions to refine');
      return;
    }
    if (!refineInput.trim()) {
      toast.error('Enter a refinement directive');
      return;
    }
    setRefineBusy(true);
    try {
      const res = await api.refineRavenMission(last.id, refineInput.trim());
      toast.success(`Refining mission #${res.mission_id}`);
      setRefineInput('');
      await loadMissions();
    } catch (e: unknown) {
      toast.error(`Refine failed: ${apiErr(e)}`);
    } finally {
      setRefineBusy(false);
    }
  }, [missions, refineInput, loadMissions]);

  const runImageTask = useCallback(
    async (mode: 'txt2img' | 'img2img' | 'upscale' | 'inpaint') => {
      if (mode !== 'txt2img' && !active) {
        toast.error('Select a source image first');
        return;
      }
      if (!sdPrompt.trim() && mode !== 'upscale' && mode !== 'inpaint') {
        toast.error('Enter a prompt');
        return;
      }
      setSdBusy(true);
      try {
        let b64: string | null = null;
        if (mode === 'txt2img') {
          const res = await api.generateImage({ prompt: sdPrompt.trim(), model: sdModel || undefined });
          b64 = res?.data?.[0]?.b64_json ?? null;
        } else {
          const src = await api.fetchWorkspaceFileRaw(workspace.id, active!.path);
          const srcB64 = await new Promise<string>((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => {
              const result = String(reader.result || '');
              const comma = result.indexOf(',');
              resolve(comma >= 0 ? result.slice(comma + 1) : result);
            };
            reader.onerror = () => reject(reader.error);
            reader.readAsDataURL(src);
          });
          const prompt =
            mode === 'upscale'
              ? sdPrompt.trim() || 'upscale to 2x higher resolution, enhance fine details'
              : mode === 'inpaint'
                ? sdPrompt.trim() || 'inpaint and seamlessly improve the masked region'
                : sdPrompt.trim();
          const res = await api.editImage({ prompt, image: srcB64, model: sdModel || undefined });
          b64 = res?.data?.[0]?.b64_json ?? null;
        }
        if (!b64) {
          toast.error('Stable Diffusion returned no image');
          return;
        }
        const fname = `${mode}_${Date.now()}.png`;
        const rel = baseDirOf(currentPath) + fname;
        await api.writeWorkspaceFileBase64(workspace.id, rel, b64);
        toast.success(`Saved ${rel}`);
        await loadDir(currentPath);
        await openByPath(rel);
      } catch (e: unknown) {
        toast.error(`SD task failed: ${apiErr(e)}`);
      } finally {
        setSdBusy(false);
      }
    },
    [active, sdPrompt, sdModel, currentPath, workspace.id, loadDir, openByPath, baseDirOf],
  );

  const breadcrumbs = useMemo(() => {
    if (currentPath === '.') return ['~'];
    return ['~', ...currentPath.split('/')];
  }, [currentPath]);

  const statusMission = missions[0];

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-[#0a0e1a] text-slate-200">
      {/* Title bar */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-white/10 bg-[#0d1222]">
        <div className="flex items-center gap-2 min-w-0">
          <Code2 size={16} className="text-indigo-400 shrink-0" />
          <span className="font-semibold text-white truncate">{workspace.display_name}</span>
          <span className="text-xs text-slate-500 truncate hidden sm:inline">
            {workspace.resolved_path ?? workspace.local_path}
          </span>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={() => setShowSecrets(true)}
            className="p-1.5 text-slate-400 hover:text-white hover:bg-white/10 rounded-lg transition-all"
            aria-label="Secrets & Environment"
            title="Secrets & Environment"
          >
            <KeyRound size={16} />
          </button>
          <button
            onClick={onClose}
            className="p-1.5 text-slate-400 hover:text-white hover:bg-white/10 rounded-lg transition-all"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>
      </div>

      <div className="flex-1 min-h-0 flex">
        {/* Activity Bar */}
        {showSecrets && (
          <WorkspaceSecrets workspace={workspace} onClose={() => setShowSecrets(false)} />
        )}
        <div className="w-12 shrink-0 bg-[#0b0f1a] border-r border-white/10 flex flex-col items-center py-2 gap-1">
          {ACTIVITY.map((a) => {
            const Icon = a.icon;
            const isActive = a.id === 'terminal' && terminalPosition === 'bottom' ? terminalOpen : activeView === a.id;
            return (
              <button
                key={a.id}
                title={a.label}
                onClick={() => {
                  if (a.id === 'terminal') {
                    if (terminalPosition === 'bottom') {
                      setTerminalOpen((prev) => !prev);
                    } else {
                      setActiveView('terminal');
                    }
                  } else {
                    setActiveView(a.id);
                  }
                }}
                className={cn(
                  'relative w-10 h-10 flex items-center justify-center rounded-lg transition-colors',
                  isActive ? 'text-white bg-white/10' : 'text-slate-500 hover:text-slate-200',
                )}
              >
                {isActive && <span className="absolute left-0 top-1.5 bottom-1.5 w-0.5 rounded-r bg-indigo-400" />}
                <Icon size={20} />
              </button>
            );
          })}
        </div>

        {/* Primary Side Bar (switches by view) */}
        <div className="w-80 max-w-[78vw] shrink-0 border-r border-white/10 bg-[#0c1120] flex flex-col min-h-0">
          {activeView === 'explorer' && (
            <>
              <div className="flex items-center gap-1 px-2 py-1.5 text-xs text-slate-400 border-b border-white/5 overflow-x-auto whitespace-nowrap custom-scrollbar">
                {breadcrumbs.map((b, i) => (
                  <span key={i} className="flex items-center">
                    {i > 0 && <ChevronRight size={12} className="mx-0.5 text-slate-600" />}
                    <span className={i === breadcrumbs.length - 1 ? 'text-slate-200' : ''}>{b}</span>
                  </span>
                ))}
              </div>
              <div className="flex items-center gap-1 px-2 py-1.5 border-b border-white/5">
                <button onClick={() => loadDir(currentPath)} className="p-1.5 text-slate-400 hover:text-white hover:bg-white/10 rounded" title="Refresh">
                  <RefreshCw size={15} className={loadingFiles ? 'animate-spin' : ''} />
                </button>
                <button onClick={() => createNew('file')} className="p-1.5 text-slate-400 hover:text-white hover:bg-white/10 rounded" title="New file">
                  <FilePlus size={15} />
                </button>
                <button onClick={() => createNew('folder')} className="p-1.5 text-slate-400 hover:text-white hover:bg-white/10 rounded" title="New folder">
                  <FolderPlus size={15} />
                </button>
                <button onClick={() => fileInputRef.current?.click()} className="p-1.5 text-slate-400 hover:text-white hover:bg-white/10 rounded" title="Upload">
                  <Upload size={15} />
                </button>
                <input ref={fileInputRef} type="file" className="hidden" onChange={onUpload} />
                {currentPath !== '.' && (
                  <button
                    onClick={() => {
                      const parent = currentPath.includes('/')
                        ? currentPath.slice(0, currentPath.lastIndexOf('/'))
                        : '.';
                      loadDir(parent);
                    }}
                    className="p-1.5 text-slate-400 hover:text-white hover:bg-white/10 rounded ml-auto"
                    title="Up one level"
                  >
                    <ChevronUp size={15} />
                  </button>
                )}
              </div>
              <div
                className="flex-1 overflow-y-auto custom-scrollbar py-1"
                onContextMenu={(e) => {
                  e.preventDefault();
                  setCtxMenu({ x: e.clientX, y: e.clientY, entry: null });
                }}
              >
                {loadingFiles && entries.length === 0 ? (
                  <div className="flex items-center justify-center py-8 text-slate-500 text-sm">
                    <Loader2 size={16} className="animate-spin mr-2" /> Loading…
                  </div>
                ) : entries.length === 0 ? (
                  <div className="px-3 py-8 text-center text-slate-600 text-xs">Empty folder</div>
                ) : (
                  entries.map((entry) => (
                    <div
                      key={entry.path}
                      className={cn(
                        'group flex items-center gap-2 px-3 py-1.5 text-sm cursor-pointer hover:bg-white/5',
                        activeTab === entry.path && 'bg-indigo-500/15',
                      )}
                      onClick={() => openFile(entry)}
                      onContextMenu={(e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        setCtxMenu({ x: e.clientX, y: e.clientY, entry });
                      }}
                    >
                      {entry.is_dir ? (
                        <Folder size={15} className="text-amber-400/80 shrink-0" />
                      ) : (
                        <FileIcon size={15} className="text-slate-400 shrink-0" />
                      )}
                      <span className="truncate flex-1">{entry.name}</span>
                      {modifiedPaths.has(entry.path) && (
                        <span
                          className="shrink-0 text-[10px] font-bold px-1 rounded bg-amber-500/20 text-amber-300"
                          title="Uncommitted changes"
                        >
                          {modifiedPaths.get(entry.path)}
                        </span>
                      )}
                      {!entry.is_dir && entry.size != null && (
                        <span className="text-[10px] text-slate-600">{formatBytes(entry.size)}</span>
                      )}
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          deleteEntry(entry);
                        }}
                        className="opacity-0 group-hover:opacity-100 p-1 text-slate-500 hover:text-red-400 rounded"
                        title="Delete"
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                  ))
                )}
              </div>
            </>
          )}

          {activeView === 'git' && (
            <div className="flex-1 overflow-y-auto custom-scrollbar p-3 flex flex-col gap-3">
              <div className="text-[11px] uppercase tracking-wide text-slate-500">Source Control</div>
              {!gitEnabled && (
                <div className="flex items-start gap-2 text-xs text-amber-400 bg-amber-500/10 border border-amber-500/20 rounded p-2">
                  <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                  {hasGit ? 'This workspace is not a Git repository.' : 'This workspace has no git capability configured.'}
                </div>
              )}
              <div className="flex items-center justify-between">
                <span className="text-xs text-slate-400">
                  {gitStatus?.branch ? `Branch: ${gitStatus.branch}` : 'Not a git repo'}
                </span>
                <button onClick={refreshGit} disabled={gitBusy || !gitEnabled} className="p-1 text-slate-400 hover:text-white disabled:opacity-30" title="Refresh">
                  <RefreshCw size={14} className={gitBusy ? 'animate-spin' : ''} />
                </button>
              </div>
              {(gitStatus?.porcelain?.length ?? 0) > 0 && (
                <div className="text-xs font-mono bg-black/40 rounded p-2 max-h-40 overflow-y-auto custom-scrollbar">
                  {gitStatus?.porcelain?.map((line: string, i: number) => (
                    <div key={i} className="text-slate-300 whitespace-pre-wrap">{line}</div>
                  ))}
                </div>
              )}
              <div className="grid grid-cols-2 gap-1.5">
                <button onClick={gitDiffView} disabled={gitBusy || !gitEnabled} className="flex items-center justify-center gap-1 py-1.5 text-xs rounded bg-white/5 hover:bg-white/10 disabled:opacity-30">
                  <GitPullRequest size={13} /> Diff
                </button>
                <button onClick={gitFetch} disabled={gitBusy || !gitEnabled} className="flex items-center justify-center gap-1 py-1.5 text-xs rounded bg-white/5 hover:bg-white/10 disabled:opacity-30">
                  <Terminal size={13} /> Fetch
                </button>
                <button onClick={gitCommit} disabled={gitBusy || !gitEnabled} className="flex items-center justify-center gap-1 py-1.5 text-xs rounded bg-indigo-600 hover:bg-indigo-500 disabled:opacity-30">
                  <GitCommit size={13} /> Commit
                </button>
                <button onClick={gitPush} disabled={gitBusy || !gitEnabled} className="flex items-center justify-center gap-1 py-1.5 text-xs rounded bg-white/5 hover:bg-white/10 disabled:opacity-30">
                  <UploadCloud size={13} /> Push
                </button>
              </div>
              <input
                value={commitMsg}
                onChange={(e) => setCommitMsg(e.target.value)}
                placeholder="Commit message…"
                disabled={!gitEnabled}
                className="w-full px-2 py-1.5 text-xs rounded bg-black/40 border border-white/10 focus:border-indigo-500 outline-none disabled:opacity-40"
              />
              <p className="text-[10px] text-slate-600 leading-relaxed">
                Open the diff in the editor with the <span className="text-slate-400">Diff</span> button above — it renders full-size with syntax highlighting.
              </p>
              {gitLog.length > 0 && (
                <div>
                  <div className="text-[11px] uppercase tracking-wide text-slate-500 mb-1">Recent commits</div>
                  <div className="flex flex-col gap-1">
                    {gitLog.map((c, i) => (
                      <div key={i} className="text-[11px] font-mono text-slate-400 truncate" title={c.message}>
                        <span className="text-indigo-400">{String(c.commit || '').slice(0, 7)}</span> {c.message}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {activeView === 'terminal' && terminalPosition === 'sidebar' && (
            <div className="flex-1 flex flex-col min-h-0 bg-[#0b0f1a]">
              <div className="flex items-center justify-between px-3 py-1.5 border-b border-white/5 bg-[#0d1222]">
                <span className="text-[11px] uppercase tracking-wide text-slate-400 font-semibold">Terminal</span>
                <button
                  onClick={() => {
                    setTerminalPosition('bottom');
                    setTerminalOpen(true);
                    setActiveView('explorer');
                  }}
                  className="p-1 text-slate-400 hover:text-white rounded"
                  title="Move Panel to Bottom"
                >
                  <ChevronUp size={14} />
                </button>
              </div>
              <div className="flex-1 min-h-0">
                <TerminalPane workspace={workspace} />
              </div>
            </div>
          )}

          {activeView === 'tools' && (
            <div className="flex-1 overflow-y-auto custom-scrollbar p-3 flex flex-col gap-3">
              <div className="text-[11px] uppercase tracking-wide text-slate-500">Tools</div>
              <button onClick={runLint} disabled={toolBusy} className="flex items-center justify-center gap-1.5 py-2 text-sm rounded bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40">
                {toolBusy ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
                Lint {activeTab ? 'file' : 'workspace'}
              </button>
              <button onClick={syncNextcloud} disabled={toolBusy || !workspace.nextcloud_path} className="flex items-center justify-center gap-1.5 py-2 text-sm rounded bg-white/5 hover:bg-white/10 disabled:opacity-40" title={workspace.nextcloud_path ? `→ ${workspace.nextcloud_path}` : 'No NextCloud path'}>
                <CloudUpload size={14} /> Sync to NextCloud
              </button>
              {toolOutput && (
                <pre className="text-[11px] font-mono bg-black/40 rounded p-2 max-h-[60vh] overflow-y-auto custom-scrollbar whitespace-pre-wrap text-slate-300">
                  {toolOutput}
                </pre>
              )}
            </div>
          )}

          {activeView === 'chat' && (
            <div className="flex-1 min-h-0 flex flex-col">
              <div className="px-3 py-2 border-b border-white/5 flex items-center gap-2">
                <Bot size={15} className="text-indigo-400" />
                <span className="text-xs font-semibold text-slate-200">Raven Chat</span>
                <span className="text-[10px] text-slate-500">tasks run in this workspace</span>
              </div>
              <div className="flex-1 overflow-y-auto custom-scrollbar p-3 space-y-2">
                {missionsLoading ? (
                  <div className="flex items-center justify-center py-8 text-slate-500 text-sm">
                    <Loader2 size={16} className="animate-spin mr-2" /> Loading…
                  </div>
                ) : missions.length === 0 ? (
                  <div className="px-3 py-8 text-center text-slate-600 text-xs">
                    No missions yet. Describe a task below to dispatch Raven.
                  </div>
                ) : (
                  missions.map((m) => (
                    <div key={m.id} className={cn('rounded border border-white/10 p-2 text-xs', m.id === statusMission?.id && 'border-indigo-500/40 bg-indigo-500/5')}>
                      <div className="flex items-center justify-between mb-1">
                        <span className="font-semibold text-slate-200">#{m.id}</span>
                        <span className="text-[10px] uppercase text-slate-500">{m.status}</span>
                      </div>
                      <div className="text-slate-400 line-clamp-2">{m.proposed_mission}</div>
                      {m.last_llm_reply && (
                        <div className="mt-1.5 text-[11px] text-slate-300 bg-black/30 rounded p-1.5 max-h-24 overflow-y-auto custom-scrollbar whitespace-pre-wrap">
                          {m.last_llm_reply.slice(0, 400)}
                          {m.last_llm_reply.length > 400 && '…'}
                        </div>
                      )}
                    </div>
                  ))
                )}
              </div>
              <div className="border-t border-white/10 p-2 space-y-2">
                <div className="flex gap-2">
                  <textarea
                    value={chatInput}
                    onChange={(e) => setChatInput(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                        e.preventDefault();
                        void sendChat();
                      }
                    }}
                    placeholder="Describe a task for Raven to run in this workspace…  (⌘/Ctrl+Enter to send)"
                    rows={2}
                    className="flex-1 bg-black/50 border border-white/10 rounded-lg px-2 py-1.5 text-xs text-white placeholder-slate-600 focus:border-indigo-500 outline-none resize-none"
                  />
                  <button
                    onClick={sendChat}
                    disabled={chatBusy || !chatInput.trim()}
                    className="px-3 self-stretch flex items-center justify-center rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white"
                    title="Dispatch Raven mission"
                  >
                    {chatBusy ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
                  </button>
                </div>
                {missions.length > 0 && (
                  <div className="flex gap-2">
                    <input
                      value={refineInput}
                      onChange={(e) => setRefineInput(e.target.value)}
                      placeholder={`Refine last mission #${missions[0].id}…`}
                      className="flex-1 bg-black/40 border border-white/10 rounded-lg px-2 py-1.5 text-xs text-white placeholder-slate-600 focus:border-indigo-500 outline-none"
                    />
                    <button
                      onClick={refineLastMission}
                      disabled={refineBusy || !refineInput.trim()}
                      className="px-3 rounded-lg bg-white/10 hover:bg-white/20 disabled:opacity-40 text-xs text-slate-200"
                    >
                      Refine
                    </button>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>

        {/* Editor / Preview */}
        <div className="flex-1 min-w-0 flex flex-col bg-[#0a0e1a]">
          <div className="flex-1 min-h-0 flex flex-col">
          {showDiff ? (
            <div className="flex-1 min-h-0 flex flex-col">
              <div className="flex items-center justify-between px-3 py-1.5 border-b border-white/10 bg-[#0c1120]">
                <div className="flex items-center gap-2 min-w-0">
                  <GitPullRequest size={14} className="text-indigo-400 shrink-0" />
                  <span className="text-sm text-slate-300 truncate">Git Diff — working tree vs HEAD</span>
                </div>
                <button
                  onClick={() => setShowDiff(false)}
                  className="flex items-center gap-1 px-2.5 py-1.5 text-xs rounded bg-white/10 hover:bg-white/20 text-slate-200"
                >
                  <X size={13} /> Close
                </button>
              </div>
              <div className="flex-1 min-h-0">
                {diffText ? (
                  <CodeEditor value={diffText} readOnly language="diff" height="100%" />
                ) : (
                  <div className="flex items-center justify-center h-full text-slate-600 text-sm">
                    No changes to show
                  </div>
                )}
              </div>
            </div>
          ) : active ? (
            <>
              {/* Tab bar */}
              <div className="flex items-stretch bg-[#0c1120] border-b border-white/10 overflow-x-auto custom-scrollbar shrink-0">
                {tabs.map((t) => (
                  <div
                    key={t.path}
                    onClick={() => setActiveTab(t.path)}
                    className={cn(
                      'group flex items-center gap-1.5 pl-3 pr-2 py-2 text-xs cursor-pointer border-r border-white/10 whitespace-nowrap',
                      activeTab === t.path ? 'bg-[#0a0e1a] text-white' : 'text-slate-400 hover:bg-white/5',
                    )}
                    title={t.path}
                  >
                    {t.kind === 'image' ? (
                      <ImageIcon size={13} className="text-indigo-400 shrink-0" />
                    ) : t.kind === 'audio' || t.kind === 'video' ? (
                      <Music size={13} className="text-indigo-400 shrink-0" />
                    ) : (
                      <FileText size={13} className="text-indigo-400 shrink-0" />
                    )}
                    <span className="truncate max-w-[160px]">{t.path.split('/').pop()}</span>
                    {t.dirty && (
                      <span className="text-amber-400 text-[10px] leading-none" title="Unsaved changes">
                        ●
                      </span>
                    )}
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        closeTab(t.path);
                      }}
                      className="opacity-0 group-hover:opacity-100 p-0.5 text-slate-500 hover:text-red-400 rounded"
                      title="Close tab"
                    >
                      <X size={12} />
                    </button>
                  </div>
                ))}
              </div>

              {active.kind === 'image' ? (
                <>
                  <div className="flex items-center justify-between px-3 py-1.5 border-b border-white/10 bg-[#0c1120]">
                    <div className="flex items-center gap-2 min-w-0">
                      <ImageIcon size={14} className="text-indigo-400 shrink-0" />
                      <span className="text-sm text-slate-300 truncate">{active.path}</span>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <button onClick={downloadImage} className="p-1.5 text-slate-400 hover:text-white hover:bg-white/10 rounded" title="Download">
                        <Download size={15} />
                      </button>
                    </div>
                  </div>
                  <div className="flex-1 min-h-0 flex">
                    <div className="flex-1 min-w-0 flex items-center justify-center bg-black/40 p-4 overflow-auto">
                      {active.imageUrl ? (
                        <ImageViewer url={active.imageUrl} />
                      ) : (
                        <span className="text-sm text-slate-600">No preview</span>
                      )}
                    </div>
                    <div className="w-80 shrink-0 border-l border-white/10 bg-[#0c1120] overflow-y-auto custom-scrollbar p-3 flex flex-col gap-3">
                      <div className="flex items-center gap-1.5 text-[11px] uppercase tracking-wide text-slate-500">
                        <Wand2 size={13} className="text-indigo-400" /> Stable Diffusion
                      </div>
                      <textarea
                        value={sdPrompt}
                        onChange={(e) => setSdPrompt(e.target.value)}
                        placeholder="Prompt for generate / edit / inpaint…"
                        rows={3}
                        className="w-full bg-black/40 border border-white/10 rounded-lg px-2 py-1.5 text-xs text-white placeholder-slate-600 focus:border-indigo-500 outline-none resize-none"
                      />
                      {imageModels.length > 0 && (
                        <select
                          value={sdModel}
                          onChange={(e) => setSdModel(e.target.value)}
                          className="w-full bg-black/40 border border-white/10 rounded-lg px-2 py-1.5 text-xs text-white focus:border-indigo-500 outline-none"
                        >
                          {imageModels.map((m) => (
                            <option key={m} value={m}>{m}</option>
                          ))}
                        </select>
                      )}
                      <button
                        onClick={() => runImageTask('txt2img')}
                        disabled={sdBusy || !sdPrompt.trim()}
                        className="flex items-center justify-center gap-1.5 py-2 text-xs rounded bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white"
                      >
                        <Wand2 size={13} /> Generate (txt2img)
                      </button>
                      <button
                        onClick={() => runImageTask('img2img')}
                        disabled={sdBusy}
                        className="flex items-center justify-center gap-1.5 py-2 text-xs rounded bg-white/5 hover:bg-white/10 disabled:opacity-40 text-slate-200"
                      >
                        <Brush size={13} /> Edit image (img2img)
                      </button>
                      <button
                        onClick={() => runImageTask('upscale')}
                        disabled={sdBusy}
                        className="flex items-center justify-center gap-1.5 py-2 text-xs rounded bg-white/5 hover:bg-white/10 disabled:opacity-40 text-slate-200"
                      >
                        <Maximize2 size={13} /> Upscale
                      </button>
                      <button
                        onClick={() => runImageTask('inpaint')}
                        disabled={sdBusy}
                        className="flex items-center justify-center gap-1.5 py-2 text-xs rounded bg-white/5 hover:bg-white/10 disabled:opacity-40 text-slate-200"
                      >
                        <Eye size={13} /> Inpaint
                      </button>
                      {sdBusy && (
                        <div className="flex items-center gap-1.5 text-xs text-slate-400">
                          <Loader2 size={13} className="animate-spin" /> Processing…
                        </div>
                      )}
                      <p className="text-[10px] text-slate-600 leading-relaxed">
                        Results are saved into the current folder and open automatically. Edit/Upscale/Inpaint use the selected image as the source.
                      </p>
                    </div>
                  </div>
                </>
              ) : active.kind === 'markdown' ? (
                <>
                  <div className="flex items-center justify-between px-3 py-1.5 border-b border-white/10 bg-[#0c1120]">
                    <div className="flex items-center gap-2 min-w-0">
                      <FileText size={14} className="text-indigo-400 shrink-0" />
                      <span className="text-sm text-slate-300 truncate">{active.path}</span>
                      {active.dirty && <span className="text-[10px] text-amber-400">● unsaved</span>}
                    </div>
                    <div className="flex items-center gap-1.5">
                      <button
                        onClick={() => setMdRichPaths((prev) => ({ ...prev, [active.path]: !(prev[active.path] ?? false) }))}
                        className={cn('px-2.5 py-1.5 text-xs font-medium rounded', mdRichPaths[active.path] ? 'bg-indigo-600/30 text-indigo-300 hover:bg-indigo-600/50' : 'bg-white/10 hover:bg-white/20 text-slate-200')}
                        title={mdRichPaths[active.path] ? 'Switch to source view' : 'Switch to rich markdown editor'}
                      >
                        {mdRichPaths[active.path] ? 'Source' : 'Rich'}
                      </button>
                      <button onClick={downloadFile} disabled={!active} className="p-1.5 text-slate-400 hover:text-white hover:bg-white/10 rounded disabled:opacity-30" title="Download"><Download size={15} /></button>
                      <button onClick={saveFile} disabled={!active.dirty || saving} className="flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium rounded bg-indigo-600 hover:bg-indigo-500 text-white disabled:opacity-40" title="Save (Ctrl+S)">{saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />} Save</button>
                    </div>
                  </div>
                  <div className="flex-1 min-h-0">
                    {mdRichPaths[active.path] ? (
                      <MarkdownEditor
                        ref={markdownEditorRef}
                        workspaceId={workspace.id}
                        path={active.path}
                        initialMarkdown={active.content}
                        onDirtyChange={(dirty) => {
                          setTabs((prev) => prev.map((t) => (t.path === active.path ? { ...t, dirty } : t)));
                        }}
                      />
                    ) : (
                      <MarkdownViewer value={active.content} onChange={onEditorChange} height="100%" />
                    )}
                  </div>
                </>
              ) : active.kind === 'audio' || active.kind === 'video' ? (
                <>
                  <div className="flex items-center justify-between px-3 py-1.5 border-b border-white/10 bg-[#0c1120]">
                    <div className="flex items-center gap-2 min-w-0">
                      <Music size={14} className="text-indigo-400 shrink-0" />
                      <span className="text-sm text-slate-300 truncate">{active.path}</span>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <button onClick={() => active.blobUrl && downloadBlob(active.blobUrl, active.path)} disabled={!active.blobUrl} className="p-1.5 text-slate-400 hover:text-white hover:bg-white/10 rounded disabled:opacity-30" title="Download"><Download size={15} /></button>
                    </div>
                  </div>
                  <div className="flex-1 min-h-0 flex items-center justify-center p-4">
                    {active.kind === 'audio' ? (
                      <audio controls src={active.blobUrl ?? undefined} className="w-full max-w-2xl" />
                    ) : (
                      <video controls src={active.blobUrl ?? undefined} className="max-w-full max-h-full" />
                    )}
                  </div>
                </>
              ) : active.kind === 'pdf' || active.kind === 'docx' || active.kind === 'xlsx' || active.kind === 'odf' ? (
                <>
                  <div className="flex items-center justify-between px-3 py-1.5 border-b border-white/10 bg-[#0c1120]">
                    <div className="flex items-center gap-2 min-w-0">
                      <FileText size={14} className="text-indigo-400 shrink-0" />
                      <span className="text-sm text-slate-300 truncate">{active.path}</span>
                    </div>
                    <div className="flex items-center gap-1.5">
                      {(active.kind === 'docx' || active.kind === 'odf') && (
                        <button onClick={saveDocumentTab} disabled={saving} className="flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium rounded bg-indigo-600 hover:bg-indigo-500 text-white disabled:opacity-40" title="Save (Ctrl+S)">
                          {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
                          Save
                        </button>
                      )}
                      <button onClick={() => active.blobUrl && downloadBlob(active.blobUrl, active.path)} disabled={!active.blobUrl} className="p-1.5 text-slate-400 hover:text-white hover:bg-white/10 rounded disabled:opacity-30" title="Download"><Download size={15} /></button>
                    </div>
                  </div>
                  <div className="flex-1 min-h-0">
                    {active.kind === 'pdf' && <PdfViewer url={active.blobUrl ?? ''} />}
                    {active.kind === 'docx' && <DocxEditor ref={docxEditorRef} url={active.blobUrl ?? ''} workspaceId={workspace.id} path={active.path} />}
                    {active.kind === 'xlsx' && <ExcelViewer url={active.blobUrl ?? ''} />}
                    {active.kind === 'odf' && <OdfViewer ref={odfViewerRef} url={active.blobUrl ?? ''} workspaceId={workspace.id} path={active.path} />}
                  </div>
                </>
              ) : (
                <>
                  <div className="flex items-center justify-between px-3 py-1.5 border-b border-white/10 bg-[#0c1120]">
                    <div className="flex items-center gap-2 min-w-0">
                      <FileText size={14} className="text-indigo-400 shrink-0" />
                      <span className="text-sm text-slate-300 truncate">{active.path}</span>
                      {active.dirty && <span className="text-[10px] text-amber-400">● unsaved</span>}
                    </div>
                    <div className="flex items-center gap-1.5">
                      <button onClick={() => setVimMode((v) => !v)} className={cn('px-2.5 py-1.5 text-xs font-medium rounded', vimMode ? 'bg-emerald-600/30 text-emerald-300 hover:bg-emerald-600/50' : 'bg-white/10 hover:bg-white/20 text-slate-200')} title="Toggle VIM keybindings">
                        Vim
                      </button>
                      <button onClick={validateFile} className="px-2.5 py-1.5 text-xs font-medium rounded bg-white/10 hover:bg-white/20 text-slate-200" title="Validate / lint file">
                        Validate
                      </button>
                      <button onClick={gitDiffView} disabled={gitBusy || !gitEnabled} className="flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium rounded bg-white/10 hover:bg-white/20 text-slate-200 disabled:opacity-40" title="View diff in editor">
                        <GitPullRequest size={13} /> Diff
                      </button>
                      <button onClick={downloadFile} disabled={!active} className="p-1.5 text-slate-400 hover:text-white hover:bg-white/10 rounded disabled:opacity-30" title="Download">
                        <Download size={15} />
                      </button>
                      <button onClick={saveFile} disabled={!active.dirty || saving} className="flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium rounded bg-indigo-600 hover:bg-indigo-500 text-white disabled:opacity-40" title="Save (Ctrl+S)">
                        {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
                        Save
                      </button>
                    </div>
                  </div>
                  <div className="flex-1 min-h-0 flex flex-col">
                    <div className="flex-1 min-h-0">
                      <CodeEditor
                        value={active.content}
                        onChange={onEditorChange}
                        language={language as never}
                        vim={vimMode}
                        height="100%"
                      />
                    </div>
                    {problems.open && (
                      <div className="shrink-0 h-44 border-t border-white/10 bg-[#0b0f1a] flex flex-col">
                        <div className="flex items-center justify-between px-3 py-1 border-b border-white/5">
                          <span className="text-[11px] uppercase tracking-wide text-slate-400">
                            Problems {problems.count > 0 ? `(${problems.count})` : ''}
                          </span>
                          <button onClick={() => setProblems((p) => ({ ...p, open: false }))} className="p-1 text-slate-500 hover:text-white rounded" title="Close">
                            <X size={12} />
                          </button>
                        </div>
                        <pre className="flex-1 overflow-auto custom-scrollbar text-[11px] font-mono text-slate-300 p-2 whitespace-pre-wrap">
                          {problems.text}
                        </pre>
                      </div>
                    )}
                  </div>
                </>
              )}
            </>
          ) : (
            <div className="flex flex-col items-center justify-center h-full text-slate-600 gap-2">
              <FolderOpen size={42} />
              <p className="text-sm">Select a file from the Explorer to edit</p>
            </div>
          )}
          </div>
          {terminalOpen && terminalPosition === 'bottom' && (
            <div
              style={{ height: `${terminalHeight}px` }}
              className="shrink-0 border-t border-white/10 bg-[#0b0f1a] flex flex-col relative"
            >
              {/* Drag resize handle */}
              <div
                onMouseDown={startResize}
                className="absolute top-0 left-0 right-0 h-1 cursor-ns-resize hover:bg-indigo-500/50 transition-colors z-20"
              />
              
              {/* Header bar */}
              <div className="flex items-center justify-between px-3 py-1.5 border-b border-white/5 bg-[#0d1222]">
                <span className="text-[11px] uppercase tracking-wide text-slate-400 font-semibold">
                  Terminal
                </span>
                <div className="flex items-center gap-1.5">
                  <button
                    onClick={() => {
                      setTerminalPosition('sidebar');
                      setTerminalOpen(false);
                      setActiveView('terminal');
                    }}
                    className="p-1 text-slate-400 hover:text-white rounded transition-colors"
                    title="Move Panel to Sidebar"
                  >
                    <ChevronRight size={13} />
                  </button>
                  <button
                    onClick={() => setTerminalOpen(false)}
                    className="p-1 text-slate-400 hover:text-white rounded transition-colors"
                    title="Close Terminal"
                  >
                    <X size={13} />
                  </button>
                </div>
              </div>
              
              {/* Terminal content */}
              <div className="flex-1 min-h-0">
                <TerminalPane workspace={workspace} />
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Status bar */}
      <div className="h-7 shrink-0 bg-[#0d1222] border-t border-white/10 flex items-center gap-4 px-3 text-[11px] text-slate-400">
        <span className="flex items-center gap-1">
          <GitBranch size={12} />
          {gitEnabled ? (
            <select
              value={gitStatus?.branch ?? ''}
              onChange={(e) => {
                const v = e.target.value;
                if (v === '__new__') {
                  const name = prompt('New branch name:');
                  if (name) void switchBranch(name.trim(), true);
                } else if (v) {
                  void switchBranch(v);
                }
              }}
              disabled={branchBusy || (branches.length === 0 && !gitStatus?.branch)}
              className="bg-transparent text-[11px] text-slate-200 outline-none cursor-pointer disabled:opacity-50 max-w-[140px]"
              title="Switch branch"
            >
              {(branches.length ? branches : gitStatus?.branch ? [gitStatus.branch] : []).map((b) => (
                <option key={b} value={b} className="bg-[#0d1222] text-slate-200">{b}</option>
              ))}
              <option value="__new__" className="bg-[#0d1222] text-slate-200">+ New branch…</option>
            </select>
          ) : (
            'no git repo'
          )}
        </span>
        <span className="truncate max-w-[40%]">{activeTab ? `📄 ${activeTab}` : 'No file open'}</span>
        {tabs.length > 1 && <span className="text-slate-600">{tabs.length} open</span>}
        <span className="ml-auto">{workspace.display_name}</span>
      </div>

      {ctxMenu && (
        <>
          <div
            className="fixed inset-0 z-40"
            onClick={closeCtxMenu}
            onContextMenu={(e) => {
              e.preventDefault();
              closeCtxMenu();
            }}
          />
          <div
            className="fixed z-50 min-w-[190px] rounded-md border border-white/10 bg-[#0d1222] shadow-xl py-1 text-sm"
            style={{
              top: Math.min(ctxMenu.y, window.innerHeight - 230),
              left: Math.min(ctxMenu.x, window.innerWidth - 210),
            }}
            onClick={(e) => e.stopPropagation()}
          >
            {ctxMenu.entry ? (
              <>
                <FileCtxMenuItem
                  icon={ctxMenu.entry.is_dir ? <Folder size={14} /> : <FileIcon size={14} />}
                  label={ctxMenu.entry.is_dir ? 'Open' : 'Open'}
                  onClick={() => {
                    closeCtxMenu();
                    void openFile(ctxMenu.entry!);
                  }}
                />
                {!ctxMenu.entry.is_dir && (
                  <FileCtxMenuItem
                    icon={<Download size={14} />}
                    label="Download"
                    onClick={() => void downloadEntry(ctxMenu.entry!)}
                  />
                )}
                <FileCtxMenuItem
                  icon={<Copy size={14} />}
                  label="Copy path"
                  onClick={() => copyPath(ctxMenu.entry!)}
                />
                <FileCtxMenuItem
                  icon={<Pencil size={14} />}
                  label="Rename"
                  onClick={() => void renameEntry(ctxMenu.entry!)}
                />
                <div className="my-1 h-px bg-white/10" />
                <FileCtxMenuItem
                  icon={<Trash2 size={14} />}
                  label="Delete"
                  danger
                  onClick={() => void deleteEntry(ctxMenu.entry!)}
                />
              </>
            ) : (
              <>
                <FileCtxMenuItem
                  icon={<FilePlus size={14} />}
                  label="New file here"
                  onClick={() => {
                    closeCtxMenu();
                    void createNew('file');
                  }}
                />
                <FileCtxMenuItem
                  icon={<FolderPlus size={14} />}
                  label="New folder here"
                  onClick={() => {
                    closeCtxMenu();
                    void createNew('folder');
                  }}
                />
                <FileCtxMenuItem
                  icon={<Upload size={14} />}
                  label="Upload here"
                  onClick={() => {
                    closeCtxMenu();
                    fileInputRef.current?.click();
                  }}
                />
                <div className="my-1 h-px bg-white/10" />
                <FileCtxMenuItem
                  icon={<RefreshCw size={14} />}
                  label="Refresh"
                  onClick={() => {
                    closeCtxMenu();
                    void loadDir(currentPath);
                  }}
                />
              </>
            )}
          </div>
        </>
      )}
    </div>
  );
}
