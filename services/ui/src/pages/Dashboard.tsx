import { useMemo, useState, useCallback, useEffect, useRef, Component, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
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
  Settings2,
} from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../services/api';
import type { LogEntry, Workspace, SearchResult } from '../services/api';
import { useAuth } from '../context/AuthContext';
import { Capacitor } from '@capacitor/core';
import { useRavenMissions } from '../hooks/useRavenMissions';
import { useHaptics } from '../hooks/useHaptics';
import { useDebounce } from '../hooks/useDebounce';
import { useServiceInfo, useSystemHealth } from '../hooks/useSystemHealth';
import HaloBanner from '../components/presence/HaloBanner';
import LocationBackgroundNotice from '../components/location/LocationBackgroundNotice';
import { useLocation } from '../context/LocationContext';
import VoiceAssistantOverlay from '../components/voice/VoiceAssistantOverlay';
import Modal from '../components/ui/Modal';
import BentoBoxDashboard from '../components/dashboard/BentoBoxDashboard';
import DashboardSettingsPanel from '../components/dashboard/DashboardSettingsPanel';
import { useWidgetStore } from '../stores/widgetStore';
import { useClearedAt, useDismissed, newerThan } from '../lib/clearedAt';
import SwipeToDismiss from '../components/ui/SwipeToDismiss';
import { relativeTime } from '../lib/wanderTrips';

// Per-section Error Boundary

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

// Types

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

// Service Status Card

const ServiceCard = ({ service, onClick }: { service: ServiceSummary; onClick: () => void }) => {
  const Icon = service.icon;
  const isOk = service.status === 'OK';

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
        Revision · last restart
      </p>
    </button>
  );
};

// Log Entry Card

const LOG_LEVEL_COLORS: Record<string, string> = {
  error: 'text-red-400',
  warning: 'text-amber-400',
  warn: 'text-amber-400',
  info: 'text-blue-400',
  debug: 'text-slate-500',
};

const PROBLEMS_CLEARED_KEY = 'jarvis_problems_cleared_at';
const PROBLEMS_DISMISSED_KEY = 'jarvis_problems_dismissed';
/** Log entries have no stable id; this names one well enough to hide it. */
const problemKey = (log: LogEntry) => `${log.timestamp}|${log.service}|${log.message}`;

/** The newest timestamp in a list, so a clear hides exactly what was shown
 *  even when this device's clock and the server's disagree. */
function newestStamp(logs: LogEntry[]): string {
  const times = logs.map((l) => new Date(l.timestamp).getTime()).filter(Number.isFinite);
  return times.length ? new Date(Math.max(...times)).toISOString() : new Date().toISOString();
}

/** Uvicorn access lines ('INFO: 1.2.3.4:5 - "GET /x HTTP/1.1" 500 Internal
 *  Server Error') shown as 'GET /x: 500 Internal Server Error'. */
function readableLogMessage(message: string): string {
  const m = /"([A-Z]+) (\S+) HTTP\/[\d.]+" (\d{3})\s*(.*)$/.exec(message);
  return m ? `${m[1]} ${m[2]}: ${m[3]}${m[4] ? ` ${m[4]}` : ''}` : message;
}

const LogEntryCard = ({ log }: { log: LogEntry }) => {
  // The logging service stores levels upper-case ("ERROR"); compare folded.
  const level = (log.level || '').toLowerCase();
  return (
  <div className="flex items-start gap-3 pl-4 pr-10 py-3 rounded-xl bg-black/20 border border-white/5 hover:bg-black/30 transition-colors">
    <div
      className={`w-1.5 h-1.5 rounded-full mt-1.5 shrink-0 ${
        level === 'error' ? 'bg-red-500' :
        (level === 'warning' || level === 'warn') ? 'bg-amber-500' :
        'bg-blue-500/50'
      }`}
    />
    <div className="min-w-0 flex-1">
      <div className="flex items-center justify-between gap-2 mb-0.5">
        <p className="text-xs font-semibold text-slate-300 capitalize truncate min-w-0">{log.service}</p>
        <span className={`text-[9px] font-bold uppercase tracking-widest shrink-0 ${LOG_LEVEL_COLORS[level] ?? 'text-slate-500'}`}>
          {log.level}
        </span>
      </div>
      <p className="text-xs text-slate-400 leading-relaxed break-words">{readableLogMessage(log.message)}</p>
      <p className="text-[10px] text-slate-500 mt-1" title={new Date(log.timestamp).toLocaleString()}>{relativeTime(log.timestamp)}</p>
    </div>
  </div>
  );
};

