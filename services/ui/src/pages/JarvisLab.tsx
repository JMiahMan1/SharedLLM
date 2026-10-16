import { useEffect, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { CheckCircle2, Play, RefreshCcw, Terminal, Wrench, Zap, Eye, Filter, Trash2, Pause, PlayCircle, FlaskConical, Shield } from 'lucide-react';
import toast from 'react-hot-toast';
import { errorText } from '../lib/errorText';
import { api } from '../services/api';
import type { LogEntry, RavenMission, SmokeTestResult, Workspace } from '../services/api';
import RavenLiveTrace from '../components/settings/RavenLiveTrace';
import RavenMissionDetail from '../components/settings/RavenMissionDetail';
import { useWebSocket, type ConnectionState } from '../lib/wsManager';
import { useAuth } from '../context/AuthContext';
import { useSystemHealth } from '../hooks/useSystemHealth';

const MISSION_TEMPLATES = [
  { label: 'Audit Codebase', query: 'Audit the codebase for lint errors, unused imports, and code quality issues. Fix all findings.' },
  { label: 'Sync Workspaces', query: 'Check workspace status, pull latest from remote, and report any conflicts.' },
  { label: 'Convert Files', query: 'Find all PNG images in the Assets workspace and convert them to WebP format.' },
  { label: 'Check Dependencies', query: 'Review requirements.txt and package.json for outdated or vulnerable dependencies.' },
];

// User View

const JarvisLab = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const tabQuery = searchParams.get('tab');
  const initialTab = tabQuery === 'tests' || tabQuery === 'missions' ? tabQuery : 'missions';
  const [activeTab, setActiveTab] = useState<'tests' | 'missions'>(initialTab);
  const { user } = useAuth();
  const isAdmin = user?.is_admin ?? false;
  const navigate = useNavigate();

  // Sync tab state to query parameter when changed
  const handleTabChange = (tab: 'tests' | 'missions') => {
    setActiveTab(tab);
    setSearchParams({ tab });
  };

  return (
    <div className="space-y-8 pb-12">
      <header className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
        <div>
          <h2 className="text-3xl sm:text-4xl font-black tracking-tighter text-white uppercase">Jarvis Lab</h2>
          <p className="mt-2 text-slate-400">Live verification, autonomous missions, and workspace management.</p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex flex-wrap rounded-2xl border border-white/10 bg-white/5 p-1">
            {([
              ['missions', 'Missions'],
              ['tests', 'Tests'],
            ] as const).map(([key, label]) => (
              <button
                key={key}
                onClick={() => handleTabChange(key)}
                className={`min-h-11 rounded-xl px-4 text-[10px] font-black uppercase tracking-widest ${
                  activeTab === key ? 'bg-indigo-600/40 text-white' : 'text-slate-500 hover:text-white'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          {isAdmin && (
            <button
              onClick={() => navigate('/lab/admin')}
              className="flex min-h-11 min-w-11 items-center justify-center gap-2 rounded-xl border border-purple-500/30 bg-purple-500/10 px-3 text-[10px] font-black uppercase tracking-widest text-purple-300 hover:bg-purple-500/20 transition-colors"
              title="Switch to Admin Lab view"
              aria-label="Admin view"
            >
              <Shield size={14} />
              <span className="hidden md:inline">Admin View</span>
            </button>
          )}
        </div>
      </header>

      {activeTab === 'tests' && <TestsPane />}
      {activeTab === 'missions' && <MissionsPane />}
    </div>
  );
};

// Admin View

export const JarvisLabAdmin = () => {
  const [activeTab, setActiveTab] = useState<'overview' | 'tests' | 'logs'>('overview');
  const { user } = useAuth();
  const navigate = useNavigate();

  // Guard: non-admins should not reach this page, but redirect defensively
  if (!user?.is_admin) {
    return (
      <div className="flex flex-col items-center justify-center gap-4 py-24 text-center">
        <Shield size={48} className="text-slate-600" />
        <p className="text-slate-400">You don't have permission to view this page.</p>
        <button
          onClick={() => navigate('/lab')}
          className="glass-button px-6 py-3 text-[10px] font-black uppercase tracking-widest"
        >
          Go to Jarvis Lab
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-8 pb-12">
      <header className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
        <div>
          <div className="flex items-center gap-3">
            <h2 className="text-3xl sm:text-4xl font-black tracking-tighter text-white uppercase">Advanced Lab</h2>
            <span className="inline-flex items-center px-2 py-1 rounded-lg text-[10px] font-black uppercase tracking-wider bg-purple-500/20 text-purple-300 border border-purple-500/20">
              Admin
            </span>
          </div>
          <p className="mt-2 text-slate-400">System-wide diagnostics, live telemetry, and advanced controls.</p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex flex-wrap rounded-2xl border border-white/10 bg-white/5 p-1">
            {([
              ['overview', 'System Health'],
              ['logs', 'Live Telemetry'],
              ['tests', 'Verification'],
            ] as const).map(([key, label]) => (
              <button
                key={key}
                onClick={() => setActiveTab(key)}
                className={`min-h-11 rounded-xl px-4 text-[10px] font-black uppercase tracking-widest ${
                  activeTab === key ? 'bg-indigo-600/40 text-white' : 'text-slate-500 hover:text-white'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          <button
            onClick={() => navigate('/lab')}
            className="flex min-h-11 min-w-11 items-center justify-center gap-2 rounded-xl border border-white/10 bg-white/5 px-3 text-[10px] font-black uppercase tracking-widest text-slate-400 hover:text-white hover:bg-white/10 transition-colors"
            title="Switch to User Lab view"
            aria-label="User view"
          >
            <FlaskConical size={14} />
            <span className="hidden md:inline">User View</span>
          </button>
        </div>
      </header>

      {activeTab === 'overview' && <AdvancedOverviewPane />}
      {activeTab === 'tests' && <TestsPane advanced />}
      {activeTab === 'logs' && <LogTelemetryStream />}
    </div>
  );
};

// Shared Panes

// Advanced overview adds faster polling and more runtime detail
const AdvancedOverviewPane = () => {
  const { data: health } = useSystemHealth();

  const { data: workspaces = [] } = useQuery<Workspace[]>({
    queryKey: ['workspaces'],
    queryFn: () => api.getWorkspaces(),
    refetchInterval: 5000,
  });

  return (
    <div className="grid gap-8 xl:grid-cols-[0.9fr_1.1fr]">
      <section className="glass-panel p-4 sm:p-6">
        <div className="mb-6 flex items-center gap-3">
          <CheckCircle2 size={20} className="text-emerald-300" />
          <div>
            <h3 className="text-xl font-bold text-white">Mesh Health</h3>
            <p className="text-sm text-slate-400">Real-time readiness across the running stack.</p>
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

      <section className="glass-panel p-4 sm:p-6">
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

// `advanced` prop unlocks both smoke tests and unit tests; user view shows smoke only
const TestsPane = ({ advanced = false }: { advanced?: boolean }) => {
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
    <section className="glass-panel p-4 sm:p-6">
      <div className="mb-6 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h3 className="text-xl font-bold text-white">
            {advanced ? 'Verification Engine' : 'Test Runner'}
          </h3>
          <p className="text-sm text-slate-400">
            {advanced
              ? 'Execute system-wide functional and logic verification suites.'
              : 'Run a smoke test to verify core system connectivity.'}
          </p>
        </div>
        <div className="flex flex-wrap gap-3">
          {advanced && (
            <button
              onClick={() => runUnitMutation.mutate()}
              disabled={!!activeMutation}
              className="glass-button min-h-11 flex items-center gap-2 px-4 text-[10px] font-black uppercase tracking-widest disabled:opacity-50"
            >
              {runUnitMutation.isPending ? <RefreshCcw size={14} className="animate-spin" /> : <Terminal size={14} />}
              Run Unit Tests
            </button>
          )}
          <button
            onClick={() => runSmokeMutation.mutate()}
            disabled={!!activeMutation}
            className="glass-button min-h-11 flex items-center gap-2 px-4 text-[10px] font-black uppercase tracking-widest disabled:opacity-50"
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

const HEALTH_CHECK_PATTERNS = ['/health', '/health/ready', 'health check', 'heartbeat'];
const isHealthCheck = (log: LogEntry) => {
  const msg = String(log.message ?? '').toLowerCase();
  return HEALTH_CHECK_PATTERNS.some((p) => msg.includes(p));
};

const STREAM_STATE: Record<ConnectionState, { label: string; color: string }> = {
  CONNECTING: { label: 'Connecting', color: 'text-slate-400' },
  CONNECTED: { label: 'Live', color: 'text-emerald-300' },
  RECONNECTING: { label: 'Reconnecting', color: 'text-amber-300' },
  DISCONNECTED: { label: 'Offline', color: 'text-slate-500' },
  FAILED: { label: 'Stream unavailable', color: 'text-red-300' },
};

// The system's logs: the last 50 from the logging service, then live lines
// over its WebSocket (admin key required).
const LogTelemetryStream = () => {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [hideHealthChecks, setHideHealthChecks] = useState(true);

  useEffect(() => {
    api.getLogs(50).then((historical) => {
      setLogs((current) => [...current, ...historical].slice(0, 80));
    }).catch(() => {});
  }, []);

  const [streamUrl] = useState(() => api.getLogWebSocketUrl());
  const { state } = useWebSocket(streamUrl, {
    onMessage: (event) => {
      let payload: (LogEntry & { ping?: boolean }) | null;
      try {
        payload = JSON.parse(event.data);
      } catch {
        return;
      }
      if (!payload || payload.ping) return;
      const entry: LogEntry = payload;
      setLogs((current) => [entry, ...current].slice(0, 80));
    },
  });

  const visibleLogs = hideHealthChecks ? logs.filter((log) => !isHealthCheck(log)) : logs;
  const status = STREAM_STATE[state];

  return (
    <section className="glass-panel p-4 sm:p-6">
      <div className="mb-6 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div className="flex items-center gap-3">
          <Terminal size={20} className="text-indigo-300 shrink-0" />
          <div>
            <h3 className="text-xl font-bold text-white">Live Telemetry</h3>
            <p className="text-sm text-slate-400">
              Every service&rsquo;s log lines as they arrive.{' '}
              <span className={`text-[10px] font-black uppercase tracking-widest ${status.color}`}>{status.label}</span>
            </p>
          </div>
        </div>
        <label className="flex min-h-11 items-center gap-2 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={hideHealthChecks}
            onChange={(e) => setHideHealthChecks(e.target.checked)}
            className="sr-only"
          />
          <span aria-hidden className={`w-8 h-4 rounded-full relative transition-colors ${hideHealthChecks ? 'bg-indigo-600' : 'bg-slate-700'}`}>
            <span className={`absolute top-0.5 left-0.5 w-3 h-3 rounded-full bg-white transition-transform ${hideHealthChecks ? 'translate-x-4' : ''}`} />
          </span>
          <span className="text-[10px] font-black uppercase tracking-widest text-slate-400">Hide health checks</span>
        </label>
      </div>
      <div className="space-y-3">
        {visibleLogs.map((log, index) => (
          <div key={`${log.timestamp}-${index}`} className="glass-card p-4 overflow-hidden">
            <div className="flex items-center justify-between gap-4">
              <p className="font-semibold text-white truncate">{log.service}</p>
              <span className="text-[10px] uppercase tracking-widest text-slate-500 shrink-0">{log.level}</span>
            </div>
            <p className="mt-2 text-sm text-slate-300 break-words">{String(log.message ?? '')}</p>
            <p className="mt-2 text-xs text-slate-500">{log.timestamp}</p>
          </div>
        ))}
        {!visibleLogs.length && (
          <p className="rounded-2xl border border-white/5 bg-white/5 px-4 py-8 text-center text-sm text-slate-500">
            {logs.length > 0
              ? 'Every line so far is a health check. Turn the filter off to see them.'
              : 'Waiting for log lines…'}
          </p>
        )}
      </div>
    </section>
  );
};

// Statuses a mission can be in (services/identity/models.py RavenMission.status).
const STATUS_FILTERS = ['all', 'pending', 'queued', 'executing', 'paused', 'completed', 'failed'] as const;
const isLive = (status: string) => status === 'executing' || status === 'running';

const actionBtn = 'glass-button min-h-11 px-3 flex items-center gap-1 text-[10px] font-black uppercase tracking-widest disabled:opacity-50';

const MissionsPane = () => {
  const [missionQuery, setMissionQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<(typeof STATUS_FILTERS)[number]>('all');
  const [liveMissionId, setLiveMissionId] = useState<number | null>(null);
  const [detailId, setDetailId] = useState<number | null>(null);

  const { data: missions = [], refetch, isLoading } = useQuery<RavenMission[]>({
    queryKey: ['user-missions'],
    queryFn: () => api.getUserMissions(),
    refetchInterval: 5000,
  });

  const act = (fn: (id: number) => Promise<unknown>, done: string, failed: string) => ({
    mutationFn: fn,
    onSuccess: () => {
      toast.success(done);
      refetch();
    },
    onError: (err: unknown) => toast.error(errorText(err, failed)),
  });

  const createMissionMutation = useMutation({
    mutationFn: () => api.createUserMission(missionQuery.trim()),
    onSuccess: () => {
      setMissionQuery('');
      toast.success('Mission dispatched');
      refetch();
    },
    onError: (err: unknown) => toast.error(errorText(err, 'Failed to dispatch mission')),
  });
  const killMissionMutation = useMutation(act((id) => api.killRavenMission(id), 'Stop requested', 'Failed to stop mission'));
  const deleteMissionMutation = useMutation(act((id) => api.deleteRavenMission(id), 'Mission deleted', 'Failed to delete mission'));
  const pauseMissionMutation = useMutation(act((id) => api.pauseRavenMission(id), 'Mission paused', 'Failed to pause mission'));
  const resumeMissionMutation = useMutation(act((id) => api.resumeRavenMission(id), 'Mission resumed', 'Failed to resume mission'));

  const filteredMissions = statusFilter === 'all' ? missions : missions.filter((m) => m.status === statusFilter);
  const dispatch = () => {
    if (missionQuery.trim() && !createMissionMutation.isPending) createMissionMutation.mutate();
  };

  return (
    <section className="glass-panel p-4 sm:p-6">
      <div className="mb-6">
        <h3 className="text-xl font-bold text-white flex items-center gap-2">
          <Play size={20} className="text-indigo-400" />
          Raven Missions
        </h3>
        <p className="text-sm text-slate-400 mt-1">Hand Raven a long-running task; it works in the background and reports back here.</p>
      </div>

      <div className="mb-4">
        <h4 className="text-[10px] font-black uppercase tracking-widest text-slate-500 mb-2">Start from</h4>
        <div className="flex flex-wrap gap-2">
          {MISSION_TEMPLATES.map(t => (
            <button
              key={t.label}
              onClick={() => setMissionQuery(t.query)}
              className="min-h-11 px-3 rounded-xl border border-white/10 bg-white/5 text-xs text-slate-300 hover:bg-indigo-500/20 hover:border-indigo-500/30 hover:text-indigo-300 transition-colors"
            >
              <Zap size={12} className="inline mr-1" />
              {t.label}
            </button>
          ))}
        </div>
      </div>

      <div className="flex flex-col sm:flex-row gap-3 mb-8">
        <textarea
          value={missionQuery}
          onChange={(e) => setMissionQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              dispatch();
            }
          }}
          rows={3}
          aria-label="Mission"
          placeholder="Describe the task, e.g. 'Convert the PNGs in my Assets workspace to WebP'"
          className="glass-input flex-1 resize-none"
        />
        <button
          onClick={dispatch}
          disabled={!missionQuery.trim() || createMissionMutation.isPending}
          className="glass-button min-h-11 px-6 bg-indigo-500/20 text-indigo-300 border border-indigo-500/30 hover:bg-indigo-500/30 text-[10px] font-black uppercase tracking-widest disabled:opacity-50 flex items-center justify-center gap-2"
        >
          {createMissionMutation.isPending ? 'Dispatching…' : (<><Play size={14} /> Dispatch</>)}
        </button>
      </div>

      <div className="flex items-center gap-2 mb-4 overflow-x-auto pb-1" role="group" aria-label="Filter by status">
        <Filter size={14} className="text-slate-500 shrink-0" />
        {STATUS_FILTERS.map(s => (
          <button
            key={s}
            onClick={() => setStatusFilter(s)}
            aria-pressed={statusFilter === s}
            className={`min-h-11 px-3 rounded-lg text-[10px] font-black uppercase tracking-widest transition-colors whitespace-nowrap ${
              statusFilter === s ? 'bg-indigo-500/30 text-indigo-300' : 'text-slate-500 hover:text-white'
            }`}
          >
            {s}
          </button>
        ))}
      </div>

      <div className="space-y-4">
        <h4 className="text-xs font-black uppercase tracking-widest text-slate-500 mb-2">
          Missions ({filteredMissions.length})
        </h4>

        {isLoading ? (
          <p className="text-sm text-slate-500">Loading missions…</p>
        ) : filteredMissions.length === 0 ? (
          <div className="rounded-2xl border border-white/5 bg-white/5 px-4 py-8 text-center text-sm text-slate-500">
            {missions.length ? 'No missions match this filter.' : 'No missions yet. Describe a task above to start one.'}
          </div>
        ) : (
          filteredMissions.map((mission) => (
            <div key={mission.id} className="glass-card p-4 border-l-4 border-l-indigo-500/50 flex flex-col gap-3 min-w-0">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mb-1">
                  <span className={`px-2 py-0.5 rounded text-[10px] font-black uppercase tracking-widest ${
                    mission.status === 'completed' ? 'bg-emerald-500/10 text-emerald-300' :
                    mission.status === 'failed' ? 'bg-red-500/10 text-red-300' :
                    isLive(mission.status) ? 'bg-indigo-500/10 text-indigo-300 animate-pulse' :
                    'bg-slate-500/10 text-slate-300'
                  }`}>
                    {mission.status}
                  </span>
                  <span className="text-xs text-slate-400">#{mission.id}</span>
                  <span className="text-[10px] text-slate-500 font-mono">
                    {new Date(mission.started_at ?? mission.created_at).toLocaleString()}
                  </span>
                </div>
                <p className="text-sm text-white line-clamp-2 break-words">{mission.proposed_mission}</p>
              </div>

              {(isLive(mission.status) || mission.progress > 0) && (
                <div className="w-full bg-black/40 rounded-full h-1.5 overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all duration-500 ${
                      mission.status === 'failed' ? 'bg-red-500' : mission.status === 'completed' ? 'bg-emerald-500' : 'bg-indigo-500'
                    }`}
                    style={{ width: `${Math.max(5, mission.progress || 0)}%` }}
                  />
                </div>
              )}

              {mission.result && (
                <div className="p-3 bg-black/30 rounded-xl border border-white/5 text-xs text-slate-300 font-mono whitespace-pre-wrap break-words line-clamp-6">
                  {mission.result}
                </div>
              )}

              <div className="flex flex-wrap items-center gap-2">
                {isLive(mission.status) && (
                  <button onClick={() => setLiveMissionId(mission.id)} className={`${actionBtn} text-blue-300`}>
                    <Eye size={12} /> Watch live
                  </button>
                )}
                <button onClick={() => setDetailId(mission.id)} className={`${actionBtn} text-indigo-300`}>
                  <Terminal size={12} /> {mission.status === 'completed' || mission.status === 'failed' || mission.status === 'paused' ? 'Inspect & refine' : 'Inspect'}
                </button>
                {isLive(mission.status) && (
                  <>
                    <button onClick={() => pauseMissionMutation.mutate(mission.id)} disabled={pauseMissionMutation.isPending} className={`${actionBtn} text-yellow-300`}>
                      <Pause size={12} /> Pause
                    </button>
                    <button
                      onClick={() => {
                        if (window.confirm(`Stop mission #${mission.id}?`)) killMissionMutation.mutate(mission.id);
                      }}
                      disabled={killMissionMutation.isPending}
                      className={`${actionBtn} text-red-400`}
                    >
                      Stop
                    </button>
                  </>
                )}
                {mission.status === 'paused' && (
                  <button onClick={() => resumeMissionMutation.mutate(mission.id)} disabled={resumeMissionMutation.isPending} className={`${actionBtn} text-emerald-300`}>
                    <PlayCircle size={12} /> Resume
                  </button>
                )}
                {!isLive(mission.status) && mission.status !== 'paused' && (
                  <button
                    onClick={() => {
                      if (window.confirm(`Delete mission #${mission.id} and its log?`)) deleteMissionMutation.mutate(mission.id);
                    }}
                    disabled={deleteMissionMutation.isPending}
                    className={`${actionBtn} text-slate-400 hover:text-red-300`}
                  >
                    <Trash2 size={12} /> Delete
                  </button>
                )}
              </div>
            </div>
          ))
        )}
      </div>

      <RavenLiveTrace isOpen={liveMissionId !== null} onClose={() => setLiveMissionId(null)} missionId={liveMissionId} />
      <RavenMissionDetail
        missionId={detailId}
        onClose={() => setDetailId(null)}
        onRefined={(id) => {
          refetch();
          setLiveMissionId(id);
        }}
      />
    </section>
  );
};

export default JarvisLab;
