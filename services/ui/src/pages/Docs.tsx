import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { FileText, Info, Search, ChevronRight, RefreshCw } from 'lucide-react';
import MarkdownViewer from '../components/MarkdownViewer';
import { api } from '../services/api';

/**
 * The project's documentation, read from the server (docs/*.md + README).
 *
 * The list comes from the gateway rather than a hard-coded menu: three of the
 * five hard-coded entries pointed at files that did not exist and only ever
 * showed "Documentation Unavailable".
 */
const Docs = () => {
  const [selected, setSelected] = useState<string | null>(null);
  const [searchTerm, setSearchTerm] = useState('');

  const docsList = useQuery({
    queryKey: ['docs-list'],
    queryFn: () => api.listDocs(),
    staleTime: 1000 * 60 * 10,
  });
  const docs = useMemo(() => docsList.data ?? [], [docsList.data]);
  const current = selected ?? docs[0]?.name ?? null;

  const doc = useQuery({
    queryKey: ['docs', current],
    queryFn: () => api.getDoc(current as string),
    enabled: !!current,
    staleTime: 1000 * 60 * 10,
  });

  const filteredDocs = useMemo(() => {
    const q = searchTerm.trim().toLowerCase();
    return q ? docs.filter((d) => `${d.title} ${d.name}`.toLowerCase().includes(q)) : docs;
  }, [docs, searchTerm]);

  const failed = docsList.isError || doc.isError;

  return (
    <div className="h-full flex flex-col gap-4 md:gap-6">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Docs</h1>
          <p className="text-slate-400 text-sm">How Jarvis works: services, features and setup.</p>
        </div>

        <div className="relative w-full md:w-64">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
          <input
            type="search"
            placeholder="Find a document…"
            aria-label="Find a document"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="glass-input w-full pl-10 pr-4 min-h-11 text-sm"
          />
        </div>
      </div>

      {/* Phones: a picker instead of a sidebar that would squeeze the text. */}
      <select
        className="md:hidden glass-input w-full min-h-11 text-sm"
        aria-label="Document"
        value={current ?? ''}
        onChange={(e) => setSelected(e.target.value)}
      >
        {filteredDocs.map((d) => (
          <option key={d.name} value={d.name}>{d.title}</option>
        ))}
      </select>

      <div className="flex-1 flex gap-6 min-h-0">
        <nav className="hidden md:flex w-64 shrink-0 flex-col gap-1 overflow-y-auto pr-2 custom-scrollbar" aria-label="Documents">
          {docsList.isLoading && <p className="text-xs text-slate-500 px-4 py-3">Loading…</p>}
          {!docsList.isLoading && filteredDocs.length === 0 && (
            <p className="text-xs text-slate-500 px-4 py-3">No documents match.</p>
          )}
          {filteredDocs.map((d) => {
            const active = d.name === current;
            return (
              <button
                key={d.name}
                onClick={() => setSelected(d.name)}
                className={`flex items-center gap-3 px-4 py-2.5 rounded-xl transition-colors text-left ${
                  active
                    ? 'bg-indigo-600/20 text-indigo-300 border border-indigo-500/30'
                    : 'text-slate-400 hover:text-white hover:bg-white/5 border border-transparent'
                }`}
              >
                <FileText className={`w-4 h-4 shrink-0 ${active ? 'text-indigo-400' : 'text-slate-500'}`} />
                <span className="flex-1 min-w-0 truncate text-sm font-medium" title={d.title}>{d.title}</span>
                {active && <ChevronRight className="w-4 h-4 shrink-0" />}
              </button>
            );
          })}
        </nav>

        <div className="flex-1 min-w-0 glass-panel overflow-y-auto p-4 md:p-8 relative custom-scrollbar">
          {failed ? (
            <div className="flex flex-col items-center justify-center h-full gap-4 text-center p-4">
              <div className="w-16 h-16 bg-red-500/10 rounded-2xl flex items-center justify-center">
                <Info className="w-8 h-8 text-red-400" />
              </div>
              <h3 className="text-lg font-bold text-white">Documentation unavailable</h3>
              <p className="text-slate-400 max-w-md text-sm">The server could not be reached, or this document is missing.</p>
              <button
                onClick={() => { void docsList.refetch(); void doc.refetch(); }}
                className="glass-button min-h-11 px-6"
              >
                <RefreshCw size={14} /> Try again
              </button>
            </div>
          ) : docsList.isLoading || doc.isLoading ? (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-4">
              <div className="w-12 h-12 border-4 border-indigo-500/20 border-t-indigo-500 rounded-full animate-spin" />
              <p className="text-slate-400 font-medium">Loading…</p>
            </div>
          ) : (
            <MarkdownViewer markdown={doc.data || ''} />
          )}
        </div>
      </div>
    </div>
  );
};

export default Docs;
