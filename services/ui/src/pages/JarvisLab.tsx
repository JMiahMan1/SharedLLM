import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { CheckCircle2, Play, RefreshCcw, Terminal, Wrench } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../services/api';
import type { HealthStatus, LogEntry, SmokeTestResult, Workspace } from '../services/api';

const JarvisLab = () => {
  const [activeTab, setActiveTab] = useState<'overview' | 'tests' | 'logs'>('overview');

  return (
    <div className="space-y-8 pb-12">
      <header className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
        <div>
          <h2 className="text-4xl font-black tracking-tighter text-white uppercase">Jarvis Lab</h2>
          <p className="mt-2 text-slate-400">Live verification, smoke execution, workspaces, and telemetry.</p>
        </div>
        <div className="flex rounded-2xl border border-white/10 bg-white/5 p-1">
          {([
            ['overview', 'Overview'],
            ['tests', 'Tests'],
            ['logs', 'Logs'],
          ] as const).map(([key, label]) => (
            <button
              key={key}
              onClick={() => setActiveTab(key)}
              className={`rounded-xl px-4 py-2 text-[10px] font-black uppercase tracking-widest ${
                activeTab === key ? 'bg-indigo-600/40 text-white' : 'text-slate-500 hover:text-white'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </header>

      {activeTab === 'overview' && <OverviewPane />}
      {activeTab === 'tests' && <TestsPane />}
      {activeTab === 'logs' && <LogTelemetryStream />}
    </div>
  );
};

const OverviewPane = () => {
  const { data: health } = useQuery<HealthStatus>({
    queryKey: ['health'],
    queryFn: () => api.getHealth(),
    refetchInterval: 5000,
  });

  const { data: workspaces = [] } = useQuery<Workspace[]>({
    queryKey: ['workspaces'],
    queryFn: () => api.getWorkspaces(),
  });

  return (
    <div className="grid gap-8 xl:grid-cols-[0.9fr_1.1fr]">
      <section className="glass-panel p-6">
        <div className="mb-6 flex items-center gap-3">
          <CheckCircle2 size={20} className="text-emerald-300" />
          <div>
            <h3 className="text-xl font-bold text-white">Mesh Health</h3>
            <p className="text-sm text-slate-400">Current readiness across the running stack.</p>
          </div>
        </div>
        <div className="space-y-3">
          {Object.entries(health?.services || {}).map(([service, status]) => (
            <div key={service} className="glass-card flex items-center justify-between p-4 gap-4 overflow-hidden">
              <span className="font-semibold text-white truncate">{service}</span>
              <span className={`text-[10px] font-black uppercase tracking-widest shrink-0 ${status === 'OK' ? 'text-emerald-300' : 'text-red-300'}`}>
                {status}
              </span>
            </div>
          ))}
        </div>
      </section>

      <section className="glass-panel p-6">
        <div className="mb-6 flex items-center gap-3">
          <Wrench size={20} className="text-cyan-300" />
          <div>
            <h3 className="text-xl font-bold text-white">Workspace Runtime</h3>
            <p className="text-sm text-slate-400">Registered workspaces exposed by the workspace runtime service.</p>
          </div>
        </div>
        <div className="space-y-3">
          {workspaces.map((workspace) => (
            <div key={workspace.id} className="glass-card p-4">
              <div className="flex items-center justify-between gap-4 overflow-hidden">
                <p className="font-semibold text-white truncate">{workspace.name}</p>
                <span className={`text-[10px] font-black uppercase tracking-widest shrink-0 ${workspace.available ? 'text-emerald-300' : 'text-red-300'}`}>
                  {workspace.available ? 'Available' : 'Unavailable'}
                </span>
              </div>
              <p className="mt-2 font-mono text-xs text-slate-400 break-all">{workspace.resolved_path}</p>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
};

const TestsPane = () => {
  const runTestMutation = useMutation<SmokeTestResult>({
    mutationFn: () => api.runSmokeTest(),
    onSuccess: () => toast.success('Smoke test completed'),
    onError: () => toast.error('Smoke test failed'),
  });

  return (
    <section className="glass-panel p-6">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h3 className="text-xl font-bold text-white">Smoke Verification</h3>
          <p className="text-sm text-slate-400">Runs the repo smoke test through workspace runtime.</p>
        </div>
        <button
          onClick={() => runTestMutation.mutate()}
          className="glass-button px-4 py-3 text-[10px] font-black uppercase tracking-widest"
        >
          {runTestMutation.isPending ? <RefreshCcw size={14} className="animate-spin" /> : <Play size={14} />}
          Run Smoke Test
        </button>
      </div>

      {runTestMutation.data && (
        <div className="mb-6 rounded-2xl border border-white/5 bg-white/5 p-4">
          <p className="text-sm text-white">
            Result: <span className={runTestMutation.data.passed ? 'text-emerald-300' : 'text-red-300'}>
              {runTestMutation.data.passed ? 'PASS' : 'FAIL'}
            </span>
          </p>
        </div>
      )}

      <div className="rounded-2xl border border-white/5 bg-black/30 p-4">
        <div className="mb-3 flex items-center gap-2 text-xs font-black uppercase tracking-widest text-slate-500">
          <Terminal size={14} />
          Raw Output
        </div>
        <pre className="whitespace-pre-wrap text-sm text-slate-300">
          {runTestMutation.data?.results || 'Run the smoke test to see live output here.'}
        </pre>
      </div>
    </section>
  );
};

const LogTelemetryStream = () => {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let reconnectTimeout: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      const ws = api.getLogWebSocket();
      wsRef.current = ws;

      ws.onmessage = (event) => {
        const payload = JSON.parse(event.data);
        if (payload.ping) {
          return;
        }
        setLogs((current) => [payload, ...current].slice(0, 80));
      };

      ws.onclose = () => {
        reconnectTimeout = setTimeout(connect, 2000);
      };
    };

    connect();

    return () => {
      if (reconnectTimeout) {
        clearTimeout(reconnectTimeout);
      }
      wsRef.current?.close();
    };
  }, []);

  return (
    <section className="glass-panel p-6">
      <div className="mb-6 flex items-center gap-3">
        <Terminal size={20} className="text-indigo-300" />
        <div>
          <h3 className="text-xl font-bold text-white">Live Logs</h3>
          <p className="text-sm text-slate-400">Streaming websocket telemetry from the logging service.</p>
        </div>
      </div>
      <div className="space-y-3">
        {logs.map((log, index) => (
          <div key={`${log.timestamp}-${index}`} className="glass-card p-4 overflow-hidden">
            <div className="flex items-center justify-between gap-4">
              <p className="font-semibold text-white truncate">{log.service}</p>
              <span className="text-[10px] uppercase tracking-widest text-slate-500 shrink-0">{log.level}</span>
            </div>
            <p className="mt-2 text-sm text-slate-300 break-words">{log.message}</p>
            <p className="mt-2 text-xs text-slate-500">{log.timestamp}</p>
          </div>
        ))}
        {!logs.length && (
          <p className="rounded-2xl border border-white/5 bg-white/5 px-4 py-6 text-center text-sm text-slate-500">
            Waiting for live log traffic...
          </p>
        )}
      </div>
    </section>
  );
};

export default JarvisLab;
