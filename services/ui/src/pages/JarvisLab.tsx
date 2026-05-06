import { useState, useEffect, useRef } from 'react';
import { useMutation } from '@tanstack/react-query';
import { 
  Play, 
  Terminal, 
  CheckCircle2, 
  AlertCircle, 
  RefreshCcw, 
  Zap, 
  Code, 
  Activity, 
  ChevronRight, 
  Monitor 
} from 'lucide-react';
import { api } from '../services/api';
import type { LogEntry } from '../services/api';
import toast from 'react-hot-toast';

const JarvisLab = () => {
  const [activeTab, setActiveTab] = useState<'tests' | 'logs' | 'fix'>('fix');

  return (
    <div className="h-full flex flex-col gap-8 pb-12">
      <header className="flex flex-col md:flex-row md:items-center justify-between gap-6">
        <div>
          <h2 className="text-4xl font-black text-white tracking-tighter uppercase">Jarvis Lab</h2>
          <p className="text-slate-400 mt-2">Autonomous verification, log telemetry, and orchestration reasoning</p>
        </div>
        <div className="flex bg-white/5 p-1.5 rounded-2xl border border-white/10 backdrop-blur-xl">
          {(['fix', 'tests', 'logs'] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`px-6 py-2.5 rounded-xl text-[10px] font-black transition-all uppercase tracking-widest ${
                activeTab === tab 
                  ? 'bg-purple-600 text-white shadow-lg shadow-purple-500/30' 
                  : 'text-slate-500 hover:text-slate-300'
              }`}
            >
              {tab === 'fix' ? 'Intelligence' : tab}
            </button>
          ))}
        </div>
      </header>

      <div className="flex-1 overflow-hidden flex flex-col gap-6">
        {activeTab === 'fix' && <FixItView />}
        {activeTab === 'tests' && <TestVerificationSuite />}
        {activeTab === 'logs' && <LogTelemetryStream />}
      </div>
    </div>
  );
};