// Workspace Card

const WorkspaceCard = ({ workspace }: { workspace: Workspace }) => (
  <div className="flex items-start gap-3 p-4 rounded-xl bg-black/20 border border-white/5 hover:border-emerald-500/15 transition-all">
    <div className={`w-2 h-2 rounded-full mt-1.5 shrink-0 ${workspace.available ? 'bg-emerald-400' : 'bg-red-400'}`} />
    <div className="min-w-0 flex-1">
      <div className="flex items-center justify-between gap-2">
        <p className="font-semibold text-sm text-white truncate min-w-0">
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

// Search hook

interface SearchResponse {
  query: string;
  results: SearchResult | null;
  error: string | null;
}

function useSearch(query: string) {
  const trimmed = query.trim();
  // Responses are tagged with the query that produced them, so staleness is
  // resolved while rendering rather than by resetting state from an effect.
  const [response, setResponse] = useState<SearchResponse | null>(null);
  const [pendingQuery, setPendingQuery] = useState<string | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    // Always drop the in-flight request first: a cancelled search must never
    // land after the box was cleared.
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;

    if (!trimmed) return;

    const controller = new AbortController();
    abortControllerRef.current = controller;
    let active = true;

    const performSearch = async () => {
      setPendingQuery(trimmed);

      try {
        const data = await api.globalSearch(trimmed, controller.signal);
        if (!active) return;
        // The gateway answers a miss with a placeholder sentence and no
        // files, so "no files" is the empty signal, not an empty answer.
        const empty = !data.files || data.files.length === 0;
        setResponse({
          query: trimmed,
          results: empty ? null : data,
          error: empty ? 'No results found' : null,
        });
      } catch {
        // A cancelled request is expected while typing; only real failures speak up.
        if (!active || controller.signal.aborted) return;
        toast.error('Search failed');
        setResponse({ query: trimmed, results: null, error: 'Search failed. Please try again.' });
      } finally {
        if (active) setPendingQuery(null);
      }
    };

    void performSearch();

    return () => {
      active = false;
      controller.abort();
    };
  }, [trimmed]);

  const fresh = response && response.query === trimmed ? response : null;

  const clear = useCallback(() => {
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
    setResponse(null);
    setPendingQuery(null);
  }, []);

  return {
    results: fresh?.results ?? null,
    error: fresh?.error ?? null,
    isSearching: pendingQuery !== null && pendingQuery === trimmed,
    clear,
  };
}

// Dashboard

