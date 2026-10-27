import { useState, useMemo, type FormEvent } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Database,
  Folder,
  File,
  ChevronRight,
  ChevronDown,
  ChevronLeft,
  Search,
  RefreshCw,
  HardDrive,
  Info,
  CheckCircle2,
  Clock,
  ShieldAlert,
  AlertTriangle,
  Sparkles,
  Brain,
  Trash2,
  Pencil,
  ArrowUpDown,
} from 'lucide-react';
import toast from 'react-hot-toast';
import { api, type StorageEntry, type RagStats, type StorageStatus, type SearchResult } from '../services/api';
import { useAuth } from '../context/AuthContext';
import Modal from '../components/ui/Modal';
import { confirmDialog } from '../components/ui/confirmDialog';

/** The two backends a full reindex can target, with the copy each one shows. */
const REINDEX_PROVIDERS: {
  id: 'nextcloud' | 'calibre';
  label: string;
  heading: string;
  blurb: string;
}[] = [
  {
    id: 'nextcloud',
    label: 'NextCloud',
    heading: 'Full NextCloud Reindex',
    blurb: 'Bypass checkpoint and re-process all files from NextCloud. Clears existing data before re-indexing.',
  },
  {
    id: 'calibre',
    label: 'Calibre library',
    heading: 'Reindex the Calibre Library',
    blurb: 'Re-read every book on the shelf and rebuild the calibre_files collection from its extracted text. Requires the calibre_library_path setting (Admin > LLM & Settings).',
  },
];

/** Friendly names for the RAG collections that stats and search report. */
const COLLECTION_LABELS: Record<string, string> = {
  nextcloud_files: 'Nextcloud',
  calibre_files: 'Calibre',
  ha_entities: 'Home Assistant',
  system_capabilities: 'Capabilities',
  system_learnings: 'Raven lessons',
  mission_history: 'Missions',
  telemetry_alerts: 'Alerts',
};

const collectionLabel = (name?: string) => (name && COLLECTION_LABELS[name]) || name || '';

/** The service's own `detail` (e.g. "NextCloud credentials not configured"), else the message. */
const errorDetail = (err: unknown, fallback: string): string => {
  const detail = (err as { response?: { data?: { detail?: unknown } } } | null)?.response?.data?.detail;
  if (typeof detail === 'string' && detail.trim()) return detail;
  const message = (err as { message?: unknown } | null)?.message;
  return typeof message === 'string' && message ? message : fallback;
};

const CRAWL_PHASE_LABELS: Record<string, string> = {
  starting: 'Starting',
  listing: 'Listing entries',
  extracting: 'Extracting text',
  syncing: 'Syncing to RAG',
};

const formatSize = (size?: number | null) => (size ? `${(size / 1024).toFixed(1)} KB` : '0 KB');

