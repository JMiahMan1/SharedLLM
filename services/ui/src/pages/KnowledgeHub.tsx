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
import { api, type StorageEntry, type RagStats } from '../services/api';
import { useAuth } from '../context/AuthContext';
import Modal from '../components/ui/Modal';

const KnowledgeHub = () => {
  const [currentPath, setCurrentPath] = useState('/');
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const [purgeModalCollection, setPurgeModalCollection] = useState<string | null>(null);
  const [fullReindexForce, setFullReindexForce] = useState(false);
  const [indexForce, setIndexForce] = useState(false);

  const [ragQuery, setRagQuery] = useState('');
  const [ragResults, setRagResults] = useState<{ answer?: string; files?: { name: string; path: string }[] } | null>(null);
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

  const { data: stats, isLoading: statsLoading } = useQuery<RagStats>({
    queryKey: ['rag-stats'],
    queryFn: () => api.getRagStats(),
    refetchInterval: 10000,
  });

  const { data: files = [], isLoading: filesLoading, isFetching: filesFetching } = useQuery<StorageEntry[]>({
    queryKey: ['storage-files', currentPath],
    queryFn: () => api.getStorageFiles(currentPath),
  });

  const {
    data: learnings,
    isLoading: learningsLoading,
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
    onError: () => toast.error('Failed to delete lesson'),
  });

  const saveLearningMutation = useMutation({
    mutationFn: (payload: { id: string; content: string }) =>
      api.editRavenLearning(payload.id, { content: payload.content }),
    onSuccess: () => {
      toast.success('Lesson updated');
      setEditLearning(null);
      queryClient.invalidateQueries({ queryKey: ['raven-learnings'] });
    },
    onError: () => toast.error('Failed to update lesson'),
  });

  const indexMutation = useMutation({
    mutationFn: ({ path, recursive = true, force = false }: { path: string; recursive?: boolean; force?: boolean }) => 
      force ? api.triggerIndexingForce(path, recursive) : api.triggerIndexing(path, recursive),
    onSuccess: () => {
      toast.success('Indexing started in background');
      queryClient.invalidateQueries({ queryKey: ['rag-stats'] });
    },
    onError: (err: Error) => {
      toast.error(err.message || 'Failed to start indexing');
    },
  });

  const fullReindexMutation = useMutation({
    mutationFn: ({ force = false }: { force?: boolean }) => 
      api.triggerFullIndex(
        { kind: 'nextcloud', settings: {} },
        { force, user_id: user?.username }
      ),
    onSuccess: () => {
      toast.success('Full NextCloud reindex started in background');
      queryClient.invalidateQueries({ queryKey: ['rag-stats'] });
    },
    onError: (err: Error) => {
      toast.error(err.message || 'Failed to start full reindex');
    },
  });

  const purgeMutation = useMutation({
    mutationFn: (collectionName: string) => api.purgeRagCollection(collectionName, user?.username || 'default'),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['rag-stats'] });
      setPurgeModalCollection(null);
      toast.success('Collection purged successfully');
    },
    onError: (err: Error) => toast.error(err.message || 'Purge failed'),
  });

  const breadcrumbs = useMemo(() => {
    const parts = currentPath.split('/').filter(Boolean);
    return [{ name: 'Root', path: '/' }, ...parts.map((part, i) => ({
      name: part,
      path: '/' + parts.slice(0, i + 1).join('/')
    }))];
  }, [currentPath]);

  const handleNavigate = (path: string) => {
    setCurrentPath(path);
  };

  const handleGoBack = () => {
    if (currentPath === '/') return;
    const parts = currentPath.split('/').filter(Boolean);
    parts.pop();
    setCurrentPath('/' + parts.join('/'));
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
        <div>
          <h2 className="text-3xl font-bold tracking-tight text-white">Knowledge Hub</h2>
          <p className="mt-1 text-slate-400">
            Manage your LLM's brain. Index files and folders from NextCloud into the RAG system.
          </p>
        </div>
        <div className="flex gap-4">
           <div className="glass-card flex items-center gap-3 px-6 py-3">
              <Database className="text-indigo-400" size={20} />
              <div>
                <p className="text-[10px] font-black uppercase tracking-widest text-slate-500 leading-none">RAG Status</p>
                <p className="text-sm font-bold text-white mt-1">Live & Active</p>
              </div>
           </div>
        </div>
      </header>

      {/* RAG Semantic Search — the home for semantic memory / indexed storage */}
      <section className="glass-panel p-6 border-indigo-500/20 bg-indigo-950/5">
        <div className="flex items-center gap-2 mb-4 flex-wrap">
          <Search size={16} className="text-indigo-300" />
          <h3 className="font-bold text-indigo-300">Semantic Memory Search</h3>
          <span className="text-[10px] uppercase tracking-widest text-slate-500">
            RAG · indexed files, Home Assistant, missions &amp; more
          </span>
        </div>
        <form onSubmit={runRagSearch} className="relative">
          <Search className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-500" size={16} />
          <input
            type="text"
            value={ragQuery}
            onChange={(e) => setRagQuery(e.target.value)}
            placeholder="Ask the brain — search indexed files, Home Assistant devices, missions…"
            className="glass-input w-full py-3 pl-10 pr-28 text-sm"
            aria-label="Semantic memory search"
          />
          <button
            type="submit"
            disabled={ragLoading}
            className="absolute right-2 top-1/2 -translate-y-1/2 glass-button px-4 py-1.5 text-xs font-bold h-8"
          >
            {ragLoading ? <RefreshCw size={13} className="animate-spin" /> : 'Search'}
          </button>
        </form>

        {ragError && (
          <div className="mt-4 rounded-xl border border-indigo-500/20 bg-indigo-500/5 p-4 text-sm text-indigo-300">
            {ragError}
          </div>
        )}
        {ragResults && (
          <div className="mt-4 space-y-4">
            {ragResults.answer && (
              <div className="rounded-xl border border-white/5 bg-black/20 p-4 text-sm leading-relaxed text-slate-300">
                {ragResults.answer}
              </div>
            )}
            {ragResults.files && ragResults.files.length > 0 && (
              <div className="grid gap-3 md:grid-cols-2">
                {ragResults.files.map((f) => (
                  <div key={f.path} className="glass-card flex items-center gap-3 p-4">
                    <File size={15} className="text-blue-400 shrink-0" />
                    <div className="min-w-0">
                      <p className="truncate text-sm font-semibold text-white">{f.name}</p>
                      <p className="truncate text-xs text-slate-500">{f.path}</p>
                    </div>
                  </div>
                ))}
              </div>
            )}
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
              {statsLoading ? '...' : stats?.total_chunks?.toLocaleString() ?? '0'}
            </h3>
            {stats?.breakdown && (
              <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[9px] font-black uppercase tracking-tighter text-slate-500">
                {Object.entries(stats.breakdown).map(([name, s]) => (
                  <span key={name}>{s.chunks.toLocaleString()} {name.split('_')[0]}</span>
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
              {statsLoading ? '...' : stats?.total_documents?.toLocaleString() ?? '0'}
            </h3>
            {stats?.breakdown && (
              <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[9px] font-black uppercase tracking-tighter text-indigo-400/50">
                {Object.entries(stats.breakdown).map(([name, s]) => (
                  <span key={name}>{s.documents.toLocaleString()} {name.split('_')[0]}</span>
                ))}
              </div>
            )}
            <p className="text-xs text-slate-500 mt-4 flex items-center gap-1">
              <CheckCircle2 size={12} className="text-emerald-500" />
              Verified across all providers
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
              {statsLoading ? '...' : (stats?.last_indexed ? new Date(stats.last_indexed).toLocaleTimeString() : 'Never')}
            </h3>
            <div className="mt-4 flex flex-wrap gap-2">
               {stats?.providers?.map(p => (
                 <span key={p} className="px-2 py-0.5 rounded bg-white/5 border border-white/10 text-[10px] text-slate-400 uppercase font-bold tracking-tighter">
                   {p}
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
            
            <div className="flex items-center gap-6 py-2.5">
              <div className="flex items-center gap-3 px-4 bg-white/5 rounded-lg border border-white/5">
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

              <div className="flex items-center gap-3 px-4 bg-white/5 rounded-lg border border-white/5">
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
              {indexMutation.isPending && !indexMutation.variables?.path.startsWith('/') ? (
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
        <div className="p-6 border-b border-white/5 flex items-center justify-between bg-white/[0.02]">
          <div className="flex items-center gap-4">
            <button 
              onClick={handleGoBack}
              disabled={currentPath === '/'}
              className="p-2 rounded-lg hover:bg-white/5 disabled:opacity-30 transition-colors"
            >
              <ChevronLeft size={20} />
            </button>
            <nav className="flex items-center gap-2 text-sm">
              {breadcrumbs.map((crumb, i) => (
                <div key={crumb.path} className="flex items-center gap-2">
                  {i > 0 && <ChevronRight size={14} className="text-slate-600" />}
                  <button 
                    onClick={() => handleNavigate(crumb.path)}
                    className={`hover:text-white transition-colors ${i === breadcrumbs.length - 1 ? 'text-white font-bold' : 'text-slate-400'}`}
                  >
                    {crumb.name}
                  </button>
                </div>
              ))}
            </nav>
          </div>
          <div className="flex items-center gap-3">
             {filesFetching && <RefreshCw size={16} className="animate-spin text-indigo-400" />}
             <div className="flex items-center gap-4">
                <div className="relative">
                   <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
                   <input 
                     type="text" 
                     placeholder="Filter files..." 
                     className="bg-black/20 border border-white/5 rounded-lg py-1.5 pl-9 pr-4 text-sm focus:outline-none focus:border-indigo-500/50 transition-colors"
                   />
                </div>
                <div className="flex items-center gap-2 px-3 py-1.5 bg-white/5 rounded-lg border border-white/5">
                   <input 
                     id="indexForce"
                     type="checkbox" 
                     checked={indexForce}
                     onChange={(e) => setIndexForce(e.target.checked)}
                     className="w-3.5 h-3.5 rounded border-white/10 bg-black/20 text-amber-600 focus:ring-amber-500 focus:ring-offset-black"
                   />
                   <label htmlFor="indexForce" className="text-[10px] text-slate-400 cursor-pointer font-bold uppercase tracking-tighter">
                     Force
                   </label>
                </div>
             </div>
           </div>
        </div>

        <div className="overflow-x-auto pb-4">
          <table className="w-full min-w-[800px] text-left border-collapse">
            <thead>
              <tr className="text-[10px] font-black uppercase tracking-widest text-slate-500 border-b border-white/5">
                <th className="px-6 py-4">Name</th>
                <th className="px-6 py-4">Size</th>
                <th className="px-6 py-4">Status</th>
                <th className="px-6 py-4 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {filesLoading ? (
                Array.from({ length: 5 }).map((_, i) => (
                  <tr key={i} className="animate-pulse">
                    <td className="px-6 py-4"><div className="h-4 w-48 bg-white/5 rounded" /></td>
                    <td className="px-6 py-4"><div className="h-4 w-12 bg-white/5 rounded" /></td>
                    <td className="px-6 py-4"><div className="h-4 w-20 bg-white/5 rounded" /></td>
                    <td className="px-6 py-4"><div className="h-4 w-24 ml-auto bg-white/5 rounded" /></td>
                  </tr>
                ))
              ) : files.length === 0 ? (
                <tr>
                  <td colSpan={4} className="px-6 py-12 text-center text-slate-500 italic">
                    This directory is empty or unreachable.
                  </td>
                </tr>
              ) : (
                files.map((file) => (
                  <tr key={file.path} className="group hover:bg-white/[0.02] transition-colors">
                    <td className="px-6 py-4">
                      <div className="flex items-center gap-3">
                        {file.is_dir ? (
                          <Folder className="text-indigo-400 shrink-0" size={18} />
                        ) : (
                          <File className="text-slate-400 shrink-0" size={18} />
                        )}
                        <button 
                          onClick={() => file.is_dir && handleNavigate(file.path)}
                          className={`text-sm truncate ${file.is_dir ? 'hover:text-indigo-300 font-medium' : 'text-slate-300'}`}
                          disabled={!file.is_dir}
                        >
                          {file.name}
                        </button>
                      </div>
                    </td>
                    <td className="px-6 py-4 text-xs text-slate-500 font-mono">
                      {file.is_dir ? '--' : (file.size ? `${(file.size / 1024).toFixed(1)} KB` : '0')}
                    </td>
                    <td className="px-6 py-4">
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
                    <td className="px-6 py-4 text-right">
                      {file.is_dir ? (
                        <button
                          onClick={() => indexMutation.mutate({ path: file.path, recursive: true, force: indexForce })}
                          disabled={indexMutation.isPending}
                          className="glass-button text-[10px] font-black uppercase tracking-widest py-1.5 px-3 flex items-center gap-2 ml-auto hover:border-indigo-500/50 hover:text-indigo-300 transition-all"
                        >
                          {indexMutation.isPending && indexMutation.variables?.path === file.path ? (
                             <RefreshCw size={12} className="animate-spin" />
                          ) : (
                            <RefreshCw size={12} />
                          )}
                          Index Folder
                        </button>
                      ) : (
                        <button
                          onClick={() => indexMutation.mutate({ path: file.path, recursive: false, force: indexForce })}
                          disabled={indexMutation.isPending}
                          className="glass-button text-[10px] font-black uppercase tracking-widest py-1.5 px-3 flex items-center gap-2 ml-auto hover:border-emerald-500/50 hover:text-emerald-300 transition-all"
                        >
                          {indexMutation.isPending && indexMutation.variables?.path === file.path ? (
                            <RefreshCw size={12} className="animate-spin" />
                          ) : (
                            <Database size={12} />
                          )}
                          Index File
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
               className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white/5 border border-white/10 text-xs text-slate-300 hover:bg-white/10 transition-colors"
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
                                           <div key={item.id} className="px-4 py-3 pl-9">
                                              <div className="flex items-center justify-between gap-3 mb-2">
                                                 <p className="text-[11px] text-slate-500">
                                                    Created {created}
                                                 </p>
                                                 <div className="flex items-center gap-3 shrink-0">
                                                    <button
                                                       onClick={() => {
                                                          setEditLearning({ id: item.id, content: item.content });
                                                          setEditDraft(item.content);
                                                       }}
                                                       className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-indigo-300 transition-colors"
                                                    >
                                                       <Pencil size={13} /> Edit
                                                    </button>
                                                    <button
                                                       onClick={() => deleteLearningMutation.mutate(item.id)}
                                                       disabled={deleteLearningMutation.isPending}
                                                       className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-red-400 transition-colors"
                                                    >
                                                       <Trash2 size={13} /> Delete
                                                    </button>
                                                 </div>
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
               <p className="text-sm text-slate-500">Privileged operations for managing indexed knowledge collections.</p>
            </div>
         </div>

          <div className="glass-panel p-6 border-amber-500/10 hover:border-amber-500/30 transition-colors group mb-6">
             <div className="flex items-start justify-between mb-4">
                <div className="space-y-1">
                   <h4 className="font-bold text-white">Full NextCloud Reindex</h4>
                   <p className="text-xs text-slate-500">Bypass checkpoint and re-process all files from NextCloud. Clears existing data before re-indexing.</p>
                </div>
                <RefreshCw className="text-amber-500/40 group-hover:text-amber-400 transition-colors" size={24} />
             </div>
             <div className="flex items-center gap-3 mb-4">
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
             <button 
               onClick={() => fullReindexMutation.mutate({ force: fullReindexForce })}
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
          </div>

          <div className="grid gap-6 grid-cols-1 md:grid-cols-2">
             <div className="glass-panel p-6 border-red-500/10 hover:border-red-500/30 transition-colors group">
                <div className="flex items-start justify-between mb-6">
                   <div className="space-y-1">
                      <h4 className="font-bold text-white">Clear Nextcloud Collection</h4>
                      <p className="text-xs text-slate-500">Permanently delete all indexed file chunks from Nextcloud storage.</p>
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
                      <h4 className="font-bold text-white">Clear Home Assistant Collection</h4>
                      <p className="text-xs text-slate-500">Remove all device states and automation history from semantic memory.</p>
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
          </div>
       </section>

       <Modal
        isOpen={Boolean(purgeModalCollection)}
        onClose={() => setPurgeModalCollection(null)}
        title="Critical Security Warning"
      >
        <div className="space-y-6">
           <div className="p-4 bg-red-500/10 border border-red-500/20 rounded-xl flex gap-4">
              <AlertTriangle className="text-red-500 shrink-0" size={24} />
              <div className="text-xs text-slate-300 leading-relaxed">
                <p className="font-bold text-white mb-1">Irreversible Action Detected</p>
                You are about to purge the <span className="font-mono text-red-400 font-bold">{purgeModalCollection}</span> collection. 
                This will remove all associated vectors from the ChromaDB instance. 
                Jarvis will lose all context regarding these resources until they are re-indexed.
              </div>
           </div>

           <p className="text-sm text-slate-400">
             Are you absolutely sure you want to proceed? This operation cannot be undone.
           </p>

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
