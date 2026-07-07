import { useMemo, useState, useCallback, useEffect, useRef, Component, type ReactNode } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Activity,
  ArrowUpRight,
  Brain,
  Cpu,
  FileText,
  FolderKanban,
  Globe,
  Loader2,
  Search,
  Shield,
  X,
  Mic,
  AlertTriangle,
  RefreshCw,
  CheckCircle2,
  XCircle,
} from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../services/api';
import type { HealthStatus, LogEntry, Workspace, SearchResult } from '../services/api';
import { useAuth } from '../context/AuthContext';
import { useRavenMissions } from '../hooks/useRavenMissions';
import { useHaptics } from '../hooks/useHaptics';
import { useDebounce } from '../hooks/useDebounce';
import HaloBanner from '../components/presence/HaloBanner';
import VoiceAssistantOverlay from '../components/voice/VoiceAssistantOverlay';
import Modal from '../components/ui/Modal';
import BentoBoxDashboard from '../components/dashboard/BentoBoxDashboard';

// ── Per-section Error Boundary ───────────────────────────────────────────────

interface SectionBoundaryState { hasError: boolean; error: Error | null }
interface SectionBoundaryProps { children: ReactNode; label: string }

class SectionErrorBoundary extends Component<SectionBoundaryProps, SectionBoundaryState> {
  constructor(props: SectionBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error(`[Dashboard] Section "${this.props.label}" threw:`, error, info);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="glass-panel p-6 border-red-500/20 bg-red-950/10">
          <div className="flex items-center gap-3 mb-2">
            <AlertTriangle size={18} className="text-red-400 shrink-0" />
            <p className="text-sm font-semibold text-red-300">{this.props.label} failed to render</p>
          </div>
          <p className="text-xs text-red-400/60 mb-4 ml-7">
            {this.state.error?.message || 'An unexpected error occurred'}
          </p>
          <button
            onClick={() => this.setState({ hasError: false, error: null })}
            className="glass-button px-3 py-1.5 text-xs text-red-300 ml-7"
          >
            <RefreshCw size={11} />
            Retry section
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

// ── Types ────────────────────────────────────────────────────────────────────

type ServiceSummary = {
  key: string;
  label: string;
  icon: typeof Shield;
  status: string;
  details: Array<{ label: string; value: string }>;
};

const SERVICE_ICON_MAP = {
  identity: Shield,
  execution: Cpu,
  gateway: Globe,
  workspace_runtime: FolderKanban,
  logging: FileText,
  storage: FolderKanban,
  rag: Activity,
  redis: Activity,
} as const;

// ── Service Status Card ──────────────────────────────────────────────────────

const ServiceCard = ({ service, onClick }: { service: ServiceSummary; onClick: () => void }) => {
  const Icon = service.icon;
  const isOk = service.status === 'READY';

  return (
    <button
      onClick={onClick}
      className="glass-card p-4 text-left group hover:border-purple-500/25 transition-all duration-200"
      aria-label={`View ${service.label} service details`}
    >
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center justify-center w-9 h-9 rounded-xl bg-white/5 group-hover:bg-purple-500/10 transition-colors">
          <Icon size={17} className="text-purple-300" />
        </div>
        <div className={`flex items-center gap-1.5 text-[10px] font-black uppercase tracking-wider ${isOk ? 'text-emerald-400' : 'text-red-400'}`}>
          {isOk ? <CheckCircle2 size={12} /> : <XCircle size={12} />}
          {service.status}
        </div>
      </div>
      <p className="font-semibold text-sm capitalize text-white group-hover:text-purple-200 transition-colors">
        {service.label}
      </p>
      <p className="mt-0.5 text-[10px] text-slate-500 leading-relaxed">
        Health · Logs · Metrics
      </p>
    </button>
  );
};

// ── Log Entry Card ────────────────────────────────────────────────────────────

const LOG_LEVEL_COLORS: Record<string, string> = {
  error: 'text-red-400',
  warning: 'text-amber-400',
  warn: 'text-amber-400',
  info: 'text-blue-400',
  debug: 'text-slate-500',
};

const LogEntryCard = ({ log }: { log: LogEntry }) => (
  <div className="flex items-start gap-3 px-4 py-3 rounded-xl bg-black/20 border border-white/5 hover:bg-black/30 transition-colors">
    <div
      className={`w-1.5 h-1.5 rounded-full mt-1.5 shrink-0 ${
        log.level === 'error' ? 'bg-red-500' :
        (log.level === 'warning' || log.level === 'warn') ? 'bg-amber-500' :
        'bg-blue-500/50'
      }`}
    />
    <div className="min-w-0 flex-1">
      <div className="flex items-center justify-between gap-2 mb-0.5">
        <p className="text-xs font-semibold text-slate-300 capitalize truncate">{log.service}</p>
        <span className={`text-[9px] font-bold uppercase tracking-widest shrink-0 ${LOG_LEVEL_COLORS[log.level?.toLowerCase()] ?? 'text-slate-500'}`}>
          {log.level}
        </span>
      </div>
      <p className="text-xs text-slate-400 leading-relaxed break-words">{log.message}</p>
      <p className="text-[9px] text-slate-600 mt-1">{new Date(log.timestamp).toLocaleString()}</p>
    </div>
  </div>
);

// ── Workspace Card ────────────────────────────────────────────────────────────

const WorkspaceCard = ({ workspace }: { workspace: Workspace }) => (
  <div className="flex items-start gap-3 p-4 rounded-xl bg-black/20 border border-white/5 hover:border-emerald-500/15 transition-all">
    <div className={`w-2 h-2 rounded-full mt-1.5 shrink-0 ${workspace.available ? 'bg-emerald-400' : 'bg-red-400'}`} />
    <div className="min-w-0 flex-1">
      <div className="flex items-center justify-between gap-2">
        <p className="font-semibold text-sm text-white truncate">
          {workspace.display_name || workspace.id}
        </p>
        <span className={`text-[9px] font-black uppercase tracking-widest shrink-0 px-2 py-0.5 rounded-md border ${
          workspace.available
            ? 'bg-emerald-500/10 text-emerald-300 border-emerald-500/20'
            : 'bg-red-500/10 text-red-300 border-red-500/20'
        }`}>
          {workspace.available ? 'Online' : 'Offline'}
        </span>
      </div>
      <p className="mt-1 font-mono text-[9px] text-slate-600 break-all bg-black/20 px-2 py-1 rounded-md">
        {workspace.resolved_path || 'Path resolution failed'}
      </p>
    </div>
  </div>
);

// ── Search hook ──────────────────────────────────────────────────────────────

function useSearch(query: string) {
  const [results, setResults] = useState<SearchResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isSearching, setIsSearching] = useState(false);
  const abortControllerRef = useRef<AbortController | null>(null);
  const hasSearchedRef = useRef(false);

  useEffect(() => {
    if (!query) {
      if (hasSearchedRef.current) {
        hasSearchedRef.current = false;
        setResults(null);
        setError(null);
      }
      return;
    }

    hasSearchedRef.current = true;

    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }

    const controller = new AbortController();
    abortControllerRef.current = controller;

    const performSearch = async () => {
      setIsSearching(true);
      setError(null);

      try {
        const data = await api.globalSearch(query);
        if (!controller.signal.aborted) {
          setResults(data);
          if (!data.answer && (!data.files || data.files.length === 0)) {
            setError('No results found');
          }
        }
      } catch {
        if (!controller.signal.aborted) {
          toast.error('Search failed');
          setError('Search failed. Please try again.');
        }
      } finally {
        if (!controller.signal.aborted) {
          setIsSearching(false);
        }
      }
    };

    performSearch();

    return () => {
      controller.abort();
    };
  }, [query]);