const Dashboard = () => {
  const [selectedService, setSelectedService] = useState<ServiceSummary | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const { user } = useAuth();
  const { isTracking } = useLocation();
  const { trigger } = useHaptics();
  const isMobile = Capacitor.isNativePlatform();
  const isAdmin = user?.is_admin;
  // The widget store owns the `adminOnly` gate, so it needs the role. This
  // component is the single place that has it and renders both consumers
  // (BentoBoxDashboard and DashboardSettingsPanel).
  const setAdminStatus = useWidgetStore((state) => state.setAdminStatus);
  useEffect(() => {
    setAdminStatus(Boolean(isAdmin));
  }, [isAdmin, setAdminStatus]);
  // On mobile, normal users get a streamlined dashboard: the full Live Service
  // Status / Recent Activity / Workspaces sections are hidden (admins keep
  // them). Workspaces are still reachable via the toggleable Workspaces widget.
  const showFullSections = !isMobile || isAdmin;
  const [voiceOpen, setVoiceOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);

  const debouncedSearch = useDebounce(searchQuery, 300);
  const { results: searchResults, error: searchError, isSearching, clear: clearSearch } = useSearch(debouncedSearch);

  const { data: health } = useSystemHealth();
  const { data: serviceInfo } = useServiceInfo();

  // Admin only (the log is the whole system's). The central log is mostly the
  // gateway's request lines, so the panel asks for warnings and errors only
  // (failed requests) instead of keyword-filtering lines that never matched.
  const { data: allProblems = [] } = useQuery<LogEntry[]>({
    queryKey: ['recent-logs', 'problems'],
    queryFn: () => api.getLogs(20, ['warning', 'error']),
    refetchInterval: 30000,
    retry: 1,
    enabled: !!isAdmin,
  });
  // Clearing hides what is listed now; the system log itself is untouched.
  const [problemsClearedAt, clearProblems] = useClearedAt(PROBLEMS_CLEARED_KEY);
  const [dismissedProblems, dismissProblem] = useDismissed(PROBLEMS_DISMISSED_KEY);
  const problems = newerThan(allProblems, problemsClearedAt, (log) => log.timestamp)
    .filter((log) => !dismissedProblems.has(problemKey(log)));

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
        ],
      };
    });
  }, [health]);

  // Search runs as you type; Enter only dismisses the phone keyboard.
  const handleSearch = (event: React.FormEvent) => {
    event.preventDefault();
    (document.activeElement as HTMLElement | null)?.blur();
  };

  const handleClearSearch = useCallback(() => {
    setSearchQuery('');
    clearSearch();
  }, [clearSearch]);

  const overallHealthOk = !health || health.status === 'READY';
  const unhealthyCount = serviceSummaries.filter((s) => s.status !== 'OK').length;

  return (
    <div className="space-y-6 md:space-y-8 pb-12 animate-fade-up">
      <HaloBanner userId={user?.id?.toString()} />
      {/* First thing seen on opening the app: sharing that is on but will not
          survive the app closing is otherwise invisible until someone asks. */}
      {isTracking && <LocationBackgroundNotice />}

      {/* Header */}
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
              {serviceInfo.service} · {serviceInfo.git_sha}
              {serviceInfo.git_branch && serviceInfo.git_branch !== 'unknown' ? ` · ${serviceInfo.git_branch}` : ''}
            </p>
          )}
        </div>

        <div className="flex items-center gap-2.5 w-full md:w-auto md:max-w-lg">
          <form onSubmit={handleSearch} className="relative flex-1 min-w-0">
            <Search
              className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-500"
              size={16}
            />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search knowledge & files…"
              className="glass-input w-full py-2.5 pl-10 pr-14 text-sm"
              enterKeyHint="search"
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
            className="flex items-center justify-center w-11 h-11 rounded-xl bg-purple-500/15 border border-purple-500/25 text-purple-400 hover:bg-purple-500/25 transition-colors shrink-0"
            aria-label="Open voice assistant"
          >
            <Mic size={18} />
          </button>

          <button
            onClick={() => setSettingsOpen(true)}
            className="flex items-center justify-center w-11 h-11 rounded-xl bg-white/5 border border-white/10 text-slate-400 hover:text-white hover:bg-white/10 transition-colors shrink-0"
            aria-label="Dashboard settings"
            title="Dashboard settings"
          >
            <Settings2 size={16} />
          </button>
        </div>
      </header>

      {/* Search Results (anchored directly beneath the search bar) */}
      {(searchResults || searchError) && (
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

            {searchError && !searchResults ? (
              <div className="rounded-xl border border-indigo-500/20 bg-indigo-500/5 p-4 text-sm text-indigo-300">
                {searchError}
              </div>
            ) : searchResults ? (
              <>
                {searchResults.answer && (
                  <div className="rounded-xl border border-white/5 bg-black/20 p-4 text-sm leading-relaxed text-slate-300 mb-4 max-h-60 overflow-y-auto whitespace-pre-wrap break-words">
                    {searchResults.answer}
                  </div>
                )}
                <div className="grid gap-3 md:grid-cols-2">
                  {(searchResults.files || []).map((file, index) => (
                    <div key={`${file.path}-${index}`} className="glass-card flex items-center gap-3 p-4">
                      <FileText size={15} className="text-blue-400 shrink-0" />
                      <div className="min-w-0">
                        <p className="truncate text-sm font-semibold text-white">{file.name}</p>
                        <p className="truncate text-xs text-slate-500">{file.path}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </>
            ) : null}
          </section>
        </SectionErrorBoundary>
      )}

      {/* System health banner (only shown when degraded) */}
      {!overallHealthOk && unhealthyCount > 0 && (
        <div className="glass-panel px-5 py-4 border-amber-500/20 bg-amber-950/10 flex items-center gap-3">
          <AlertTriangle size={18} className="text-amber-400 shrink-0" />
          <p className="text-sm text-amber-300">
            <span className="font-semibold">{unhealthyCount} service{unhealthyCount !== 1 ? 's' : ''} degraded.</span>
            {' '}The system may have limited functionality.
          </p>
        </div>
      )}

      {/* Widget Grid (BentoBox) */}
      <SectionErrorBoundary label="Widget Grid">
        <section>
          <BentoBoxDashboard />
        </section>
      </SectionErrorBoundary>

      {showFullSections && (
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
            <div className="grid grid-cols-2 gap-3 md:gap-4 xl:grid-cols-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="skeleton h-24 rounded-xl" />
              ))}
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-3 md:gap-4 xl:grid-cols-4">
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
      )}

      {/* Logs + Workspaces + Settings + Raven */}
      <div className="grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
        {isAdmin && (
        <SectionErrorBoundary label="Recent Problems">
          <section className="glass-panel p-6">
            <div className="mb-5 flex items-center justify-between gap-3">
              <div className="flex items-center gap-3 min-w-0">
                <div className="flex items-center justify-center w-9 h-9 rounded-xl bg-amber-500/10 shrink-0">
                  <AlertTriangle size={16} className="text-amber-300" />
                </div>
                <div className="min-w-0">
                  <h2 className="text-base font-bold text-white">Recent Problems</h2>
                  <p className="text-xs text-slate-500">Failed requests from the system log</p>
                </div>
              </div>
              {problems.length > 0 && (
                <button
                  onClick={() => clearProblems(newestStamp(problems))}
                  className="min-h-11 shrink-0 text-[10px] font-bold uppercase tracking-widest text-slate-400 hover:text-white px-3 rounded-lg border border-white/10 hover:bg-white/5 transition-colors"
                >
                  Clear
                </button>
              )}
            </div>

            <div className="space-y-2">
              {problems.length > 0 ? (
                problems.slice(0, 8).map((log, index) => (
                  <SwipeToDismiss key={`${log.timestamp}-${index}`} label={`${log.service} ${log.level}`} onDismiss={() => dismissProblem(problemKey(log))}>
                    <LogEntryCard log={log} />
                  </SwipeToDismiss>
                ))
              ) : (
                <div className="flex flex-col items-center justify-center py-10 text-center">
                  <CheckCircle2 size={28} className="text-emerald-700 mb-2" />
                  <p className="text-sm text-slate-500">No failed requests recently</p>
                </div>
              )}
            </div>
          </section>
        </SectionErrorBoundary>
        )}

        {/* Right column */}
        <div className="space-y-6">
          {/* Workspaces */}
          {showFullSections && (
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
          )}

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
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-[10px] font-black uppercase tracking-widest text-purple-400 min-w-0 truncate">
                            Mission #{mission.id}
                          </span>
                          <span className={`text-[10px] font-black uppercase tracking-widest shrink-0 ${
                            mission.status === 'running' ? 'text-orange-400' :
                            mission.status === 'queued' ? 'text-yellow-400' :
                            'text-blue-400'
                          }`}>
                            {mission.status}
                          </span>
                        </div>
                        <p className="text-xs text-white truncate min-w-0">{mission.proposed_mission}</p>
                        {mission.error_summary && (
                          <p className="text-[10px] text-red-400 truncate min-w-0">{mission.error_summary}</p>
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

      {/* Service Detail Modal */}
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

      {/* Voice Assistant */}
      <VoiceAssistantOverlay
        isOpen={voiceOpen}
        onClose={() => setVoiceOpen(false)}
        userId={user?.id?.toString()}
        onCommand={(transcript) => {
          setSearchQuery(transcript);
        }}
      />

      {/* Dashboard Settings */}
      <DashboardSettingsPanel
        isOpen={settingsOpen}
        onClose={() => setSettingsOpen(false)}
      />
    </div>
  );
};

export default Dashboard;