const KnowledgeHub = () => {
  const [currentPath, setCurrentPath] = useState('/');
  const queryClient = useQueryClient();
  const { user } = useAuth();
  // Lessons steer every Raven mission, so only admins may rewrite or delete them.
  const isAdmin = !!user?.is_admin;
  const [fileFilter, setFileFilter] = useState('');
  const [purgeModalCollection, setPurgeModalCollection] = useState<string | null>(null);
  const [fullReindexForce, setFullReindexForce] = useState(false);
  const [indexForce, setIndexForce] = useState(false);
  const [reindexProvider, setReindexProvider] = useState<'nextcloud' | 'calibre'>('nextcloud');

  const [ragQuery, setRagQuery] = useState('');
  const [ragResults, setRagResults] = useState<SearchResult | null>(null);
  const [ragLoading, setRagLoading] = useState(false);
  const [ragError, setRagError] = useState<string | null>(null);

  const [learningSort, setLearningSort] = useState<'recent' | 'reuse'>('recent');
  const [expandedLearning, setExpandedLearning] = useState<string | null>(null);
  const [editLearning, setEditLearning] = useState<{ id: string; content: string } | null>(null);
  const [editDraft, setEditDraft] = useState('');
  const [editSaving, setEditSaving] = useState(false);

  const runRagSearch = async (e: FormEvent) => {
    e.preventDefault();
    const q = ragQuery.trim();
    if (!q) return;
    setRagLoading(true);
    setRagError(null);
    setRagResults(null);
    try {
      const data = await api.globalSearch(q);
      setRagResults(data);
      if (!data.files || data.files.length === 0) {
        setRagError('No results found for that query.');
      }
    } catch {
      setRagError('Search failed. Please try again.');
    } finally {
      setRagLoading(false);
    }
  };

  const { data: stats, isLoading: statsLoading, isError: statsError } = useQuery<RagStats>({
    queryKey: ['rag-stats'],
    queryFn: () => api.getRagStats(),
    refetchInterval: 10000,
  });

  const { data: storageStatus } = useQuery<StorageStatus>({
    queryKey: ['storage-status'],
    queryFn: () => api.getStorageStatus(),
    // Poll fast only while a crawl is running; an idle indexer has nothing
    // to report, and 2-second polling of a stopped crawl is just noise.
    refetchInterval: (query) =>
      (query.state.data as StorageStatus | undefined)?.crawl?.active ? 2000 : 15000,
  });

  const {
    data: files = [],
    isLoading: filesLoading,
    isFetching: filesFetching,
    error: filesError,
  } = useQuery<StorageEntry[]>({
    queryKey: ['storage-files', currentPath],
    queryFn: () => api.getStorageFiles(currentPath),
    // A 400 (no Nextcloud credentials) will not fix itself on a retry.
    retry: false,
  });

  const visibleFiles = useMemo(() => {
    const needle = fileFilter.trim().toLowerCase();
    return needle ? files.filter((f) => f.name.toLowerCase().includes(needle)) : files;
  }, [files, fileFilter]);

  const {
    data: learnings,
    isLoading: learningsLoading,
    isError: learningsError,
    error: learningsErrorObj,
    refetch: refetchLearnings,
  } = useQuery({
    queryKey: ['raven-learnings', learningSort],
    queryFn: () => api.getRavenLearnings(200, learningSort),
  });

  interface LearningGroup {
    key: string;
    topic: string;
    items: NonNullable<typeof learnings>['items'];
    totalUsage: number;
    maxUsage: number;
    topTags: string[];
    latestUsed: string;
  }

  // Group lessons by topic so related lessons share one collapsible header,
  // drastically cutting page real-estate versus one card per lesson.
  const groupedLearnings = useMemo<LearningGroup[]>(() => {
    if (!learnings || learnings.items.length === 0) return [];
    const byTopic = new Map<string, NonNullable<typeof learnings>['items']>();
    for (const item of learnings.items) {
      const topic =
        typeof (item.metadata as Record<string, unknown> | undefined)?.topic === 'string'
          ? ((item.metadata as Record<string, unknown>).topic as string)
          : 'Untitled lesson';
      const list = byTopic.get(topic) ?? [];
      list.push(item);
      byTopic.set(topic, list);
    }
    const groups: LearningGroup[] = [];
    for (const [topic, items] of byTopic.entries()) {
      const usages = items.map((i) => i.usage_count || 0);
      const totalUsage = usages.reduce((a, b) => a + b, 0);
      const maxUsage = usages.reduce((a, b) => Math.max(a, b), 0);
      const tagCounts = new Map<string, number>();
      for (const i of items) {
        const tags = (i.metadata as Record<string, unknown> | undefined)?.tags;
        if (Array.isArray(tags)) {
          for (const t of tags as string[]) {
            tagCounts.set(t, (tagCounts.get(t) ?? 0) + 1);
          }
        }
      }
      const topTags = [...tagCounts.entries()]
        .sort((a, b) => b[1] - a[1])
        .slice(0, 3)
        .map(([t]) => t);
      const usedDates = items
        .map((i) => i.last_used_at)
        .filter((d): d is string => typeof d === 'string' && d.length > 0)
        .sort();
      const latestUsed = usedDates.length
        ? new Date(usedDates[usedDates.length - 1]).toLocaleDateString()
        : 'never';
      groups.push({ key: topic, topic, items, totalUsage, maxUsage, topTags, latestUsed });
    }
    return groups;
  }, [learnings]);

  const deleteLearningMutation = useMutation({
    mutationFn: (id: string) => api.deleteRavenLearning(id),
    onSuccess: () => {
      toast.success('Lesson deleted');
      queryClient.invalidateQueries({ queryKey: ['raven-learnings'] });
    },
    onError: (err: Error) => toast.error(errorDetail(err, 'Could not delete that lesson')),
  });

  const saveLearningMutation = useMutation({
    mutationFn: (payload: { id: string; content: string }) =>
      api.editRavenLearning(payload.id, { content: payload.content }),
    onSuccess: () => {
      toast.success('Lesson updated');
      setEditLearning(null);
      queryClient.invalidateQueries({ queryKey: ['raven-learnings'] });
    },
    onError: (err: Error) => toast.error(errorDetail(err, 'Could not update that lesson')),
  });

  const indexMutation = useMutation({
    mutationFn: ({ path, recursive = true, force = false }: { path: string; recursive?: boolean; force?: boolean }) => 
      force ? api.triggerIndexingForce(path, recursive) : api.triggerIndexing(path, recursive),
    onSuccess: () => {
      toast.success('Indexing started in background');
      queryClient.invalidateQueries({ queryKey: ['rag-stats'] });
    },
    onError: (err: Error) => {
      toast.error(errorDetail(err, 'Failed to start indexing'));
    },
  });

  const fullReindexMutation = useMutation({
    mutationFn: ({ force = false, provider }: { force?: boolean; provider: 'nextcloud' | 'calibre' }) =>
      api.triggerFullIndex({ kind: provider }, { force }),
    onSuccess: (_data, vars) => {
      toast.success(
        vars.provider === 'calibre'
          ? 'Calibre library crawl started in background'
          : 'Full NextCloud reindex started in background',
      );
      queryClient.invalidateQueries({ queryKey: ['rag-stats'] });
      queryClient.invalidateQueries({ queryKey: ['storage-status'] });
    },
    onError: (err: Error) => {
      // Surface the gateway's own detail: an unset calibre_library_path is
      // answered as a 400 naming the setting, and "Request failed with status
      // code 400" would hide exactly what the operator has to fix.
      toast.error(errorDetail(err, 'Failed to start full reindex'));
    },
  });

  const purgeMutation = useMutation({
    mutationFn: (collectionName: string) => api.purgeRagCollection(collectionName),
    onSuccess: (data, collectionName) => {
      queryClient.invalidateQueries({ queryKey: ['rag-stats'] });
      setPurgeModalCollection(null);
      // Say what actually happened: a purge that matched nothing is not a success story.
      const removed = typeof data?.removed === 'number' ? data.removed : null;
      if (removed === 0) {
        toast(`Nothing to purge: you have no indexed ${collectionLabel(collectionName)} entries`);
      } else {
        toast.success(removed === null ? 'Collection purged' : `Removed ${removed.toLocaleString()} entries`);
      }
    },
    onError: (err: Error) => toast.error(errorDetail(err, 'Purge failed')),
  });

  const reindexInfo =
    REINDEX_PROVIDERS.find((p) => p.id === reindexProvider) ?? REINDEX_PROVIDERS[0];
  const crawl = storageStatus?.crawl;

  const breadcrumbs = useMemo(() => {
    const parts = currentPath.split('/').filter(Boolean);
    return [{ name: 'Root', path: '/' }, ...parts.map((part, i) => ({
      name: part,
      path: '/' + parts.slice(0, i + 1).join('/')
    }))];
  }, [currentPath]);

  const handleNavigate = (path: string) => {
    setCurrentPath(path);
    setFileFilter('');
  };

  const handleGoBack = () => {
    if (currentPath === '/') return;
    const parts = currentPath.split('/').filter(Boolean);
    parts.pop();
    handleNavigate('/' + parts.join('/'));
  };

  const handleManualIndex = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const formData = new FormData(e.currentTarget);
    const path = (formData.get('path') as string).trim();
    const recursive = formData.get('recursive') === 'on';
    const force = formData.get('force') === 'on';
    
    if (path) {
      indexMutation.mutate({ path, recursive, force });
      e.currentTarget.reset();
    } else {
      toast.error('Please enter a valid path');
    }
  };

  return (
    <div className="space-y-8 pb-12 animate-in fade-in duration-500">
      <header className="flex flex-col gap-6 md:flex-row md:items-center md:justify-between">
        <div className="min-w-0">
          <h2 className="text-3xl font-bold tracking-tight text-white">Knowledge Hub</h2>
          <p className="mt-1 text-slate-400">
            What Jarvis knows: search it, and index your Nextcloud files and Calibre books into it.
          </p>
        </div>
        <div className="flex gap-4">
           <div className="glass-card flex items-center gap-3 px-6 py-3">
              <Database className="text-indigo-400" size={20} />
              <div>
                <p className="text-[10px] font-black uppercase tracking-widest text-slate-500 leading-none">RAG Status</p>
                {/* Derived from the stats request, not a fixed label. */}
                <p
                  data-testid="rag-status"
                  className={`text-sm font-bold mt-1 ${statsError ? 'text-red-300' : 'text-white'}`}
                >
                  {statsError
                    ? 'Unavailable'
                    : statsLoading
                      ? 'Checking…'
                      : crawl?.active
                        ? 'Indexing…'
                        : 'Online'}
                </p>
              </div>
           </div>
        </div>
      </header>

      {/* RAG Semantic Search: the home for semantic memory / indexed storage */}
      <section className="glass-panel p-6 border-indigo-500/20 bg-indigo-950/5">
        <div className="flex items-center gap-2 mb-4 flex-wrap">
          <Brain size={16} className="text-indigo-300" />
          <h3 className="font-bold text-indigo-300">Semantic Memory Search</h3>
          <span className="text-[10px] uppercase tracking-widest text-slate-500">
            RAG · indexed files, Home Assistant, missions &amp; more
          </span>
        </div>
        <form onSubmit={runRagSearch} className="relative">
          <Brain className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-500" size={16} />
          <input
            type="text"
            value={ragQuery}
            onChange={(e) => setRagQuery(e.target.value)}
            placeholder="Ask the brain: search indexed files, Home Assistant devices, missions…"
            className="glass-input w-full py-3 pl-10 pr-28 text-sm"
            aria-label="Semantic memory search"
          />
          <button
            type="submit"
            disabled={ragLoading}
            className="absolute right-1.5 top-1/2 -translate-y-1/2 glass-button px-4 text-xs font-bold h-9"
          >
            {ragLoading ? <RefreshCw size={13} className="animate-spin" /> : 'Search'}
          </button>
        </form>

        {ragError && (
          <div className="mt-4 rounded-xl border border-indigo-500/20 bg-indigo-500/5 p-4 text-sm text-indigo-300">
            {ragError}
          </div>
        )}
        {ragResults?.files && ragResults.files.length > 0 && (
          // Each hit shows its own text. `answer` is only the first hit's text,
          // so it is shown on its own only when the hits carry none.
          <div className="mt-4 space-y-3">
            {ragResults.answer && !ragResults.files.some((f) => f.snippet) && (
              <div className="rounded-xl border border-white/5 bg-black/20 p-4 text-sm leading-relaxed text-slate-300 break-words">
                {ragResults.answer}
              </div>
            )}
            <ul className="grid gap-3 md:grid-cols-2">
              {ragResults.files.map((f, i) => (
                <li key={`${f.path}-${i}`} className="glass-card min-w-0 p-4">
                  <div className="flex min-w-0 items-center gap-2">
                    <File size={15} className="text-blue-400 shrink-0" />
                    <p className="min-w-0 flex-1 truncate text-sm font-semibold text-white">{f.name}</p>
                    {f.collection && (
                      <span className="shrink-0 rounded bg-white/5 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-slate-400">
                        {collectionLabel(f.collection)}
                      </span>
                    )}
                  </div>
                  {f.path && f.path !== f.name && (
                    <p className="mt-1 truncate text-xs text-slate-500">{f.path}</p>
                  )}
                  {f.snippet && (
                    <p className="mt-2 line-clamp-3 break-words text-xs leading-relaxed text-slate-300">{f.snippet}</p>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>

      {/* Stats Grid */}
      <div className="grid gap-6 grid-cols-1 sm:grid-cols-2 lg:grid-cols-3">
        <div className="glass-panel p-6 relative overflow-hidden group">
          <div className="absolute top-0 right-0 p-4 opacity-10 group-hover:opacity-20 transition-opacity">
            <Database size={80} />
          </div>
          <div className="relative z-10">
            <p className="text-sm font-medium text-slate-400">Total Chunks</p>
            <h3 className="text-4xl font-bold text-white mt-2">
              {statsLoading ? '...' : statsError ? '—' : stats?.total_chunks?.toLocaleString() ?? '0'}
            </h3>
            {stats?.breakdown && (
              <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[9px] font-black uppercase tracking-tighter text-slate-500">
                {Object.entries(stats.breakdown).filter(([, s]) => s.chunks > 0).map(([name, s]) => (
                  <span key={name}>{s.chunks.toLocaleString()} {collectionLabel(name)}</span>
                ))}
              </div>
            )}
            <p className="text-xs text-slate-500 mt-4 flex items-center gap-1">
              <Info size={12} />
               Atomic units of knowledge in sqlite-vec
            </p>
          </div>
        </div>

        <div className="glass-panel p-6 relative overflow-hidden group">
          <div className="absolute top-0 right-0 p-4 opacity-10 group-hover:opacity-20 transition-opacity">
            <HardDrive size={80} />
          </div>
          <div className="relative z-10">
            <p className="text-sm font-medium text-slate-400">Documents Ingested</p>
            <h3 className="text-4xl font-bold text-indigo-400 mt-2">
              {statsLoading ? '...' : statsError ? '—' : stats?.total_documents?.toLocaleString() ?? '0'}
            </h3>
            {stats?.breakdown && (
              <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[9px] font-black uppercase tracking-tighter text-indigo-400/50">
                {Object.entries(stats.breakdown).filter(([, s]) => s.documents > 0).map(([name, s]) => (
                  <span key={name}>{s.documents.toLocaleString()} {collectionLabel(name)}</span>
                ))}
              </div>
            )}
            <p className="text-xs text-slate-500 mt-4 flex items-center gap-1">
              <Info size={12} />
              Distinct files, books, devices and lessons
            </p>
          </div>
        </div>

        <div className="glass-panel p-6 relative overflow-hidden group">
          <div className="absolute top-0 right-0 p-4 opacity-10 group-hover:opacity-20 transition-opacity">
            <Clock size={80} />
          </div>
          <div className="relative z-10">
            <p className="text-sm font-medium text-slate-400">Last Index Activity</p>
            <h3 className="text-xl font-bold text-white mt-2">
              {statsLoading
                ? '...'
                : statsError
                  ? '—'
                  : stats?.last_indexed
                    ? new Date(stats.last_indexed).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })
                    : 'Never'}
            </h3>
            <div className="mt-4 flex flex-wrap gap-2">
               {Object.entries(stats?.breakdown ?? {}).filter(([, s]) => s.chunks > 0).map(([name]) => (
                 <span key={name} className="px-2 py-0.5 rounded bg-white/5 border border-white/10 text-[10px] text-slate-400 uppercase font-bold tracking-tighter">
                   {collectionLabel(name)}
                 </span>
               ))}
            </div>
          </div>
        </div>
      </div>

      {/* Manual Ingestion Form */}
      <section className="glass-panel p-6 border-white/5 relative overflow-hidden">
        <div className="absolute top-0 right-0 p-6 opacity-5 pointer-events-none">
          <HardDrive size={100} />
        </div>
        <div className="relative z-10">
          <h3 className="text-lg font-bold text-white mb-2">Direct Ingestion</h3>
          <p className="text-sm text-slate-400 mb-6 max-w-2xl">
            Manually point to a NextCloud path or specific file to ingest it into the RAG system. 
            Useful for paths not currently visible in the explorer.
          </p>
          
          <form onSubmit={handleManualIndex} className="flex flex-col md:flex-row gap-4 items-start md:items-end">
            <div className="flex-1 w-full space-y-2">
              <label htmlFor="path" className="text-[10px] font-black uppercase tracking-widest text-slate-500 ml-1">
                Storage Path
              </label>
              <div className="relative">
                <Folder size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
                <input 
                  id="path"
                  name="path"
                  type="text" 
                  placeholder="/your/folder/path" 
                  className="w-full bg-black/20 border border-white/10 rounded-lg py-2.5 pl-10 pr-4 text-sm text-white focus:outline-none focus:border-indigo-500 transition-colors"
                />
              </div>
            </div>
            
            <div className="flex flex-wrap items-center gap-3">
              <div className="flex min-h-10 pointer-coarse:min-h-11 items-center gap-3 px-4 bg-white/5 rounded-lg border border-white/5">
                <input 
                  id="recursive"
                  name="recursive"
                  type="checkbox" 
                  defaultChecked
                  className="w-4 h-4 rounded border-white/10 bg-black/20 text-indigo-600 focus:ring-indigo-500 focus:ring-offset-black"
                />
                <label htmlFor="recursive" className="text-sm text-slate-300 cursor-pointer">
                  Recursive Ingestion
                </label>
              </div>

              <div className="flex min-h-10 pointer-coarse:min-h-11 items-center gap-3 px-4 bg-white/5 rounded-lg border border-white/5">
                <input 
                  id="force"
                  name="force"
                  type="checkbox" 
                  className="w-4 h-4 rounded border-white/10 bg-black/20 text-amber-600 focus:ring-amber-500 focus:ring-offset-black"
                />
                <label htmlFor="force" className="text-sm text-slate-300 cursor-pointer">
                  Force Reindex
                </label>
              </div>
            </div>

            <button 
              type="submit"
              disabled={indexMutation.isPending}
              className="glass-button w-full md:w-auto px-8 py-2.5 font-bold text-sm flex items-center justify-center gap-2"
            >
              {indexMutation.isPending ? (
                <RefreshCw size={16} className="animate-spin" />
              ) : (
                <Database size={16} />
              )}
              {indexMutation.isPending ? 'Ingesting...' : 'Ingest Path'}
            </button>
          </form>
        </div>
      </section>

      {/* File Explorer */}
      <section className="glass-panel overflow-hidden border-white/5">
        <div className="p-4 sm:p-6 border-b border-white/5 flex flex-wrap items-center justify-between gap-3 bg-white/[0.02]">
          <div className="flex min-w-0 flex-1 items-center gap-2 sm:gap-4">
            <button 
              onClick={handleGoBack}
              disabled={currentPath === '/'}
              aria-label="Up one folder"
              className="flex h-10 w-10 pointer-coarse:h-11 pointer-coarse:w-11 shrink-0 items-center justify-center rounded-lg hover:bg-white/5 disabled:opacity-30 transition-colors"
            >
              <ChevronLeft size={20} />
            </button>
            {/* Deep paths scroll sideways instead of pushing the page wider. */}
            <nav aria-label="Folder path" className="flex min-w-0 items-center gap-1 overflow-x-auto text-sm">
              {breadcrumbs.map((crumb, i) => (
                <div key={crumb.path} className="flex shrink-0 items-center gap-1">
                  {i > 0 && <ChevronRight size={14} className="text-slate-600" />}
                  <button 
                    onClick={() => handleNavigate(crumb.path)}
                    className={`min-h-10 pointer-coarse:min-h-11 px-1.5 whitespace-nowrap hover:text-white transition-colors ${i === breadcrumbs.length - 1 ? 'text-white font-bold' : 'text-slate-400'}`}
                  >
                    {crumb.name}
                  </button>
                </div>
              ))}
            </nav>
          </div>
          <div className="flex w-full items-center gap-3 sm:w-auto">
             {filesFetching && <RefreshCw size={16} className="animate-spin text-indigo-400 shrink-0" />}
             <div className="flex min-w-0 flex-1 items-center gap-3">
                <div className="relative min-w-0 flex-1">
                   <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
                   <input 
                     type="search" 
                     value={fileFilter}
                     onChange={(e) => setFileFilter(e.target.value)}
                     placeholder="Filter this folder..." 
                     aria-label="Filter files in this folder"
                     className="w-full min-h-10 pointer-coarse:min-h-11 bg-black/20 border border-white/5 rounded-lg py-1.5 pl-9 pr-4 text-sm focus:outline-none focus:border-indigo-500/50 transition-colors"
                   />
                </div>
                <div className="flex min-h-10 pointer-coarse:min-h-11 shrink-0 items-center gap-2 px-3 bg-white/5 rounded-lg border border-white/5">
                   <input 
                     id="indexForce"
                     type="checkbox" 
                     checked={indexForce}
                     onChange={(e) => setIndexForce(e.target.checked)}
                     className="w-3.5 h-3.5 rounded border-white/10 bg-black/20 text-amber-600 focus:ring-amber-500 focus:ring-offset-black"
                   />
                   <label htmlFor="indexForce" title="Re-read files even if they were indexed before" className="text-[10px] text-slate-400 cursor-pointer font-bold uppercase tracking-tighter">
                     Force
                   </label>
                </div>
             </div>
           </div>
        </div>

        {/* Size and status get their own columns from sm up; on a phone they
            sit under the name so the table never needs to scroll sideways. */}
        <div className="pb-4">
          <table className="w-full table-fixed text-left border-collapse">
            <thead>
              <tr className="text-[10px] font-black uppercase tracking-widest text-slate-500 border-b border-white/5">
                <th className="px-4 sm:px-6 py-4">Name</th>
                <th className="hidden sm:table-cell w-24 px-6 py-4">Size</th>
                <th className="hidden sm:table-cell w-32 px-6 py-4">Status</th>
                <th className="w-28 sm:w-40 px-4 sm:px-6 py-4 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {filesLoading ? (
                Array.from({ length: 5 }).map((_, i) => (
                  <tr key={i} className="animate-pulse">
                    <td className="px-4 sm:px-6 py-4"><div className="h-4 w-3/4 bg-white/5 rounded" /></td>
                    <td className="hidden sm:table-cell px-6 py-4"><div className="h-4 w-12 bg-white/5 rounded" /></td>
                    <td className="hidden sm:table-cell px-6 py-4"><div className="h-4 w-20 bg-white/5 rounded" /></td>
                    <td className="px-4 sm:px-6 py-4"><div className="h-4 w-16 ml-auto bg-white/5 rounded" /></td>
                  </tr>
                ))
              ) : filesError ? (
                <tr>
                  <td colSpan={4} role="alert" className="px-4 sm:px-6 py-12 text-center text-sm text-red-300">
                    {errorDetail(filesError, 'Could not list this folder.')}
                  </td>
                </tr>
              ) : visibleFiles.length === 0 ? (
                <tr>
                  <td colSpan={4} className="px-4 sm:px-6 py-12 text-center text-slate-500 italic">
                    {files.length === 0 ? 'This folder is empty.' : `Nothing here matches "${fileFilter}".`}
                  </td>
                </tr>
              ) : (
                visibleFiles.map((file) => (
                  <tr key={file.path} className="group hover:bg-white/[0.02] transition-colors">
                    <td className="px-4 sm:px-6 py-3 sm:py-4">
                      <div className="flex min-w-0 items-center gap-3">
                        {file.is_dir ? (
                          <Folder className="text-indigo-400 shrink-0" size={18} />
                        ) : (
                          <File className="text-slate-400 shrink-0" size={18} />
                        )}
                        <div className="min-w-0">
                          <button 
                            onClick={() => file.is_dir && handleNavigate(file.path)}
                            className={`block max-w-full min-h-9 pointer-coarse:min-h-11 text-left text-sm truncate ${file.is_dir ? 'hover:text-indigo-300 font-medium' : 'text-slate-300'}`}
                            disabled={!file.is_dir}
                            title={file.name}
                          >
                            {file.name}
                          </button>
                          <p className="sm:hidden text-[10px] text-slate-500">
                            {file.is_dir ? 'Folder' : formatSize(file.size)}
                            {' · '}
                            <span className={file.indexed ? 'text-emerald-400' : ''}>{file.indexed ? 'Indexed' : 'Not indexed'}</span>
                          </p>
                        </div>
                      </div>
                    </td>
                    <td className="hidden sm:table-cell px-6 py-4 text-xs text-slate-500 font-mono">
                      {file.is_dir ? '--' : formatSize(file.size)}
                    </td>
                    <td className="hidden sm:table-cell px-6 py-4">
                      {file.indexed ? (
                        <div className="flex items-center gap-1.5 text-emerald-400 text-[10px] font-bold uppercase tracking-wider">
                          <CheckCircle2 size={12} />
                          Indexed
                        </div>
                      ) : (
                        <div className="flex items-center gap-1.5 text-slate-600 text-[10px] font-bold uppercase tracking-wider">
                          <div className="w-1.5 h-1.5 rounded-full bg-slate-700" />
                          Unindexed
                        </div>
                      )}
                    </td>
                    <td className="px-4 sm:px-6 py-3 sm:py-4 text-right">
                      {file.is_dir ? (
                        <button
                          onClick={() => indexMutation.mutate({ path: file.path, recursive: true, force: indexForce })}
                          aria-label={`Index folder ${file.name}`}
                          disabled={indexMutation.isPending}
                          className="glass-button min-h-9 pointer-coarse:min-h-11 text-[10px] font-black uppercase tracking-widest py-1.5 px-3 flex items-center gap-2 ml-auto hover:border-indigo-500/50 hover:text-indigo-300 transition-all"
                        >
                          {indexMutation.isPending && indexMutation.variables?.path === file.path ? (
                             <RefreshCw size={12} className="animate-spin" />
                          ) : (
                            <RefreshCw size={12} />
                          )}
                          Index<span className="hidden sm:inline">&nbsp;Folder</span>
                        </button>
                      ) : (
                        <button
                          onClick={() => indexMutation.mutate({ path: file.path, recursive: false, force: indexForce })}
                          aria-label={`Index file ${file.name}`}
                          disabled={indexMutation.isPending}
                          className="glass-button min-h-9 pointer-coarse:min-h-11 text-[10px] font-black uppercase tracking-widest py-1.5 px-3 flex items-center gap-2 ml-auto hover:border-emerald-500/50 hover:text-emerald-300 transition-all"
                        >
                          {indexMutation.isPending && indexMutation.variables?.path === file.path ? (
                            <RefreshCw size={12} className="animate-spin" />
                          ) : (
                            <Database size={12} />
                          )}
                          Index<span className="hidden sm:inline">&nbsp;File</span>
                        </button>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="mt-8 pt-8 border-t border-white/5">
         <div className="flex items-center justify-between gap-4 mb-4">
            <div className="flex items-center gap-2.5">
               <div className="p-2 rounded-xl bg-indigo-500/10 text-indigo-400">
                  <Brain size={18} />
               </div>
               <div>
                  <h3 className="text-base font-bold text-white flex items-center gap-1.5">
                     Raven Lessons
                     <Sparkles size={13} className="text-indigo-400" />
                  </h3>
                  <p className="text-[11px] text-slate-500 leading-tight">
                     Knowledge Raven learned · ♻ = times reused
                  </p>
               </div>
            </div>
            <button
               onClick={() => setLearningSort(learningSort === 'recent' ? 'reuse' : 'recent')}
               className="flex min-h-9 pointer-coarse:min-h-11 shrink-0 items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white/5 border border-white/10 text-xs text-slate-300 hover:bg-white/10 transition-colors"
            >
               <ArrowUpDown size={12} />
               {learningSort === 'reuse' ? 'Most Reused' : 'Newest'}
            </button>
         </div>

         {learningsLoading ? (
            <div className="glass-panel p-6 text-center text-slate-500 text-sm">
               <RefreshCw size={18} className="animate-spin mx-auto mb-2 text-indigo-400" />
               Loading Raven lessons…
            </div>
         ) : learningsError ? (
            // A failed read used to render as "No Raven lessons yet", which says
            // the brain has learned nothing rather than that we could not ask.
            <div className="glass-panel p-6 text-center border-amber-500/30">
               <Brain size={28} className="mx-auto mb-2 text-amber-500/60" />
               <p className="text-amber-200 text-sm font-medium">
                 {errorDetail(learningsErrorObj, 'Could not load Raven lessons')}
               </p>
               <button
                 type="button"
                 onClick={() => void refetchLearnings()}
                 className="glass-button mt-3 inline-flex min-h-10 items-center px-3 text-xs font-semibold pointer-coarse:min-h-11"
               >
                 Try again
               </button>
            </div>
         ) : !learnings || learnings.items.length === 0 ? (
            <div className="glass-panel p-6 text-center border-white/5">
               <Brain size={28} className="mx-auto mb-2 text-slate-700" />
               <p className="text-slate-400 text-sm font-medium">No Raven lessons yet</p>
            </div>
          ) : (
            <div className="flex flex-col gap-2">
               {groupedLearnings.map((group) => {
                  const isOpen = expandedLearning === group.key;
                  return (
                           <div
                              key={group.key}
                              className="glass-panel border-white/5 overflow-hidden"
                           >
                              <button
                                 onClick={() => setExpandedLearning(isOpen ? null : group.key)}
                                 className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-white/5 transition-colors"
                              >
                                 {isOpen ? (
                                    <ChevronDown size={16} className="text-slate-500 shrink-0" />
                                 ) : (
                                    <ChevronRight size={16} className="text-slate-500 shrink-0" />
                                 )}
                                 <div className="min-w-0 flex-1">
                                    <p className="text-sm font-semibold text-white truncate">
                                       {group.topic || 'Untitled lesson'}
                                    </p>
                                    <p className="text-[11px] text-slate-500 mt-0.5">
                                       {group.items.length} lesson{group.items.length > 1 ? 's' : ''}
                                       {group.latestUsed !== 'never' ? ` · last reused ${group.latestUsed}` : ''}
                                    </p>
                                 </div>
                                 <div className="flex items-center gap-2 shrink-0">
                                    {group.topTags.slice(0, 3).map((t) => (
                                       <span
                                          key={t}
                                          className="px-2 py-0.5 rounded bg-indigo-500/10 text-indigo-300 text-[10px] font-medium hidden sm:inline"
                                       >
                                          {t}
                                       </span>
                                    ))}
                                    <span
                                       className={`px-2.5 py-1 rounded-full text-[11px] font-bold ${
                                          group.maxUsage > 0
                                             ? 'bg-emerald-500/15 text-emerald-400'
                                             : 'bg-white/5 text-slate-500'
                                       }`}
                                       title="Total reuse across this topic"
                                    >
                                       ♻ {group.totalUsage}
                                    </span>
                                 </div>
                              </button>

                              {isOpen && (
                                 <div className="border-t border-white/5 divide-y divide-white/5">
                                    {group.items.map((item) => {
                                        const meta = item.metadata || {};
                                        const tags: string[] = Array.isArray(meta.tags) ? meta.tags : [];
                                        const created = item.created_at
                                           ? new Date(item.created_at * 1000).toLocaleDateString()
                                           : '';
                                        const outcome = (item.outcome || (meta.outcome as string) || '').toString().toLowerCase();
                                        const outcomeColor =
                                           outcome === 'failure'
                                              ? 'bg-red-500/15 text-red-300'
                                              : outcome === 'partial'
                                                ? 'bg-amber-500/15 text-amber-300'
                                                : outcome === 'success'
                                                  ? 'bg-emerald-500/15 text-emerald-300'
                                                  : 'bg-slate-500/15 text-slate-400';
                                        const confidence =
                                           typeof item.confidence === 'number'
                                              ? item.confidence
                                              : typeof meta.confidence === 'number'
                                                ? meta.confidence
                                                : null;
                                        return (
                                           <div key={item.id} className="px-4 py-3 sm:pl-9 min-w-0 break-words">
                                              <div className="flex min-h-9 items-center justify-between gap-3 mb-2">
                                                 <p className="text-[11px] text-slate-500">
                                                    Created {created}
                                                 </p>
                                                 {isAdmin && (
                                                 <div className="flex items-center gap-1 shrink-0">
                                                    <button
                                                       onClick={() => {
                                                          setEditLearning({ id: item.id, content: item.content });
                                                          setEditDraft(item.content);
                                                       }}
                                                       className="flex min-h-9 pointer-coarse:min-h-11 items-center gap-1.5 px-2 text-xs text-slate-400 hover:text-indigo-300 transition-colors"
                                                    >
                                                       <Pencil size={13} /> Edit
                                                    </button>
                                                    <button
                                                       onClick={async () => {
                                                          if (await confirmDialog({ title: 'Delete this lesson?', body: 'Delete this lesson? Raven will stop using it.', confirmLabel: 'Delete', danger: true })) {
                                                             deleteLearningMutation.mutate(item.id);
                                                          }
                                                       }}
                                                       disabled={deleteLearningMutation.isPending}
                                                       className="flex min-h-9 pointer-coarse:min-h-11 items-center gap-1.5 px-2 text-xs text-slate-400 hover:text-red-400 transition-colors"
                                                    >
                                                       <Trash2 size={13} /> Delete
                                                    </button>
                                                 </div>
                                                 )}
                                              </div>

                                              {item.rule && (
                                                 <div className="mb-2">
                                                    <p className="text-[10px] uppercase tracking-wide text-indigo-400/80 mb-0.5">Rule</p>
                                                    <p className="text-xs text-slate-200 leading-relaxed whitespace-pre-wrap">{item.rule}</p>
                                                 </div>
                                              )}
                                              {item.root_cause && (
                                                 <div className="mb-2">
                                                    <p className="text-[10px] uppercase tracking-wide text-amber-400/80 mb-0.5">Why / Root cause</p>
                                                    <p className="text-xs text-slate-300 leading-relaxed whitespace-pre-wrap">{item.root_cause}</p>
                                                 </div>
                                              )}

                                              <div className="flex flex-wrap items-center gap-2 mb-2">
                                                 {outcome && (
                                                    <span className={`px-2 py-0.5 rounded text-[10px] font-semibold uppercase ${outcomeColor}`}>
                                                       {outcome}
                                                    </span>
                                                 )}
                                                 {confidence !== null && (
                                                    <span className="px-2 py-0.5 rounded bg-slate-500/10 text-slate-400 text-[10px] font-medium">
                                                       confidence {confidence.toFixed(2)}
                                                    </span>
                                                 )}
                                                 {Array.isArray(item.supersedes) && item.supersedes.length > 0 && (
                                                    <span className="px-2 py-0.5 rounded bg-slate-500/10 text-slate-500 text-[10px] font-medium">
                                                       supersedes {item.supersedes.length}
                                                    </span>
                                                 )}
                                              </div>

                                              {item.content && (
                                                 <p className="text-xs text-slate-400 leading-relaxed whitespace-pre-wrap mb-2">
                                                    {item.content}
                                                 </p>
                                              )}

                                              <p className="text-[11px] text-slate-500">
                                                 ♻ retrieved {item.usage_count}
                                                 <span className="text-emerald-400/80"> · ✓ applied {item.applied_count}</span>
                                              </p>

                                              {tags.length > 0 && (
                                                 <div className="flex flex-wrap gap-1.5 mt-2">
                                                    {tags.map((t) => (
                                                       <span
                                                          key={t}
                                                          className="px-2 py-0.5 rounded bg-indigo-500/10 text-indigo-300 text-[10px] font-medium"
                                                       >
                                                          {t}
                                                       </span>
                                                    ))}
                                                 </div>
                                              )}
                                           </div>
                                        );
                                    })}
                                 </div>
                              )}
                           </div>
                        );
                     })}
                  </div>
          )}
      </section>

      <section className="mt-12 pt-12 border-t border-white/5">
         <div className="flex items-center gap-3 mb-8">
            <div className="p-3 rounded-2xl bg-red-500/10 text-red-400">
               <ShieldAlert size={24} />
            </div>
            <div>
               <h3 className="text-xl font-bold text-white">System Maintenance</h3>
               <p className="text-sm text-slate-500">Rebuild or clear what is indexed for you.</p>
            </div>
         </div>

          <div className="glass-panel p-6 border-amber-500/10 hover:border-amber-500/30 transition-colors group mb-6">
             <div className="flex items-start justify-between mb-4">
                <div className="space-y-1">
                   <h4 className="font-bold text-white">{reindexInfo.heading}</h4>
                   <p className="text-xs text-slate-500">{reindexInfo.blurb}</p>
                </div>
                <RefreshCw className="text-amber-500/40 group-hover:text-amber-400 transition-colors" size={24} />
             </div>
             <div className="flex flex-wrap items-center gap-4 mb-4">
                <div
                  role="group"
                  aria-label="Library to reindex"
                  className="inline-flex rounded-lg border border-white/10 bg-black/20 p-1"
                >
                  {REINDEX_PROVIDERS.map((provider) => (
                    <button
                      key={provider.id}
                      type="button"
                      onClick={() => setReindexProvider(provider.id)}
                      aria-pressed={reindexProvider === provider.id}
                      className={`min-h-9 pointer-coarse:min-h-11 px-4 py-1.5 rounded-md text-xs font-bold uppercase tracking-wider transition-colors ${
                        reindexProvider === provider.id
                          ? 'bg-amber-500/20 text-amber-200'
                          : 'text-slate-400 hover:text-white'
                      }`}
                    >
                      {provider.label}
                    </button>
                  ))}
                </div>
                <div className="flex items-center gap-3">
                <input 
                  id="fullForce"
                  type="checkbox" 
                  checked={fullReindexForce}
                  onChange={(e) => setFullReindexForce(e.target.checked)}
                  className="w-4 h-4 rounded border-white/10 bg-black/20 text-amber-600 focus:ring-amber-500 focus:ring-offset-black"
                />
                <label htmlFor="fullForce" className="text-sm text-slate-300 cursor-pointer">
                  Force bypass checkpoint
                </label>
                </div>
             </div>
             <button 
               onClick={() => fullReindexMutation.mutate({ force: fullReindexForce, provider: reindexProvider })}
               disabled={fullReindexMutation.isPending}
               className="w-full glass-button py-3 text-amber-400 border-amber-500/20 hover:bg-amber-500/10 font-black text-[10px] uppercase tracking-widest flex items-center justify-center gap-2"
             >
               {fullReindexMutation.isPending ? (
                 <RefreshCw size={16} className="animate-spin" />
               ) : (
                 <RefreshCw size={16} />
               )}
               {fullReindexMutation.isPending ? 'Reindexing...' : 'Start Full Reindex'}
             </button>
             {crawl && (crawl.active || crawl.error) && (
               <div
                 role="status"
                 aria-live="polite"
                 className={`mt-4 rounded-xl border p-4 text-sm ${
                   crawl.error && !crawl.active
                     ? 'border-red-500/20 bg-red-500/5 text-red-300'
                     : 'border-amber-500/20 bg-amber-500/5 text-amber-200'
                 }`}
               >
                 {crawl.active ? (
                   <span className="flex items-center gap-2">
                     <RefreshCw size={14} className="animate-spin shrink-0" />
                     <span>
                       Crawling {crawl.kind || 'library'}:{' '}
                       {CRAWL_PHASE_LABELS[crawl.phase ?? ''] || crawl.phase || 'working'}
                       {typeof crawl.total === 'number' && crawl.total > 0
                         ? `: ${crawl.done ?? 0}/${crawl.total}`
                         : ''}
                     </span>
                   </span>
                 ) : (
                   <span>Crawl stopped: {crawl.error}</span>
                 )}
               </div>
             )}
             {crawl && !crawl.active && !crawl.error && typeof crawl.files === 'number' && (
               <p className="mt-3 text-[11px] text-slate-500">
                 Last crawl: {crawl.files} entries → {crawl.chunks ?? 0} chunks ({crawl.synced ?? 0} synced)
               </p>
             )}
          </div>

          <div className="grid gap-6 grid-cols-1 md:grid-cols-2">
             <div className="glass-panel p-6 border-red-500/10 hover:border-red-500/30 transition-colors group">
                <div className="flex items-start justify-between mb-6">
                   <div className="space-y-1">
                      <h4 className="font-bold text-white">Clear Nextcloud Collection</h4>
                      <p className="text-xs text-slate-500">Delete your indexed Nextcloud file chunks from semantic memory. The files themselves are untouched.</p>
                   </div>
                   <AlertTriangle className="text-red-500/40 group-hover:text-red-500 transition-colors" size={24} />
                </div>
                <button 
                  onClick={() => setPurgeModalCollection('nextcloud_files')}
                  className="w-full glass-button py-3 text-red-400 border-red-500/20 hover:bg-red-500/10 font-black text-[10px] uppercase tracking-widest"
                >
                   Purge Nextcloud Data
                </button>
             </div>

             <div className="glass-panel p-6 border-red-500/10 hover:border-red-500/30 transition-colors group">
                <div className="flex items-start justify-between mb-6">
                   <div className="space-y-1">
                      <h4 className="font-bold text-white">Clear Home Assistant Devices</h4>
                      <p className="text-xs text-slate-500">Remove your Home Assistant devices from semantic memory. The background sync re-adds them from Home Assistant within minutes.</p>
                   </div>
                   <AlertTriangle className="text-red-500/40 group-hover:text-red-500 transition-colors" size={24} />
                </div>
                <button 
                  onClick={() => setPurgeModalCollection('ha_entities')}
                  className="w-full glass-button py-3 text-red-400 border-red-500/20 hover:bg-red-500/10 font-black text-[10px] uppercase tracking-widest"
                >
                   Purge HA Entities
                </button>
             </div>

             <div className="glass-panel p-6 border-red-500/10 hover:border-red-500/30 transition-colors group">
                <div className="flex items-start justify-between mb-6">
                   <div className="space-y-1">
                      <h4 className="font-bold text-white">Clear Calibre Collection</h4>
                      <p className="text-xs text-slate-500">Remove every indexed book chunk from the calibre_files collection. Books stay on the shelf and can be re-crawled.</p>
                   </div>
                   <AlertTriangle className="text-red-500/40 group-hover:text-red-500 transition-colors" size={24} />
                </div>
                <button 
                  onClick={() => setPurgeModalCollection('calibre_files')}
                  className="w-full glass-button py-3 text-red-400 border-red-500/20 hover:bg-red-500/10 font-black text-[10px] uppercase tracking-widest"
                >
                   Purge Calibre Data
                </button>
             </div>
          </div>
       </section>

       <Modal
        isOpen={Boolean(purgeModalCollection)}
        onClose={() => setPurgeModalCollection(null)}
        title={`Purge ${collectionLabel(purgeModalCollection ?? undefined)} from memory?`}
      >
        <div className="space-y-6">
           <div className="p-4 bg-red-500/10 border border-red-500/20 rounded-xl flex gap-4">
              <AlertTriangle className="text-red-500 shrink-0" size={24} />
              <div className="text-xs text-slate-300 leading-relaxed">
                This removes your entries in <span className="font-mono text-red-400 font-bold break-all">{purgeModalCollection}</span> from
                the knowledge index. Jarvis cannot answer from them until they are indexed again. It cannot be undone.
              </div>
           </div>

           <div className="flex gap-3">
              <button 
                onClick={() => setPurgeModalCollection(null)}
                className="glass-button flex-1 py-3 font-bold text-[10px] uppercase tracking-widest"
              >
                Cancel
              </button>
              <button 
                onClick={() => purgeMutation.mutate(purgeModalCollection!)}
                disabled={purgeMutation.isPending}
                className="glass-button flex-1 py-3 bg-red-600/30 border-red-500/30 text-red-400 font-bold text-[10px] uppercase tracking-widest"
              >
                {purgeMutation.isPending ? 'Purging...' : 'Confirm Purge'}
              </button>
           </div>
        </div>
      </Modal>

      <Modal
        isOpen={Boolean(editLearning)}
        onClose={() => setEditLearning(null)}
        title="Edit Raven Lesson"
      >
        <div className="space-y-5">
          <textarea
            value={editDraft}
            onChange={(e) => setEditDraft(e.target.value)}
            aria-label="Lesson text"
            rows={12}
            className="w-full bg-black/20 border border-white/10 rounded-lg p-3 text-sm text-white font-mono focus:outline-none focus:border-indigo-500 transition-colors resize-y"
          />
          <div className="flex gap-3">
            <button
              onClick={() => setEditLearning(null)}
              className="glass-button flex-1 py-3 font-bold text-[10px] uppercase tracking-widest"
            >
              Cancel
            </button>
            <button
              onClick={() => {
                if (!editLearning) return;
                setEditSaving(true);
                saveLearningMutation.mutate(
                  { id: editLearning.id, content: editDraft },
                  { onSettled: () => setEditSaving(false) },
                );
              }}
              disabled={editSaving || !editDraft.trim()}
              className="glass-button flex-1 py-3 bg-indigo-600/30 border-indigo-500/30 text-indigo-300 font-bold text-[10px] uppercase tracking-widest"
            >
              {editSaving ? 'Saving…' : 'Save Lesson'}
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
};

export default KnowledgeHub;
