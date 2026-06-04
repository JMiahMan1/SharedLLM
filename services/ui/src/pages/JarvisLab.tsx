import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { CheckCircle2, Play, RefreshCcw, Terminal, Wrench, Zap, Eye, Filter, Trash2, Pause, PlayCircle } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../services/api';
import type { HealthStatus, LogEntry, SmokeTestResult, Workspace } from '../services/api';
import RavenLiveTrace from '../components/settings/RavenLiveTrace';

const MISSION_TEMPLATES = [
  { label: 'Audit Codebase', query: 'Audit the codebase for lint errors, unused imports, and code quality issues. Fix all findings.' },
  { label: 'Sync Workspaces', query: 'Check workspace status, pull latest from remote, and report any conflicts.' },
  { label: 'Convert Files', query: 'Find all PNG images in the Assets workspace and convert them to WebP format.' },
  { label: 'Check Dependencies', query: 'Review requirements.txt and package.json for outdated or vulnerable dependencies.' },
];

const JarvisLab = () => {
  const [activeTab, setActiveTab] = useState<'overview' | 'tests' | 'logs' | 'missions'>('overview');

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
  const [hideHealthChecks, setHideHealthChecks] = useState(true);
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

  const HEALTH_CHECK_PATTERNS = ['/health', '/health/ready', 'health check', 'heartbeat'];
  const isHealthCheck = (log: LogEntry) => {
    const msg = (log.message || '').toLowerCase();
    return HEALTH_CHECK_PATTERNS.some(p => msg.includes(p));
  };

  const visibleLogs = hideHealthChecks
    ? logs.filter(log => !isHealthCheck(log))
    : logs;

  return (
    <section className="glass-panel p-6">
      <div className="mb-6 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Terminal size={20} className="text-indigo-300" />
          <div>
            <h3 className="text-xl font-bold text-white">Live Logs</h3>
            <p className="text-sm text-slate-400">Streaming websocket telemetry from the logging service.</p>
          </div>
        </div>
        <label className="flex items-center gap-2 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={hideHealthChecks}
            onChange={(e) => setHideHealthChecks(e.target.checked)}
            className="sr-only peer"
          />
          <div className="w-8 h-4 rounded-full bg-slate-700 peer-checked:bg-indigo-600 relative transition-colors">
            <div className="absolute top-0.5 left-0.5 w-3 h-3 rounded-full bg-white transition-transform peer-checked:translate-x-4" />
          </div>
          <span className="text-[10px] font-black uppercase tracking-widest text-slate-400">Hide Health Checks</span>
        </label>
      </div>
      <div className="space-y-3">
        {visibleLogs.map((log, index) => (
          <div key={`${log.timestamp}-${index}`} className="glass-card p-4 overflow-hidden">
            <div className="flex items-center justify-between gap-4">
              <p className="font-semibold text-white truncate">{log.service}</p>
              <span className="text-[10px] uppercase tracking-widest text-slate-500 shrink-0">{log.level}</span>
            </div>
            <p className="mt-2 text-sm text-slate-300 break-words">{log.message}</p>
            <p className="mt-2 text-xs text-slate-500">{log.timestamp}</p>
          </div>
        ))}
        {!visibleLogs.length && (
          <p className="rounded-2xl border border-white/5 bg-white/5 px-4 py-6 text-center text-sm text-slate-500">
            {logs.length > 0
              ? 'All visible logs are health checks. Toggle the filter to see them.'
              : 'Waiting for live log traffic...'}
          </p>
        )}
      </div>
    </section>
  );
};

