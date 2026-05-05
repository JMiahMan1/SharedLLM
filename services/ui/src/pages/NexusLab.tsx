import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { 
  Play, 
  Terminal, 
  FileCode, 
  Search,
  CheckCircle2,
  AlertCircle,
  RefreshCcw
} from 'lucide-react';
import { api } from '../services/api';

const NexusLab = () => {
  const [activeTab, setActiveTab] = useState<'tests' | 'logs' | 'fix'>('fix');

  return (
    <div className="h-full flex flex-col gap-6">
      <header className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-white">Nexus Lab</h2>
          <p className="text-slate-400">Verification suite and autonomous orchestration reasoning</p>
        </div>
        <div className="flex bg-white/5 p-1 rounded-xl border border-white/10">
          {(['fix', 'tests', 'logs'] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`px-4 py-2 rounded-lg text-xs font-bold transition-all uppercase tracking-wider ${
                activeTab === tab 
                  ? 'bg-purple-600 text-white shadow-lg shadow-purple-500/20' 
                  : 'text-slate-500 hover:text-slate-300'
              }`}
            >
              {tab === 'fix' ? 'Fix-it View' : tab}
            </button>
          ))}
        </div>
      </header>

      <div className="flex-1 overflow-hidden flex flex-col gap-6">
        {activeTab === 'fix' && (
          <div className="flex-1 grid grid-cols-1 lg:grid-cols-2 gap-6 overflow-hidden">
            <div className="glass-panel flex flex-col overflow-hidden border-blue-500/20">
              <div className="p-4 border-b border-white/5 flex items-center justify-between bg-blue-500/5">
                <h3 className="text-sm font-bold text-blue-400 flex items-center gap-2">
                  <FileCode size={16} />
                  AI Reasoning & Plan
                </h3>
                <span className="text-[10px] text-blue-500 font-mono">TASK_ID: 8842-X</span>
              </div>
              <div className="flex-1 p-6 overflow-y-auto space-y-6">
                <div className="space-y-4">
                  <h4 className="text-xs font-bold text-slate-300 uppercase tracking-widest">Thought Process</h4>
                  <p className="text-sm text-slate-400 leading-relaxed italic">
                    "Detecting inconsistency in <code>IdentityService</code> AES-256 decryption. The current implementation fails to handle padding in legacy tokens. I will implement a check for token versioning and apply PKCS7 padding where necessary."
                  </p>
                </div>
                <div className="space-y-3">
                  <h4 className="text-xs font-bold text-slate-300 uppercase tracking-widest">Proposed Plan</h4>
                  <div className="space-y-2">
                    {[
                      'Inspect security.py for padding logic',
                      'Modify decrypt_credential() to handle versioned tokens',
                      'Run pytest test_identity.py to verify fix'
                    ].map((step, i) => (
                      <div key={i} className="flex items-center gap-3 text-xs text-slate-400">
                        <div className="w-5 h-5 rounded-full border border-white/10 flex items-center justify-center text-[10px]">
                          {i + 1}
                        </div>
                        {step}
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>

            <div className="glass-panel flex flex-col overflow-hidden">
               <div className="p-4 border-b border-white/5 flex items-center justify-between bg-slate-800/50">
                  <h3 className="text-sm font-bold text-white flex items-center gap-2">
                    <Terminal size={16} />
                    Verification Output
                  </h3>
                  <button className="p-1 hover:text-purple-400 transition-colors">
                    <RefreshCcw size={14} />
                  </button>
               </div>
               <div className="flex-1 p-4 bg-black/40 font-mono text-xs overflow-y-auto">
                  <div className="text-green-400 mb-2 font-bold underline"># pytest services/identity/tests/test_security.py</div>
                  <div className="text-slate-500">platform linux -- Python 3.12.2, pytest-8.1.1</div>
                  <div className="text-slate-500">rootdir: /home/jeremiah/Code/SharedLLM</div>
                  <div className="mt-4">
                    <span className="text-green-400">PASSED</span> test_identity.py::test_legacy_token_decryption <br />
                    <span className="text-green-400">PASSED</span> test_identity.py::test_v2_token_decryption <br />
                    <span className="text-green-400">PASSED</span> test_identity.py::test_invalid_token_failure
                  </div>
                  <div className="mt-4 text-emerald-400 font-bold border-t border-emerald-500/20 pt-2">
                    3 passed in 0.12s
                  </div>
               </div>
            </div>
          </div>
        )}

        {activeTab === 'tests' && (
          <div className="glass-panel flex-1 p-6">
            <div className="flex items-center justify-between mb-8">
              <h3 className="font-bold text-white">Full Service Verification</h3>
              <button className="glass-button bg-purple-600/50 hover:bg-purple-600">
                <Play size={16} /> Run All Tests
              </button>
            </div>
            <div className="space-y-3">
               {['Identity', 'RAG', 'Gateway', 'Execution', 'Storage'].map((svc) => (
                 <div key={svc} className="flex items-center justify-between p-4 glass-card">
                    <div className="flex items-center gap-4">
                       <CheckCircle2 size={18} className="text-green-500" />
                       <span className="text-sm font-medium">{svc} Service Tests</span>
                    </div>
                    <span className="text-[10px] text-slate-500">Last run: 5 mins ago</span>
                 </div>
               ))}
               <div className="flex items-center justify-between p-4 glass-card border-red-500/20 bg-red-500/5">
                  <div className="flex items-center gap-4">
                     <AlertCircle size={18} className="text-red-500" />
                     <span className="text-sm font-medium">Workspace Runtime Tests</span>
                  </div>
                  <span className="text-[10px] text-red-500 font-bold">2 FAILED</span>
               </div>
            </div>
          </div>
        )}

        {activeTab === 'logs' && <LogViewer />}
      </div>
    </div>
  );
};

const LogViewer = () => {
  const { data: logs, isLoading, refetch } = useQuery({
    queryKey: ['logs'],
    queryFn: () => api.getLogs(100),
    refetchInterval: 5000,
  });

  return (
    <div className="glass-panel flex-1 flex flex-col overflow-hidden">
      <div className="p-4 border-b border-white/5 flex items-center justify-between">
        <div className="relative flex-1 max-w-md">
          <Search className="absolute left-3 top-2.5 text-slate-500" size={14} />
          <input type="text" placeholder="Filter logs..." className="w-full glass-input pl-9 text-xs h-9" />
        </div>
        <button onClick={() => refetch()} className="ml-4 p-2 hover:bg-white/5 rounded-lg transition-colors">
          <RefreshCcw size={16} className={isLoading ? 'animate-spin' : ''} />
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-4 space-y-1 font-mono text-[10px]">
        {logs?.map((log: any) => (
          <div key={log.id} className={`text-slate-500 ${log.level === 'ERROR' ? 'bg-red-500/10 -mx-4 px-4 py-1' : ''}`}>
            <span className="text-slate-600">[{new Date(log.timestamp).toLocaleTimeString()}]</span>{' '}
            <span className={
              log.level === 'ERROR' ? 'text-red-400' : 
              log.level === 'WARNING' ? 'text-yellow-400' : 
              log.level === 'SUCCESS' ? 'text-emerald-400' : 
              'text-blue-400'
            }>
              {log.level}:
            </span>{' '}
            {log.message}
          </div>
        ))}
        {logs?.length === 0 && <div className="text-center py-8 text-slate-500">No logs found.</div>}
      </div>
    </div>
  );
};

export default NexusLab;
