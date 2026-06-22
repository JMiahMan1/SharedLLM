import { useMemo, useState, useCallback, useEffect } from 'react';
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
  Settings2,
  Shield,
  X,
  Mic,
} from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../services/api';
import type { GlobalSetting, HealthStatus, LogEntry, RavenMission, Workspace } from '../services/api';
import { useAuth } from '../context/AuthContext';
import { useHaptics } from '../hooks/useHaptics';
import HaloBanner from '../components/presence/HaloBanner';
import VoiceAssistantOverlay from '../components/voice/VoiceAssistantOverlay';
import Modal from '../components/ui/Modal';
import BentoBoxDashboard from '../components/dashboard/BentoBoxDashboard';

type SearchResult = {
  answer?: string;
  files?: { name: string; path: string }[];
};

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

const Dashboard = () => {
  const queryClient = useQueryClient();
  const [selectedService, setSelectedService] = useState<ServiceSummary | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [isSearching, setIsSearching] = useState(false);
  const [searchResults, setSearchResults] = useState<SearchResult | null>(null);
  const { user } = useAuth();
  const { trigger } = useHaptics();
  const [voiceOpen, setVoiceOpen] = useState(false);

  const { data: health } = useQuery<HealthStatus>({
    queryKey: ['health'],
    queryFn: () => api.getHealth(),
    refetchInterval: 5000,
  });

  const { data: serviceInfo } = useQuery<{ service: string; version: string; git_sha: string; git_branch: string } | null>({
    queryKey: ['service-info'],
    queryFn: () => api.getInfo(),
    refetchOnWindowFocus: false,
  });

  const { data: logs = [] } = useQuery<LogEntry[]>({
    queryKey: ['recent-logs'],
    queryFn: () => api.getLogs(8),
    refetchInterval: 10000,
  });

  const { data: workspaces = [] } = useQuery<Workspace[]>({
    queryKey: ['workspaces'],
    queryFn: () => api.getWorkspaces(),
  });

  const { data: settings = [] } = useQuery<GlobalSetting[]>({
    queryKey: ['settings'],
    queryFn: () => api.getSettings(),
  });

  const [ravenMissions, setRavenMissions] = useState<RavenMission[]>([]);
  const [ravenLoading, setRavenLoading] = useState(true);

  const fetchRavenMissions = useCallback(async () => {
    try {
      const resp = await api.getUserMissions();
      const missions = Array.isArray(resp) ? resp : [];
      const active = missions.filter((m: RavenMission) =>
        ['queued', 'running', 'paused'].includes(m.status)
      );
      setRavenMissions(active);
    } catch {
      setRavenMissions([]);
    } finally {
      setRavenLoading(false);
    }
  }, []);

  useEffect(() => {
    // Defer initial fetch to avoid synchronous setState in effect
    const timeoutId = setTimeout(() => fetchRavenMissions(), 0);
    const intervalId = setInterval(fetchRavenMissions, 30000);
    return () => {
      clearTimeout(timeoutId);
      clearInterval(intervalId);
    };
  }, [fetchRavenMissions]);

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
    if (!searchQuery.trim()) {
      return;
    }
    setIsSearching(true);
    try {
      const results = await api.globalSearch(searchQuery) as SearchResult;
      setSearchResults(results);
    } catch {
      toast.error('Search failed');
    } finally {
      setIsSearching(false);
    }
  };

  return (
    <div className="space-y-6 md:space-y-8 pb-12">
      <HaloBanner userId={user?.id?.toString()} />

      <header className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
        <div>
          <h2 className="text-2xl md:text-3xl font-bold tracking-tight text-white">Jarvis Dashboard</h2>
          <p className="mt-1 text-sm text-slate-400">
            Welcome back, <span className="font-bold text-purple-400">{user?.full_name || user?.username}</span>
          </p>
          {serviceInfo && serviceInfo.git_sha !== 'unknown' && (
            <p className="mt-1 text-[10px] font-mono text-slate-600">
              {serviceInfo.service} · {serviceInfo.git_sha} · {serviceInfo.git_branch}
            </p>
          )}
        </div>

        <div className="flex items-center gap-3 w-full">
          <form onSubmit={handleSearch} className="relative flex-1">
          <Search className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-slate-500" size={18} />
          <input
            type="text"
            value={searchQuery}
            onChange={(event) => setSearchQuery(event.target.value)}
            placeholder="Search live RAG context and indexed files"
            className="glass-input w-full py-3 pl-12 pr-12"
          />
          <button type="submit" aria-label="Submit search" disabled={isSearching} className="absolute right-2 top-1.5 glass-button px-4 py-1.5 text-xs font-bold">
            {isSearching ? '...' : <ArrowUpRight size={16} />}
          </button>
        </form>

        <button
          onClick={() => { trigger('medium'); setVoiceOpen(true); }}
          className="p-3 rounded-xl bg-purple-500/20 border border-purple-500/30 text-purple-400 hover:bg-purple-500/30 transition-colors shrink-0"
          aria-label="Voice command"
        >
          <Mic size={20} />
        </button>
      </div>
      </header>

      <section>
        <BentoBoxDashboard />
      </section>

      {searchResults && (
        <section className="glass-panel space-y-4 border-indigo-500/20 bg-indigo-500/5 p-6">
          <div className="flex items-center justify-between">
            <h3 className="font-bold text-indigo-300">Live Search Result</h3>
            <button onClick={() => setSearchResults(null)} aria-label="Close search" className="text-slate-500 hover:text-white">
              <X size={16} />
            </button>
          </div>
          {searchResults.answer && (
            <div className="rounded-xl border border-white/5 bg-black/20 p-4 text-sm leading-relaxed text-slate-300">
              {searchResults.answer}
            </div>
          )}
          <div className="grid gap-3 md:grid-cols-2">
            {(searchResults.files || []).map((file) => (
              <div key={file.path} className="glass-card flex items-center gap-3 p-4">
                <FileText size={16} className="text-blue-300" />
                <div className="min-w-0">
                  <p className="truncate text-sm font-semibold text-white">{file.name}</p>
                  <p className="truncate text-xs text-slate-500">{file.path}</p>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      <section>
        <div className="mb-6 flex items-center justify-between">
          <h3 className="flex items-center gap-2 text-xl font-bold text-white">
            <Activity size={20} className="text-purple-400" />
            Live Service Status
          </h3>
          <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">
            {health?.status || 'UNKNOWN'}
          </span>
        </div>

        <div className="grid gap-6 md:grid-cols-2 xl:grid-cols-4">
          {serviceSummaries.map((service) => (
            <button
              key={service.key}
              onClick={() => { trigger('light'); setSelectedService(service); }}
              className="glass-card flex flex-col gap-4 p-6 text-left transition hover:border-purple-500/30"
            >
              <div className="flex items-center justify-between">
                <div className="rounded-xl bg-white/5 p-3">
                  <service.icon size={20} className="text-purple-300" />
                </div>
                <span className={`text-[10px] font-black uppercase tracking-widest ${service.status === 'OK' ? 'text-emerald-300' : 'text-red-300'}`}>
                  {service.status}
                </span>
              </div>
              <div>
                <p className="font-semibold capitalize text-white">{service.label}</p>
                <p className="mt-1 text-xs text-slate-400">Derived from `/health/ready` and recent logs.</p>
              </div>
            </button>
          ))}
        </div>
      </section>

      <div className="grid gap-8 xl:grid-cols-[1.1fr_0.9fr]">
        <section className="glass-panel p-6">
          <div className="mb-6 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <FileText size={20} className="text-blue-300" />
              <div>
                <h3 className="text-xl font-bold text-white">Recent Activity</h3>
                <p className="text-sm text-slate-400">Actual log entries from the logging service.</p>
              </div>
            </div>
            <button 
              onClick={() => api.clearLogs().then(() => {
                queryClient.invalidateQueries({ queryKey: ['recent-logs'] });
                queryClient.invalidateQueries({ queryKey: ['header-notifications'] });
                toast.success('Logs cleared');
              })}
              className="glass-button px-4 py-2 text-[10px] font-black uppercase tracking-widest text-red-400 hover:bg-red-500/10"
            >
              Clear Logs
            </button>
          </div>
          <div className="space-y-3">
            {logs.map((log, index) => (
              <div key={`${log.timestamp}-${index}`} className="glass-card p-4">
                <div className="flex items-center justify-between gap-4 overflow-hidden">
                  <p className="font-semibold text-white truncate">{log.service}</p>
                  <span className="text-[10px] uppercase tracking-widest text-slate-500 shrink-0">{log.level}</span>
                </div>
                <p className="mt-2 text-sm text-slate-300 break-words">{log.message}</p>
                <p className="mt-2 text-xs text-slate-500">{new Date(log.timestamp).toLocaleString()}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="space-y-8">
          <div className="glass-panel p-6">
            <div className="mb-6 flex items-center gap-3">
              <FolderKanban size={20} className="text-emerald-300" />
              <div>
                <h3 className="text-xl font-bold text-white">Workspaces</h3>
                <p className="text-sm text-slate-400">Live workspace registry from workspace runtime.</p>
              </div>
            </div>
            <div className="space-y-3">
              {Array.isArray(workspaces) && workspaces.length > 0 ? (
                workspaces.map((workspace) => (
                  <div key={workspace.id} className="glass-card p-4 transition-all hover:border-emerald-500/20">
                    <div className="flex items-center justify-between gap-4 overflow-hidden">
                      <p className="font-semibold text-white truncate">{workspace.display_name || workspace.id}</p>
                      <span className={`text-[10px] font-black uppercase tracking-widest shrink-0 px-2 py-1 rounded-md ${workspace.available ? 'bg-emerald-500/10 text-emerald-300 border border-emerald-500/20' : 'bg-red-500/10 text-red-300 border border-red-500/20'}`}>
                        {workspace.available ? 'Online' : 'Unavailable'}
                      </span>
                    </div>
                    <p className="mt-2 font-mono text-[10px] text-slate-500 break-all bg-black/20 p-2 rounded">
                      {workspace.resolved_path || 'Path resolution failed'}
                    </p>
                    {!workspace.available && (
                      <p className="mt-2 text-[10px] text-red-400/70 italic">
                        Check workspace mount or local_path configuration.
                      </p>
                    )}
                  </div>
                ))
              ) : (
                <div className="text-center py-8 glass-card border-dashed border-white/5">
                  <p className="text-sm text-slate-500">No workspaces registered</p>
                </div>
              )}
            </div>
          </div>

          <div className="glass-panel p-6">
            <div className="mb-6 flex items-center gap-3">
              <Settings2 size={20} className="text-orange-300" />
              <div>
                <h3 className="text-xl font-bold text-white">System Settings</h3>
                <p className="text-sm text-slate-400">Live settings currently exposed by identity.</p>
              </div>
            </div>
            <div className="space-y-3">
              {settings
                .filter((s) => !['assistant_model', 'coding_model', 'librarian_model'].includes(s.key))
                .map((setting) => (
                <div className="overflow-hidden">
                  <p className="font-mono text-sm text-white truncate">{setting.key}</p>
                  <p className="mt-2 text-sm text-slate-300 break-words">{setting.value}</p>
                  {setting.description && (
                    <p className="mt-2 text-xs text-slate-500 italic">{setting.description}</p>
                  )}
                </div>
              ))}
            </div>
          </div>

          {user?.is_admin && (
            <div className="glass-panel p-6 border-l-4 border-l-purple-500">
              <div className="mb-4 flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <Brain size={20} className="text-purple-400" />
                  <div>
                    <h3 className="text-xl font-bold text-white">Raven Status</h3>
                    <p className="text-sm text-slate-400">Autonomous mission monitoring</p>
                  </div>
                </div>
                <span className={`text-[10px] font-black uppercase tracking-widest ${ravenLoading ? 'text-yellow-400' : ravenMissions.length > 0 ? 'text-orange-400' : 'text-emerald-400'}`}>
                  {ravenLoading ? 'Loading...' : ravenMissions.length > 0 ? 'Active' : 'Idle'}
                </span>
              </div>
              <div className="space-y-3">
                {ravenLoading ? (
                  <div className="flex items-center justify-center py-8">
                    <Loader2 size={24} className="animate-spin text-purple-400" />
                  </div>
                ) : ravenMissions.length > 0 ? (
                  ravenMissions.slice(0, 3).map((mission) => (
                    <div key={mission.id} className="glass-card p-4 space-y-2">
                      <div className="flex items-center justify-between">
                        <span className="text-[10px] font-black uppercase tracking-widest text-purple-400">Mission #{mission.id}</span>
                        <span className={`text-[10px] font-black uppercase tracking-widest ${
                          mission.status === 'running' ? 'text-orange-400' :
                          mission.status === 'queued' ? 'text-yellow-400' :
                          'text-blue-400'
                        }`}>
                          {mission.status}
                        </span>
                      </div>
                      <p className="text-sm text-white truncate">{mission.proposed_mission}</p>
                      {mission.error_summary && (
                        <p className="text-[10px] text-red-400 truncate">{mission.error_summary}</p>
                      )}
                      {mission.progress > 0 && (
                        <div className="w-full bg-white/10 rounded-full h-1.5 mt-2">
                          <div
                            className="bg-purple-500 h-1.5 rounded-full transition-all duration-500"
                            style={{ width: `${Math.min(mission.progress, 100)}%` }}
                          />
                        </div>
                      )}
                    </div>
                  ))
                ) : (
                  <div className="text-center py-8">
                    <Brain size={32} className="mx-auto text-slate-700 mb-2" />
                    <p className="text-sm text-slate-500">Raven is idle</p>
                    <p className="text-[10px] text-slate-600 mt-1">Ready to launch missions from the Lab</p>
                  </div>
                )}
              </div>
            </div>
          )}
        </section>
      </div>

      <Modal isOpen={Boolean(selectedService)} onClose={() => setSelectedService(null)} title={selectedService?.label}>
        <div className="space-y-4">
          {selectedService?.details.map((detail) => (
            <div key={detail.label} className="glass-card p-4">
              <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">{detail.label}</p>
              <p className="mt-2 text-sm text-white">{detail.value}</p>
            </div>
          ))}
        </div>
      </Modal>

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