  const clear = useCallback(() => {
    setResults(null);
    setError(null);
    hasSearchedRef.current = false;
  }, []);

  return { results, error, isSearching, clear };
}

// ── Dashboard ────────────────────────────────────────────────────────────────

const Dashboard = () => {
  const queryClient = useQueryClient();
  const [selectedService, setSelectedService] = useState<ServiceSummary | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const { user } = useAuth();
  const { trigger } = useHaptics();
  const [voiceOpen, setVoiceOpen] = useState(false);

  const debouncedSearch = useDebounce(searchQuery, 300);
  const { results: searchResults, error: searchError, isSearching, clear: clearSearch } = useSearch(debouncedSearch);

  const { data: health } = useQuery<HealthStatus>({
    queryKey: ['health'],
    queryFn: () => api.getHealth(),
    refetchInterval: 5000,
    // Don't crash on failure — just return stale data
    retry: 2,
  });

  const { data: serviceInfo } = useQuery<{ service: string; version: string; git_sha: string; git_branch: string } | null>({
    queryKey: ['service-info'],
    queryFn: () => api.getInfo(),
    refetchOnWindowFocus: false,
    retry: false,
  });

  const { data: logs = [] } = useQuery<LogEntry[]>({
    queryKey: ['recent-logs'],
    queryFn: () => api.getLogs(50),
    refetchInterval: 10000,
    retry: 1,
  });

  const filteredLogs = useMemo(() => {
    return logs.filter(n => {
      const msg = n.message.toLowerCase();
      const service = n.service.toLowerCase();

      // 1. Check if it's an update notification (admin only)
      const isUpdate = msg.includes('update available') || msg.includes('updates available') || msg.includes('image update');
      if (isUpdate) {
        return !!user?.is_admin;
      }

      // 2. Check if it's an ingestion / upload / import event (all users can see)
      const isIngest = ['ingest', 'upload', 'import', 'document', 'file', 'rag'].some(
        kw => msg.includes(kw) || service.includes(kw)
      );
      if (isIngest) {
        return true;
      }

      // 3. Check if it's a communication (Nextcloud Talk, messages, mentions, chats)
      const isComm = ['talk', 'nextcloud', 'message', 'chat', 'mention', 'communication', 'notification'].some(
        kw => msg.includes(kw) || service.includes(kw)
      );

      const relatesToUser = user?.username && msg.includes(user.username.toLowerCase());

      if (isComm) {
        if (relatesToUser || service.includes('talk') || service.includes('nextcloud') || msg.includes('talk') || msg.includes('nextcloud')) {
          return true;
        }
      }

      return false;
    });
  }, [logs, user]);

  const { data: workspaces = [] } = useQuery<Workspace[]>({
    queryKey: ['workspaces'],
    queryFn: () => api.getWorkspaces(),
    retry: 1,
  });

  const { data: activeMissions = [], isLoading: ravenLoading } = useRavenMissions();

  const serviceSummaries = useMemo<ServiceSummary[]>(() => {
    const services = health?.services || {};
    const detailsMap = health?.service_details || {};
    return Object.entries(services).map(([key, status]) => {
      const Icon = SERVICE_ICON_MAP[key as keyof typeof SERVICE_ICON_MAP] ?? Activity;
      const relatedLogs = logs.filter((log) => log.service === key).slice(0, 2);
      const meta = detailsMap[key];
      const gitSha = meta?.git_sha || 'unknown';
      const lastRestart = meta?.start_time
        ? new Date(meta.start_time * 1000).toLocaleString()
        : 'unknown';

      return {
        key,
        label: key.replace(/_/g, ' '),
        icon: Icon,
        status,
        details: [
          { label: 'Status', value: status },
          { label: 'Git Revision', value: gitSha },
          { label: 'Last Restart', value: lastRestart },
          { label: 'Recent Logs', value: String(relatedLogs.length) },
          { label: 'Updated', value: new Date().toLocaleTimeString() },
        ],
      };
    });
  }, [health, logs]);

  const handleSearch = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!searchQuery.trim()) return;
  };

  const handleClearSearch = useCallback(() => {
    setSearchQuery('');
    clearSearch();
  }, [clearSearch]);

  const overallHealthOk = !health || health.status === 'READY';
  const unhealthyCount = serviceSummaries.filter((s) => s.status !== 'READY').length;

  return (
    <div className="space-y-6 md:space-y-8 pb-12 animate-fade-up">
      <HaloBanner userId={user?.id?.toString()} />

      {/* ── Header ── */}
      <header className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
        <div>
          <h1 className="text-2xl md:text-3xl font-bold tracking-tight text-white">
            Jarvis Dashboard
          </h1>
          <p className="mt-1 text-sm text-slate-400">
            Welcome back,{' '}
            <span className="font-semibold text-purple-300">
              {user?.full_name || user?.username || 'User'}
            </span>
          </p>
          {serviceInfo && serviceInfo.git_sha !== 'unknown' && (
            <p className="mt-0.5 text-[10px] font-mono text-slate-700">
              {serviceInfo.service} · {serviceInfo.git_sha} · {serviceInfo.git_branch}
            </p>
          )}
        </div>

        <div className="flex items-center gap-2.5 w-full md:w-auto md:max-w-lg">
          <form onSubmit={handleSearch} className="relative flex-1">
            <Search
              className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-500"
              size={16}
            />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search RAG context and indexed files…"
              className="glass-input w-full py-2.5 pl-10 pr-14 text-sm"
              aria-label="Global search"
            />
            <button
              type="submit"
              aria-label="Submit search"
              disabled={isSearching}
              className="absolute right-2 top-1/2 -translate-y-1/2 glass-button px-3 py-1.5 text-xs font-bold min-h-0 min-w-0 h-8"
            >
              {isSearching ? <Loader2 size={13} className="animate-spin" /> : <ArrowUpRight size={14} />}
            </button>
          </form>

          <button
            onClick={() => { trigger('medium'); setVoiceOpen(true); }}
            className="flex items-center justify-center w-10 h-10 rounded-xl bg-purple-500/15 border border-purple-500/25 text-purple-400 hover:bg-purple-500/25 transition-colors shrink-0"
            aria-label="Open voice assistant"
          >
            <Mic size={18} />
          </button>
        </div>
      </header>

      {/* ── System health banner (only shown when degraded) ── */}
      {!overallHealthOk && unhealthyCount > 0 && (
        <div className="glass-panel px-5 py-4 border-amber-500/20 bg-amber-950/10 flex items-center gap-3">
          <AlertTriangle size={18} className="text-amber-400 shrink-0" />
          <p className="text-sm text-amber-300">
            <span className="font-semibold">{unhealthyCount} service{unhealthyCount !== 1 ? 's' : ''} degraded.</span>
            {' '}The system may have limited functionality.
          </p>
        </div>
      )}

      {/* ── Widget Grid (BentoBox) ── */}
      <SectionErrorBoundary label="Widget Grid">
        <section>
          <BentoBoxDashboard />
        </section>
      </SectionErrorBoundary>

      {/* ── Search Results ── */}
      {searchResults && (
        <SectionErrorBoundary label="Search Results">
          <section className="glass-panel p-6 border-indigo-500/20 bg-indigo-950/5">
            <div className="flex items-center justify-between mb-4">
              <h2 className="font-bold text-indigo-300 flex items-center gap-2">
                <Search size={15} />
                Live Search Result
              </h2>
              <button
                onClick={handleClearSearch}
                aria-label="Close search results"
                className="p-1.5 rounded-lg text-slate-500 hover:text-white hover:bg-white/5 transition-colors"
              >
                <X size={14} />
              </button>
            </div>

            {searchError && !searchResults.answer && (!searchResults.files || searchResults.files.length === 0) ? (
              <div className="rounded-xl border border-indigo-500/20 bg-indigo-500/5 p-4 text-sm text-indigo-300">
                {searchError}
              </div>
            ) : (
              <>
                {searchResults.answer && (
                  <div className="rounded-xl border border-white/5 bg-black/20 p-4 text-sm leading-relaxed text-slate-300 mb-4">
                    {searchResults.answer}
                  </div>
                )}
                <div className="grid gap-3 md:grid-cols-2">
                  {(searchResults.files || []).map((file) => (
                    <div key={file.path} className="glass-card flex items-center gap-3 p-4">
                      <FileText size={15} className="text-blue-400 shrink-0" />
                      <div className="min-w-0">
                        <p className="truncate text-sm font-semibold text-white">{file.name}</p>
                        <p className="truncate text-xs text-slate-500">{file.path}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </>
            )}
          </section>
        </SectionErrorBoundary>
      )}

      {/* ── Live Service Status ── */}
      <SectionErrorBoundary label="Service Status">
        <section>
          <div className="mb-5 flex items-center justify-between">
            <h2 className="flex items-center gap-2 text-lg font-bold text-white">
              <Activity size={18} className="text-purple-400" />
              Live Service Status
            </h2>
            <span className={`text-[10px] font-black uppercase tracking-widest px-2.5 py-1 rounded-lg border ${
              overallHealthOk
                ? 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20'
                : 'text-amber-400 bg-amber-500/10 border-amber-500/20'
            }`}>
              {health?.status || 'Loading…'}
            </span>
          </div>

          {serviceSummaries.length === 0 ? (
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="skeleton h-24 rounded-xl" />
              ))}
            </div>
          ) : (
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              {serviceSummaries.map((service) => (
                <ServiceCard
                  key={service.key}
                  service={service}
                  onClick={() => { trigger('light'); setSelectedService(service); }}
                />
              ))}
            </div>
          )}
        </section>
      </SectionErrorBoundary>

      {/* ── Logs + Workspaces + Settings + Raven ── */}
      <div className="grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
        {/* Recent Activity */}
        <SectionErrorBoundary label="Recent Activity">
          <section className="glass-panel p-6">
            <div className="mb-5 flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="flex items-center justify-center w-9 h-9 rounded-xl bg-blue-500/10">
                  <FileText size={16} className="text-blue-300" />
                </div>
                <div>
                  <h2 className="text-base font-bold text-white">Recent Activity</h2>
                  <p className="text-xs text-slate-500">Live log stream</p>
                </div>
              </div>
              <button
                onClick={() =>
                  api.clearLogs().then(() => {
                    queryClient.invalidateQueries({ queryKey: ['recent-logs'] });
                    queryClient.invalidateQueries({ queryKey: ['header-notifications'] });
                    toast.success('Logs cleared');
                  }).catch(() => toast.error('Failed to clear logs'))
                }
                className="text-[10px] font-bold uppercase tracking-widest text-red-400 hover:text-red-300 px-3 py-1.5 rounded-lg border border-red-500/20 hover:bg-red-500/10 transition-colors"
              >
                Clear
              </button>
            </div>

            <div className="space-y-2">
              {filteredLogs.length > 0 ? (
                filteredLogs.slice(0, 8).map((log, index) => (
                  <LogEntryCard key={`${log.timestamp}-${index}`} log={log} />
                ))
              ) : (
                <div className="flex flex-col items-center justify-center py-10 text-center">
                  <FileText size={28} className="text-slate-700 mb-2" />
                  <p className="text-sm text-slate-500">No recent activity</p>
                </div>
              )}
            </div>
          </section>
        </SectionErrorBoundary>

        {/* Right column */}
        <div className="space-y-6">
          {/* Workspaces */}
          <SectionErrorBoundary label="Workspaces">
            <section className="glass-panel p-6">
              <div className="mb-5 flex items-center gap-3">
                <div className="flex items-center justify-center w-9 h-9 rounded-xl bg-emerald-500/10">
                  <FolderKanban size={16} className="text-emerald-300" />
                </div>
                <div>
                  <h2 className="text-base font-bold text-white">Workspaces</h2>
                  <p className="text-xs text-slate-500">Live registry</p>
                </div>
              </div>
              <div className="space-y-2">
                {Array.isArray(workspaces) && workspaces.length > 0 ? (
                  workspaces.map((workspace) => (
                    <WorkspaceCard key={workspace.id} workspace={workspace} />
                  ))
                ) : (
                  <div className="flex flex-col items-center justify-center py-8 text-center">
                    <FolderKanban size={28} className="text-slate-700 mb-2" />
                    <p className="text-sm text-slate-500">No workspaces registered</p>
                  </div>
                )}
              </div>
            </section>
          </SectionErrorBoundary>

          {/* Raven Status (admin only) */}
          {user?.is_admin && (
            <SectionErrorBoundary label="Raven Status">
              <section className="glass-panel p-6 border-l-2 border-l-purple-500/40">
                <div className="mb-5 flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <div className="flex items-center justify-center w-9 h-9 rounded-xl bg-purple-500/10">
                      <Brain size={16} className="text-purple-400" />
                    </div>
                    <div>
                      <h2 className="text-base font-bold text-white">Raven</h2>
                      <p className="text-xs text-slate-500">Autonomous missions</p>
                    </div>
                  </div>
                  <span className={`text-[10px] font-black uppercase tracking-widest px-2.5 py-1 rounded-lg border ${
                    ravenLoading
                      ? 'text-yellow-400 bg-yellow-500/10 border-yellow-500/20'
                      : activeMissions.length > 0
                        ? 'text-orange-400 bg-orange-500/10 border-orange-500/20'
                        : 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20'
                  }`}>
                    {ravenLoading ? 'Loading' : activeMissions.length > 0 ? `${activeMissions.length} Active` : 'Idle'}
                  </span>
                </div>

                <div className="space-y-2">
                  {ravenLoading ? (
                    <div className="flex items-center justify-center py-8">
                      <Loader2 size={22} className="animate-spin text-purple-400" />
                    </div>
                  ) : activeMissions.length > 0 ? (
                    activeMissions.slice(0, 3).map((mission) => (
                      <div key={mission.id} className="glass-card p-4 space-y-2">
                        <div className="flex items-center justify-between">
                          <span className="text-[10px] font-black uppercase tracking-widest text-purple-400">
                            Mission #{mission.id}
                          </span>
                          <span className={`text-[10px] font-black uppercase tracking-widest ${
                            mission.status === 'running' ? 'text-orange-400' :
                            mission.status === 'queued' ? 'text-yellow-400' :
                            'text-blue-400'
                          }`}>
                            {mission.status}
                          </span>
                        </div>
                        <p className="text-xs text-white truncate">{mission.proposed_mission}</p>
                        {mission.error_summary && (
                          <p className="text-[10px] text-red-400 truncate">{mission.error_summary}</p>
                        )}
                        {mission.progress > 0 && (
                          <div className="w-full bg-white/5 rounded-full h-1 mt-1">
                            <div
                              className="bg-gradient-to-r from-purple-600 to-purple-400 h-1 rounded-full transition-all duration-700"
                              style={{ width: `${Math.min(mission.progress, 100)}%` }}
                            />
                          </div>
                        )}
                      </div>
                    ))
                  ) : (
                    <div className="flex flex-col items-center justify-center py-8 text-center">
                      <Brain size={30} className="text-slate-700 mb-2" />
                      <p className="text-sm text-slate-500">Raven is idle</p>
                      <p className="text-xs text-slate-600 mt-1">Launch missions from the Lab</p>
                    </div>
                  )}
                </div>
              </section>
            </SectionErrorBoundary>
          )}
        </div>
      </div>

      {/* ── Service Detail Modal ── */}
      <Modal isOpen={Boolean(selectedService)} onClose={() => setSelectedService(null)} title={selectedService?.label}>
        <div className="space-y-3">
          {selectedService?.details.map((detail) => (
            <div key={detail.label} className="glass-card p-4">
              <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">{detail.label}</p>
              <p className="mt-1.5 text-sm text-white font-mono">{detail.value}</p>
            </div>
          ))}
        </div>
      </Modal>

      {/* ── Voice Assistant ── */}
      <VoiceAssistantOverlay
        isOpen={voiceOpen}
        onClose={() => setVoiceOpen(false)}
        userId={user?.id?.toString()}
        onCommand={(transcript) => {
          setSearchQuery(transcript);
        }}
      />
    </div>
  );
};

export default Dashboard;