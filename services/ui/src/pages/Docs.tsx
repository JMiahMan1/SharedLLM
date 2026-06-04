import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import axios from 'axios';
import { useAuth } from '../context/AuthContext';
import MarkdownViewer from '../components/MarkdownViewer';
import { 
  Book, 
  Terminal, 
  Layers, 
  Cpu,
  Info,
  Search,
  ChevronRight,
  ExternalLink
} from 'lucide-react';
import toast from 'react-hot-toast';

const DOC_ITEMS = [
  { id: 'api_reference', label: 'API Reference', icon: Terminal, file: 'api_reference.md' },
  { id: 'integrations', label: 'Integration Setup', icon: Layers, file: 'integrations.md' },
  { id: 'architecture', label: 'System Architecture', icon: Cpu, file: 'architecture.md' },
  { id: 'workspace_runtime', label: 'Workspace Runtime', icon: Book, file: 'workspace_runtime.md' },
  { id: 'README', label: 'Project Overview', icon: Info, file: 'README.md' },
];

const Docs = () => {
  const { token } = useAuth();
  const [selectedDoc, setSelectedDoc] = useState(DOC_ITEMS[0]);
  const [searchTerm, setSearchTerm] = useState('');

  const { data: docContent, isLoading, error } = useQuery({
    queryKey: ['docs', selectedDoc.id],
    queryFn: async () => {
      const response = await axios.get(`/api/docs/${selectedDoc.file}`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      return response.data.content;
    },
    staleTime: 1000 * 60 * 60 * 24, // 24 hours
  });

  const handleTestExample = (prompt: string) => {
    // Store in session and redirect or use a callback
    sessionStorage.setItem('pending_prompt', prompt);
    toast.success(`Prompt "${prompt}" copied to Laboratory!`);
    window.location.href = '/lab';
  };

  const filteredDocs = DOC_ITEMS.filter(doc => 
    doc.label.toLowerCase().includes(searchTerm.toLowerCase())
  );

  return (
    <div className="h-full flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Developer & Help Hub</h1>
          <p className="text-slate-400 text-sm">System documentation and API reference for the Jarvis OS ecosystem.</p>
        </div>
        
        <div className="relative group">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500 group-focus-within:text-indigo-400 transition-colors" />
          <input
            type="text"
            placeholder="Search documentation..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="pl-10 pr-4 py-2 bg-slate-900/50 border border-white/10 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/50 transition-all w-64"
          />
        </div>
      </div>

      <div className="flex-1 flex gap-6 overflow-hidden">
        {/* Sidebar Navigation */}
        <div className="w-64 flex flex-col gap-2 overflow-y-auto pr-2 custom-scrollbar">
          {filteredDocs.map((doc) => (
            <button
              key={doc.id}
              onClick={() => setSelectedDoc(doc)}
              className={`flex items-center gap-3 px-4 py-3 rounded-xl transition-all duration-200 text-left ${
                selectedDoc.id === doc.id
                  ? 'bg-indigo-600/20 text-indigo-400 border border-indigo-500/30 shadow-lg shadow-indigo-500/5'
                  : 'text-slate-400 hover:text-white hover:bg-white/5 border border-transparent'
              }`}
            >
              <doc.icon className={`w-4 h-4 ${selectedDoc.id === doc.id ? 'text-indigo-400' : 'text-slate-500'}`} />
              <span className="flex-1 font-medium text-sm">{doc.label}</span>
              {selectedDoc.id === doc.id && <ChevronRight className="w-4 h-4" />}
            </button>
          ))}
          
          <div className="mt-auto p-4 rounded-2xl bg-indigo-500/5 border border-indigo-500/10">
            <h4 className="text-xs font-bold text-indigo-400 uppercase tracking-wider mb-2">Internal Links</h4>
            <a 
              href="https://github.com/JMiahMan1/SharedLLM" 
              target="_blank" 
              rel="noreferrer"
              className="flex items-center justify-between text-xs text-slate-500 hover:text-indigo-300 transition-colors"
            >
              GitHub Repository
              <ExternalLink className="w-3 h-3" />
            </a>
          </div>
        </div>

        {/* Content Area */}
        <div className="flex-1 glass-panel overflow-y-auto p-8 relative custom-scrollbar">
          {isLoading ? (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-4">
              <div className="w-12 h-12 border-4 border-indigo-500/20 border-t-indigo-500 rounded-full animate-spin" />
              <p className="text-slate-400 animate-pulse font-medium">Retrieving Documentation...</p>
            </div>
          ) : error ? (
            <div className="flex flex-col items-center justify-center h-full gap-4 text-center p-8">
              <div className="w-16 h-16 bg-red-500/10 rounded-2xl flex items-center justify-center mb-2">
                <Info className="w-8 h-8 text-red-400" />
              </div>
              <h3 className="text-lg font-bold text-white">Documentation Unavailable</h3>
              <p className="text-slate-400 max-w-md">
                We couldn't reach the documentation service. Please ensure the Gateway is online and you are authenticated.
              </p>
              <button 
                onClick={() => window.location.reload()}
                className="mt-4 px-6 py-2 bg-white/5 hover:bg-white/10 border border-white/10 rounded-xl transition-all"
              >
                Retry Connection
              </button>
            </div>
          ) : (
            <MarkdownViewer 
              markdown={docContent || ''} 
              onTestExample={handleTestExample}
            />
          )}
        </div>
      </div>
    </div>
  );
};

export default Docs;