const MissionsPane = () => {
  const [missionQuery, setMissionQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [liveMissionId, setLiveMissionId] = useState<number | null>(null);
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
    onError: (err: unknown) => toast.error(err instanceof Error ? err.message : 'Failed to dispatch mission'),
  });

  const killMissionMutation = useMutation({
    mutationFn: (id: number) => api.killRavenMission(id),
    onSuccess: () => {
      toast.success('Kill Signal Dispatched');
      refetch();
    },
    onError: (err: unknown) => toast.error(err instanceof Error ? err.message : 'Failed to kill mission'),
  });

  const deleteMissionMutation = useMutation({
    mutationFn: (id: number) => api.deleteRavenMission(id),
    onSuccess: () => {
      toast.success('Mission Deleted');
      refetch();
    },
    onError: (err: unknown) => toast.error(err instanceof Error ? err.message : 'Failed to delete mission'),
  });

  const pauseMissionMutation = useMutation({
    mutationFn: (id: number) => api.pauseRavenMission(id),
    onSuccess: () => {
      toast.success('Mission Paused');
      refetch();
    },
    onError: (err: unknown) => toast.error(err instanceof Error ? err.message : 'Failed to pause mission'),
  });

  const resumeMissionMutation = useMutation({
    mutationFn: (id: number) => api.resumeRavenMission(id),
    onSuccess: () => {
      toast.success('Mission Resumed');
      refetch();
    },
    onError: (err: unknown) => toast.error(err instanceof Error ? err.message : 'Failed to resume mission'),
  });

  const filteredMissions = statusFilter === 'all'
    ? missions
    : missions.filter((m: { status: string }) => m.status === statusFilter);

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

      <div className="mb-6">
        <h4 className="text-[10px] font-black uppercase tracking-widest text-slate-500 mb-3">Quick Actions</h4>
        <div className="flex flex-wrap gap-2">
          {MISSION_TEMPLATES.map(t => (
            <button
              key={t.label}
              onClick={() => setMissionQuery(t.query)}
              className="px-3 py-1.5 rounded-xl border border-white/10 bg-white/5 text-xs text-slate-300 hover:bg-indigo-500/20 hover:border-indigo-500/30 hover:text-indigo-300 transition-colors"
            >
              <Zap size={12} className="inline mr-1" />
              {t.label}
            </button>
          ))}
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

      <div className="flex items-center gap-2 mb-4">
        <Filter size={14} className="text-slate-500" />
        <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Filter:</span>
        {['all', 'executing', 'paused', 'completed', 'failed', 'pending'].map(s => (
          <button
            key={s}
            onClick={() => setStatusFilter(s)}
            className={`px-2 py-1 rounded-lg text-[10px] font-black uppercase tracking-widest transition-colors ${
              statusFilter === s
                ? 'bg-indigo-500/30 text-indigo-300'
                : 'text-slate-500 hover:text-white'
            }`}
          >
            {s}
          </button>
        ))}
      </div>

      <div className="space-y-4">
        <h4 className="text-xs font-black uppercase tracking-widest text-slate-500 mb-2">
          Active & Recent Missions ({filteredMissions.length})
        </h4>

        {filteredMissions.length === 0 ? (
          <div className="rounded-2xl border border-white/5 bg-white/5 px-4 py-8 text-center text-sm text-slate-500">
            No missions match the current filter.
          </div>
        ) : (
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          filteredMissions.map((mission: any) => (
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
                    {(mission.status === 'executing' || mission.status === 'running') && (
                      <span className="text-[10px] text-slate-500 font-mono">Started: {new Date(mission.created_at).toLocaleTimeString()}</span>
                    )}
                  </div>
                  <p className="text-sm text-white line-clamp-2">{mission.proposed_mission}</p>
                </div>
                <div className="flex-shrink-0 flex items-center gap-2">
                  {(mission.status === 'executing' || mission.status === 'running') && (
                    <>
                      <button
                        onClick={() => setLiveMissionId(mission.id)}
                        className="glass-button bg-blue-500/10 border-blue-500/20 text-blue-300 hover:bg-blue-500/20 px-3 py-1.5 flex items-center gap-1 text-[10px] font-black uppercase tracking-widest"
                      >
                        <Eye size={12} /> Watch Live
                      </button>
                      <button
                        onClick={() => pauseMissionMutation.mutate(mission.id)}
                        disabled={pauseMissionMutation.isPending}
                        className="glass-button bg-yellow-500/10 border-yellow-500/20 text-yellow-300 hover:bg-yellow-500/20 px-3 py-1.5 flex items-center gap-1 text-[10px] font-black uppercase tracking-widest disabled:opacity-50"
                      >
                        <Pause size={12} /> Pause
                      </button>
                      <button
                        onClick={() => killMissionMutation.mutate(mission.id)}
                        disabled={killMissionMutation.isPending}
                        className="glass-button bg-red-500/10 border-red-500/20 text-red-400 hover:bg-red-500/20 px-3 py-1.5 flex items-center gap-1 text-[10px] font-black uppercase tracking-widest disabled:opacity-50"
                      >
                        Stop
                      </button>
                    </>
                  )}
                  {mission.status === 'paused' && (
                    <button
                      onClick={() => resumeMissionMutation.mutate(mission.id)}
                      disabled={resumeMissionMutation.isPending}
                      className="glass-button bg-emerald-500/10 border-emerald-500/20 text-emerald-300 hover:bg-emerald-500/20 px-3 py-1.5 flex items-center gap-1 text-[10px] font-black uppercase tracking-widest disabled:opacity-50"
                    >
                      <PlayCircle size={12} /> Resume
                    </button>
                  )}
                  {(mission.status === 'completed' || mission.status === 'failed') && (
                    <button
                      onClick={() => deleteMissionMutation.mutate(mission.id)}
                      disabled={deleteMissionMutation.isPending}
                      className="glass-button bg-slate-500/10 border-slate-500/20 text-slate-400 hover:bg-red-500/20 hover:text-red-300 hover:border-red-500/30 px-3 py-1.5 flex items-center gap-1 text-[10px] font-black uppercase tracking-widest disabled:opacity-50"
                    >
                      <Trash2 size={12} /> Delete
                    </button>
                  )}
                </div>
              </div>

              {(mission.status === 'executing' || (mission.progress !== undefined && mission.progress > 0)) && (
                <div className="w-full bg-black/40 rounded-full h-1.5 overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all duration-500 ${
                      mission.status === 'failed' ? 'bg-red-500' :
                      mission.status === 'completed' ? 'bg-emerald-500' : 'bg-indigo-500'
                    }`}
                    style={{ width: `${Math.max(5, mission.progress || 0)}%` }}
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

      <RavenLiveTrace
        isOpen={liveMissionId !== null}
        onClose={() => setLiveMissionId(null)}
        missionId={liveMissionId}
      />
    </section>
  );
};

export default JarvisLab;
