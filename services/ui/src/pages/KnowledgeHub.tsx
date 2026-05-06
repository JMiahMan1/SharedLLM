import { useState, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Database,
  Folder,
  File,
  ChevronRight,
  ChevronLeft,
  Search,
  RefreshCw,
  HardDrive,
  Info,
  CheckCircle2,
  Clock,
  ArrowRight,
  Trash2,
  AlertTriangle,
} from 'lucide-react';
import toast from 'react-hot-toast';
import { api, type StorageEntry, type RagStats } from '../services/api';
import Modal from '../components/ui/Modal';

const KnowledgeHub = () => {
  const [currentPath, setCurrentPath] = useState('/');
  const [purgeCollection, setPurgeCollection] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const { data: stats, isLoading: statsLoading } = useQuery<RagStats>({
    queryKey: ['rag-stats'],
    queryFn: () => api.getRagStats(),
    refetchInterval: 10000,
  });

  const { data: files = [], isLoading: filesLoading, isFetching: filesFetching } = useQuery<StorageEntry[]>({
    queryKey: ['storage-files', currentPath],
    queryFn: () => api.getStorageFiles(currentPath),
  });

  const indexMutation = useMutation({
    mutationFn: ({ path, recursive = true }: { path: string; recursive?: boolean }) => 
      api.triggerIndexing(path, recursive),
    onSuccess: () => {
      toast.success('Indexing started in background');
      queryClient.invalidateQueries({ queryKey: ['rag-stats'] });
    },
    onError: (err: Error) => {
      toast.error(err.message || 'Failed to start indexing');
    },
  });

  const purgeMutation = useMutation({
    mutationFn: (collectionName: string) => api.purgeRagCollection(collectionName, 'me'),
    onSuccess: () => {
      toast.success('Collection purged');
      setPurgeCollection(null);
      queryClient.invalidateQueries({ queryKey: ['rag-stats'] });
    },
    onError: (err: Error) => {
      toast.error(err.message || 'Failed to purge collection');
    },
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

  const handleIndexFolder = (path: string) => {
    indexMutation.mutate({ path, recursive: true });
  };

  const handleManualIndex = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const formData = new FormData(e.currentTarget);
    const path = (formData.get('path') as string).trim();
    const recursive = formData.get('recursive') === 'on';
    
    if (path) {
      indexMutation.mutate({ path, recursive });
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

      {/* Stats Grid */}
      <div className="grid gap-6 md:grid-cols-3">
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
              Atomic units of knowledge in ChromaDB
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

      {/* Danger Zone / Maintenance */}
      <section className="glass-panel p-6 border-red-500/10 bg-red-500/5">
        <div className="flex items-center gap-3 mb-4">
           <AlertTriangle className="text-red-400" size={20} />
           <h3 className="text-lg font-bold text-white">Maintenance Operations</h3>
        </div>
        <p className="text-sm text-slate-400 mb-6">
          Irreversibly clear indexed knowledge from the vector database. This does not affect your original files.
        </p>
        <div className="flex flex-wrap gap-4">
           {stats?.breakdown && Object.keys(stats.breakdown).map(name => (
             <button
                key={name}
                onClick={() => setPurgeCollection(name)}
                className="glass-button border-red-500/20 text-red-300 hover:bg-red-500/10 px-4 py-2 text-[10px] font-black uppercase tracking-widest flex items-center gap-2"
             >
                <Trash2 size={14} />
                Clear {name.replace('_', ' ')}
             </button>
           ))}
           <button
              onClick={() => setPurgeCollection('all')}
              className="glass-button border-red-500/40 bg-red-500/10 text-red-200 hover:bg-red-500/20 px-4 py-2 text-[10px] font-black uppercase tracking-widest flex items-center gap-2"
           >
              <Trash2 size={14} />
              Purge Entire Hub
           </button>
        </div>
      </section>

      <Modal
        isOpen={Boolean(purgeCollection)}
        onClose={() => setPurgeCollection(null)}
        title="Confirm Permanent Deletion"
      >
        <div className="space-y-6">
          <div className="flex items-center gap-4 p-4 rounded-2xl bg-red-500/10 border border-red-500/20">
            <AlertTriangle className="text-red-400 shrink-0" size={24} />
            <p className="text-sm text-red-200">
              You are about to delete all vector embeddings for <strong>{purgeCollection === 'all' ? 'the entire Knowledge Hub' : purgeCollection}</strong>. 
              This action is irreversible and the system will lose all memory associated with this data until it is re-indexed.
            </p>
          </div>
          <p className="text-sm text-slate-400">
            Are you absolutely sure you want to proceed?
          </p>
          <div className="flex gap-3">
            <button
              onClick={() => setPurgeCollection(null)}
              className="glass-button px-4 py-3 text-[10px] font-black uppercase tracking-widest"
            >
              Cancel
            </button>
            <button
              onClick={() => purgeMutation.mutate(purgeCollection!)}
              disabled={purgeMutation.isPending}
              className="glass-button flex-1 px-4 py-3 bg-red-600/30 border-red-500/30 text-[10px] font-black uppercase tracking-widest text-red-300"
            >
              {purgeMutation.isPending ? 'Purging...' : 'Yes, Delete Knowledge'}
            </button>
          </div>
        </div>
      </Modal>

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
            
            <div className="flex items-center gap-3 py-2.5 px-4 bg-white/5 rounded-lg border border-white/5">
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
             <div className="relative">
                <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
                <input 
                  type="text" 
                  placeholder="Filter files..." 
                  className="bg-black/20 border border-white/5 rounded-lg py-1.5 pl-9 pr-4 text-sm focus:outline-none focus:border-indigo-500/50 transition-colors"
                />
             </div>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
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
                          onClick={() => handleIndexFolder(file.path)}
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
                        <button className="text-slate-600 hover:text-white transition-colors">
                          <ArrowRight size={14} />
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
    </div>
  );
};

export default KnowledgeHub;