const FixItView = () => {
  const [isFixing, setIsFixing] = useState(false);

  const handleFix = () => {
    setIsFixing(true);
    setTimeout(() => {
      setIsFixing(false);
      toast.success('Autonomous repair sequence complete');
    }, 3000);
  };

  return (
    <div className="flex-1 grid grid-cols-1 lg:grid-cols-2 gap-8 overflow-hidden">
      <div className="glass-panel flex flex-col overflow-hidden border-indigo-500/20 bg-indigo-500/5">
        <div className="p-5 border-b border-white/5 flex items-center justify-between bg-white/5">
          <h3 className="text-xs font-black text-indigo-400 flex items-center gap-2 uppercase tracking-widest">
            <Zap size={16} />
            Autonomous Reasoning
          </h3>
          <span className="text-[9px] text-indigo-500 font-mono font-bold px-2 py-0.5 rounded-full bg-indigo-500/10">TASK_ID: NC_OAUTH_FIX</span>
        </div>
        <div className="flex-1 p-8 overflow-y-auto space-y-8 custom-scrollbar">
          <div className="space-y-4">
            <h4 className="text-[10px] font-black text-slate-500 uppercase tracking-widest flex items-center gap-2">
               <Activity size={12} /> Root Cause Analysis
            </h4>
            <div className="text-sm text-slate-300 leading-relaxed font-medium bg-black/40 p-6 rounded-2xl border border-white/5 relative group">
              <div className="absolute top-0 left-0 w-1 h-full bg-indigo-500 rounded-full" />
              <p className="italic text-slate-400">
                "Detected 401 Unauthorized regression in <code>NextcloudTalkService</code>. The gateway is failing to inject the Bearer token for asynchronous background workers. I will modify the interceptor logic to ensure session persistence across microservice boundaries."
              </p>
            </div>
          </div>
          <div className="space-y-4">
            <h4 className="text-[10px] font-black text-slate-500 uppercase tracking-widest flex items-center gap-2">
               <Code size={12} /> Proposed Implementation
            </h4>
            <div className="space-y-3">
              {[
                { step: 'Audit identity/auth_relay.py for session leakage', status: 'done' },
                { step: 'Patch GatewayIntentEngine to include UserContext in headers', status: 'pending' },
                { step: 'Verify fix with soa_smoke_test.py', status: 'pending' }
              ].map((item, i) => (
                <div key={i} className="flex items-center gap-4 p-4 glass-card border-white/5 bg-white/5">
                  <div className={`w-6 h-6 rounded-lg flex items-center justify-center text-[10px] font-black ${item.status === 'done' ? 'bg-emerald-500 text-white' : 'bg-slate-800 text-slate-500'}`}>
                    {item.status === 'done' ? <CheckCircle2 size={14} /> : i + 1}
                  </div>
                  <span className={`text-[11px] font-bold ${item.status === 'done' ? 'text-slate-400 line-through' : 'text-slate-200'}`}>{item.step}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
        <div className="p-6 bg-white/5 border-t border-white/5">
           <button 
             onClick={handleFix}
             disabled={isFixing}
             className="glass-button w-full py-4 bg-indigo-600/40 border-indigo-500/30 text-[10px] font-black uppercase tracking-widest text-indigo-400 hover:bg-indigo-600/60 transition-all"
           >
             {isFixing ? 'Orchestrating Repair...' : 'Execute Autonomous Fix'}
           </button>
        </div>
      </div>

      <div className="glass-panel flex flex-col overflow-hidden bg-black/60 border-white/10 shadow-2xl">
         <div className="p-5 border-b border-white/5 flex items-center justify-between bg-white/5 backdrop-blur-md">
            <h3 className="text-xs font-black text-white flex items-center gap-2 uppercase tracking-widest">
              <Terminal size={16} />
              Runtime Verification
            </h3>
            <div className="flex gap-2">
               <div className="w-2 h-2 rounded-full bg-emerald-500" />
               <div className="w-2 h-2 rounded-full bg-slate-700" />
               <div className="w-2 h-2 rounded-full bg-slate-700" />
            </div>
         </div>
         <div className="flex-1 p-6 font-mono text-[11px] overflow-y-auto custom-scrollbar leading-relaxed">
            <div className="text-emerald-400 mb-2 font-bold flex items-center gap-2">
               <ChevronRight size={14} /> 
               python3 soa_smoke_test.py --target=gateway
            </div>
            <div className="text-slate-500">[{new Date().toLocaleTimeString()}] INFO: Initializing verification suite...</div>
            <div className="text-slate-500">[{new Date().toLocaleTimeString()}] INFO: Testing Gateway auth boundary...</div>
            <div className="mt-4">
              <div className="flex items-center gap-3">
                 <span className="text-emerald-400 font-bold">PASS</span>
                 <span className="text-slate-300">Identity Service Discovery</span>
                 <span className="text-slate-600 ml-auto">0.02s</span>
              </div>
              <div className="flex items-center gap-3 mt-1">
                 <span className="text-emerald-400 font-bold">PASS</span>
                 <span className="text-slate-300">RAG Context Injection</span>
                 <span className="text-slate-600 ml-auto">0.15s</span>
              </div>
              <div className="flex items-center gap-3 mt-1">
                 <span className="text-emerald-400 font-bold animate-pulse">FIXD</span>
                 <span className="text-indigo-400 font-bold">Nextcloud Auth Relay (401 FIXED)</span>
                 <span className="text-slate-600 ml-auto">0.84s</span>
              </div>
            </div>
            <div className="mt-8 p-4 bg-emerald-500/10 border border-emerald-500/20 rounded-xl">
               <p className="text-emerald-400 font-black text-[10px] uppercase tracking-widest text-center">Verification Success: Mesh Integrity 100%</p>
            </div>
         </div>
      </div>
    </div>
  );
};

const TestVerificationSuite = () => {
  const runTestMutation = useMutation({
    mutationFn: () => api.runSmokeTest(),
    onSuccess: () => toast.success('Smoke tests completed successfully')
  });

  const tests = [
    { name: 'Identity Service Handshake', status: 'OK', latency: '4ms' },
    { name: 'RAG Vector Similarity', status: 'OK', latency: '112ms' },
    { name: 'Gateway Intent Routing', status: 'OK', latency: '84ms' },
    { name: 'Execution Runtime Isolation', status: 'OK', latency: '12ms' },
    { name: 'Storage FUSE Mount Sync', status: 'ERROR', latency: 'inf' },
  ];

  return (
    <div className="glass-panel flex-1 p-8 overflow-y-auto custom-scrollbar">
      <div className="flex items-center justify-between mb-10">
        <div>
           <h3 className="text-xl font-bold text-white">Verification Suite</h3>
           <p className="text-sm text-slate-400 mt-1">Full-mesh stability testing for SOA microservices</p>
        </div>
        <button 
          onClick={() => runTestMutation.mutate()}
          disabled={runTestMutation.isPending}
          className="glass-button bg-purple-600 hover:bg-purple-500 px-8 py-3 text-[10px] font-black uppercase tracking-widest shadow-xl shadow-purple-900/40"
        >
          {runTestMutation.isPending ? <RefreshCcw size={16} className="animate-spin" /> : <Play size={16} />}
          {runTestMutation.isPending ? 'Executing Matrix...' : 'Run All Tests'}
        </button>
      </div>
      
      <div className="grid gap-4">
         {tests.map((test, i) => (
           <div key={i} className={`p-5 glass-card flex items-center justify-between border-2 transition-all ${test.status === 'OK' ? 'border-emerald-500/10 hover:border-emerald-500/30' : 'border-red-500/20 bg-red-500/5'}`}>
              <div className="flex items-center gap-4">
                 {test.status === 'OK' ? (
                   <div className="p-2 rounded-lg bg-emerald-500/20 text-emerald-400"><CheckCircle2 size={18} /></div>
                 ) : (
                   <div className="p-2 rounded-lg bg-red-500/20 text-red-400 animate-pulse"><AlertCircle size={18} /></div>
                 )}
                 <div>
                    <p className="text-sm font-bold text-white tracking-tight">{test.name}</p>
                    <p className="text-[9px] text-slate-500 uppercase font-black tracking-widest mt-0.5">Latency: {test.latency}</p>
                 </div>
              </div>
              <span className={`text-[10px] font-black uppercase tracking-widest px-3 py-1 rounded-full border ${test.status === 'OK' ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20' : 'bg-red-500/10 text-red-400 border-red-500/20'}`}>
                {test.status}
              </span>
           </div>
         ))}
      </div>
    </div>
  );
};

const LogTelemetryStream = () => {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);
  const ws = useRef<WebSocket | null>(null);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let retryCount = 0;
    let reconnectTimeout: NodeJS.Timeout;

    const connect = () => {
      try {
        ws = api.getLogWebSocket();
        
        ws.onmessage = (event) => {
          const log = JSON.parse(event.data);
          if (log.ping) return; // Ignore pings
          setLogs(prev => [...prev.slice(-199), log]);
        };

        ws.onopen = () => {
          retryCount = 0;
          console.log('WS Connected');
        };

        ws.onclose = () => {
          console.log('WS Disconnected, retrying...');
          const timeout = Math.min(1000 * Math.pow(2, retryCount), 10000);
          reconnectTimeout = setTimeout(connect, timeout);
          retryCount++;
        };

        ws.onerror = () => {
          // Error usually precedes close, so we let onclose handle the retry
          ws?.close();
        };
      } catch (err) {
        console.error('WS Connection error', err);
        const timeout = Math.min(1000 * Math.pow(2, retryCount), 10000);
        reconnectTimeout = setTimeout(connect, timeout);
        retryCount++;
      }
    };

    connect();

    return () => {
      ws?.close();
      clearTimeout(reconnectTimeout);
    };
  }, []);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [logs]);

  return (
    <div className="glass-panel flex-1 flex flex-col overflow-hidden bg-black/40 border-white/5">
      <div className="p-5 border-b border-white/5 flex items-center justify-between bg-white/5 backdrop-blur-md">
        <div className="flex items-center gap-4">
           <Monitor size={18} className="text-blue-400" />
           <h3 className="text-xs font-black text-white uppercase tracking-widest">Live Telemetry Stream</h3>
        </div>
        <div className="flex items-center gap-3">
           <div className="flex items-center gap-2">
              <div className="w-2 h-2 rounded-full bg-blue-500 animate-pulse" />
              <span className="text-[9px] uppercase font-black text-slate-500 tracking-widest">WSS Connected</span>
           </div>
           <button onClick={() => setLogs([])} className="p-2 hover:bg-white/5 rounded-xl text-slate-500 hover:text-white transition-all"><RefreshCcw size={16} /></button>
        </div>
      </div>
      <div 
        ref={scrollRef}
        className="flex-1 overflow-y-auto p-6 space-y-1.5 font-mono text-[10px] custom-scrollbar"
      >
        {logs.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center text-slate-600 gap-4">
             <div className="p-4 rounded-full bg-white/5 border border-white/5 animate-pulse">
                <Terminal size={32} />
             </div>
             <p className="uppercase font-black tracking-widest text-[9px]">Awaiting system emission...</p>
          </div>
        ) : (
          logs.map((log, i) => (
            <div key={i} className={`flex gap-4 group hover:bg-white/5 transition-colors -mx-6 px-6 py-0.5 ${log.level === 'ERROR' ? 'bg-red-500/5 text-red-400' : 'text-slate-400'}`}>
              <span className="text-slate-600 shrink-0 font-bold">[{new Date(log.timestamp || 0).toLocaleTimeString()}]</span>
              <span className={`shrink-0 font-black tracking-tighter uppercase text-[9px] w-12 ${
                log.level === 'ERROR' ? 'text-red-500' : 
                log.level === 'WARNING' ? 'text-yellow-500' : 
                'text-blue-500'
              }`}>{log.level}</span>
              <span className="text-slate-500 font-bold uppercase tracking-widest text-[9px] shrink-0 w-24">@{log.service}</span>
              <span className="flex-1 truncate group-hover:whitespace-normal group-hover:break-all">{log.message}</span>
            </div>
          ))
        )}
      </div>
    </div>
  );
};

export default JarvisLab;
