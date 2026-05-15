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
            ['missions', 'Missions'],
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
      {activeTab === 'missions' && <MissionsPane />}
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
                <p className="font-semibold text-white truncate">{workspace.display_name || workspace.id}</p>
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
  const runSmokeMutation = useMutation<SmokeTestResult>({
    mutationFn: () => api.runSmokeTest(),
    onSuccess: () => toast.success('Smoke test completed'),
    onError: () => toast.error('Smoke test failed'),
  });

  const runUnitMutation = useMutation<SmokeTestResult>({
    mutationFn: () => api.runUnitTests(),
    onSuccess: () => toast.success('Unit tests completed'),
    onError: () => toast.error('Unit tests failed'),
  });

  const activeMutation = runSmokeMutation.isPending ? runSmokeMutation : runUnitMutation.isPending ? runUnitMutation : null;
  const lastResult = runSmokeMutation.data || runUnitMutation.data;

  return (
    <section className="glass-panel p-6">
      <div className="mb-6 flex items-center justify-between gap-4">
        <div>
          <h3 className="text-xl font-bold text-white">Verification Engine</h3>
          <p className="text-sm text-slate-400">Execute system-wide functional and logic verification suites.</p>
        </div>
        <div className="flex gap-3">
          <button
            onClick={() => runUnitMutation.mutate()}
            disabled={!!activeMutation}
            className="glass-button flex items-center gap-2 px-4 py-3 text-[10px] font-black uppercase tracking-widest disabled:opacity-50"
          >
            {runUnitMutation.isPending ? <RefreshCcw size={14} className="animate-spin" /> : <Terminal size={14} />}
            Run Unit Tests
          </button>
          <button
            onClick={() => runSmokeMutation.mutate()}
            disabled={!!activeMutation}
            className="glass-button flex items-center gap-2 px-4 py-3 text-[10px] font-black uppercase tracking-widest disabled:opacity-50"
          >
            {runSmokeMutation.isPending ? <RefreshCcw size={14} className="animate-spin" /> : <Play size={14} />}
            Run Smoke Test
          </button>
        </div>
      </div>

      {lastResult && (
        <div className="mb-6 rounded-2xl border border-white/5 bg-white/5 p-4">
          <p className="text-sm text-white">
            Last Run Result: <span className={lastResult.passed ? 'text-emerald-300' : 'text-red-300'}>
              {lastResult.passed ? 'PASS' : 'FAIL'}
            </span>
          </p>
        </div>
      )}

      <div className="rounded-2xl border border-white/5 bg-black/30 p-4">
        <div className="mb-3 flex items-center gap-2 text-xs font-black uppercase tracking-widest text-slate-500">
          <Terminal size={14} />
          Raw Output
        </div>
        <pre className="whitespace-pre-wrap font-mono text-xs leading-relaxed text-slate-300 max-h-[500px] overflow-y-auto custom-scrollbar">
          {lastResult?.results || 'Select a test suite to begin verification.'}
        </pre>
      </div>
    </section>
  );
};

const LogTelemetryStream = () => {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    api.getLogs(50).then((historical) => {
      setLogs(historical.slice(0, 80));
    }).catch(() => {});
  }, []);

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

const MissionsPane = () => {
  const [missionQuery, setMissionQuery] = useState('');
  const { data: missions = [], refetch } = useQuery({
    queryKey: ['user-missions'],
    queryFn: () => api.getUserMissions(),
    refetchInterval: 5000,
  });

  const createMissionMutation = useMutation({
    mutationFn: () => api.createUserMission(missionQuery),
    onSuccess: () => {
      setMissionQuery('');
      toast.success('Mission Dispatched');
      refetch();
    },
    onError: (err: any) => toast.error(err.message || 'Failed to dispatch mission'),
  });

  return (
    <section className="glass-panel p-6">
      <div className="mb-6 flex items-center justify-between gap-4">
        <div>
          <h3 className="text-xl font-bold text-white flex items-center gap-2">
            <Play size={20} className="text-indigo-400" />
            Raven Autonomous Missions
          </h3>
          <p className="text-sm text-slate-400 mt-1">Assign long-running background tasks to Raven (e.g., File conversions, analysis).</p>
        </div>
      </div>

      <div className="flex gap-3 mb-8">
        <input
          type="text"
          value={missionQuery}
          onChange={(e) => setMissionQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && missionQuery.trim()) {
              createMissionMutation.mutate();
            }
          }}
          placeholder="Describe the task (e.g., 'Convert all PNGs in the Assets workspace to WebP')"
          className="glass-input flex-1"
        />
        <button
          onClick={() => createMissionMutation.mutate()}
          disabled={!missionQuery.trim() || createMissionMutation.isPending}
          className="glass-button px-6 py-3 bg-indigo-500/20 text-indigo-300 border border-indigo-500/30 hover:bg-indigo-500/30 text-[10px] font-black uppercase tracking-widest disabled:opacity-50 flex items-center gap-2"
        >
          {createMissionMutation.isPending ? 'Dispatching...' : (
            <>
              <Play size={14} /> Dispatch
            </>
          )}
        </button>
      </div>

      <div className="space-y-4">
        <h4 className="text-xs font-black uppercase tracking-widest text-slate-500 mb-2">Active & Recent Missions</h4>
        
        {missions.length === 0 ? (
          <div className="rounded-2xl border border-white/5 bg-white/5 px-4 py-8 text-center text-sm text-slate-500">
            No active missions.
          </div>
        ) : (
          missions.map((mission: any) => (
            <div key={mission.id} className="glass-card p-4 border-l-4 border-l-indigo-500/50 flex flex-col gap-3">
              <div className="flex items-center justify-between gap-4">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-3 mb-1">
                    <span className={`px-2 py-0.5 rounded text-[10px] font-black uppercase tracking-widest ${
                      mission.status === 'completed' ? 'bg-emerald-500/10 text-emerald-300' :
                      mission.status === 'failed' ? 'bg-red-500/10 text-red-300' :
                      mission.status === 'executing' ? 'bg-indigo-500/10 text-indigo-300 animate-pulse' :
                      'bg-slate-500/10 text-slate-300'
                    }`}>
                      {mission.status}
                    </span>
                    <span className="text-xs text-slate-400">Mission #{mission.id}</span>
                  </div>
                  <p className="text-sm text-white line-clamp-2">{mission.proposed_mission}</p>
                </div>
              </div>
              
              {(mission.status === 'executing' || mission.progress > 0) && (
                <div className="w-full bg-black/40 rounded-full h-1.5 overflow-hidden">
                  <div 
                    className={`h-full rounded-full transition-all duration-500 ${
                      mission.status === 'failed' ? 'bg-red-500' :
                      mission.status === 'completed' ? 'bg-emerald-500' : 'bg-indigo-500'
                    }`}
                    style={{ width: `${Math.max(5, mission.progress)}%` }}
                  />
                </div>
              )}
              
              {mission.result && (
                <div className="p-3 bg-black/30 rounded-xl border border-white/5 text-xs text-slate-300 font-mono overflow-x-auto whitespace-pre-wrap">
                  {mission.result}
                </div>
              )}
            </div>
          ))
        )}
      </div>
    </section>
  );
};

export default JarvisLab;
