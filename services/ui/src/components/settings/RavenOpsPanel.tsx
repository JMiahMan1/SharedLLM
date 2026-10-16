import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Activity, AlertTriangle, Clock, History, Play, PowerOff, Search, ShieldAlert, Square, Terminal, Trash2, Volume2 } from 'lucide-react';
import toast from 'react-hot-toast';
import { api, type RavenConfig, type RavenMission } from '../../services/api';
import { errorText } from '../../lib/errorText';
import HelpTooltip from '../ui/HelpTooltip';
import RavenAuditLog from './RavenAuditLog';
import RavenLiveTrace from './RavenLiveTrace';
import RavenMissionDetail from './RavenMissionDetail';

const SCAN_INTERVALS: [number, string][] = [
  [60, 'Every minute'],
  [300, 'Every 5 minutes'],
  [3600, 'Hourly'],
  [86400, 'Daily'],
];

const ACTIVE = ['running', 'executing', 'queued'];
const btn = 'glass-button min-h-11 px-4 flex items-center justify-center gap-2 text-[10px] font-black uppercase tracking-widest disabled:opacity-50';

const formatDuration = (secs?: number | null) => (secs == null ? '' : `${Math.floor(secs / 60)}m ${secs % 60}s`);

/**
 * Raven's background health watch (the gateway's Raven worker scans container
 * logs and files `admin_fix` missions for an admin to approve) and the
 * missions it and the family have started.
 */
export default function RavenOpsPanel() {
  const queryClient = useQueryClient();
  const [draftConfig, setDraftConfig] = useState<Partial<RavenConfig>>({});
  const [isAuditLogOpen, setIsAuditLogOpen] = useState(false);
  const [liveMissionId, setLiveMissionId] = useState<number | null>(null);
  const [detailId, setDetailId] = useState<number | null>(null);
  const [missionPrompt, setMissionPrompt] = useState('');

  const { data: config, isLoading: configLoading, isError: configError } = useQuery<RavenConfig>({
    queryKey: ['raven-config'],
    queryFn: () => api.getRavenConfig(),
  });

  const { data: missions = [], isLoading: missionsLoading } = useQuery<RavenMission[]>({
    queryKey: ['raven-missions-admin'],
    queryFn: () => api.getAdminRavenQueue(),
    refetchInterval: 5000,
  });

  const { data: voicesRes } = useQuery({ queryKey: ['raven-voices'], queryFn: () => api.getRavenVoices() });
  const voices = voicesRes?.voices || [];

  const refreshMissions = () => queryClient.invalidateQueries({ queryKey: ['raven-missions-admin'] });

  const updateConfigMutation = useMutation({
    mutationFn: (newConfig: Partial<RavenConfig>) => api.updateRavenConfig(newConfig),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['raven-config'] });
      setDraftConfig({});
      toast.success('Raven settings saved');
    },
    onError: (err: unknown) => toast.error(errorText(err, 'Failed to save Raven settings')),
  });

  // Start a mission by hand: the same POST /api/raven/missions the Lab uses.
  const startMissionMutation = useMutation({
    mutationFn: (query: string) => api.createUserMission(query),
    onSuccess: (data) => {
      refreshMissions();
      toast.success(`Mission #${data.mission.id} queued`);
      setMissionPrompt('');
      setLiveMissionId(data.mission.id);
    },
    onError: (err: unknown) => toast.error(errorText(err, 'Could not start the mission')),
  });

  const executeMissionMutation = useMutation({
    mutationFn: (id: number) => api.executeAdminRavenMission(id),
    onSuccess: () => {
      refreshMissions();
      toast.success('Fix queued');
    },
    onError: (err: unknown) => toast.error(errorText(err, 'Failed to queue the fix')),
  });

  const deleteMissionMutation = useMutation({
    mutationFn: (id: number) => api.deleteRavenMission(id),
    onSuccess: () => {
      refreshMissions();
      toast.success('Dismissed');
    },
    onError: (err: unknown) => toast.error(errorText(err, 'Failed to dismiss')),
  });

  const killMissionMutation = useMutation({
    mutationFn: (id: number) => api.killRavenMission(id),
    onSuccess: () => {
      refreshMissions();
      toast.success('Stop requested');
    },
    onError: (err: unknown) => toast.error(errorText(err, 'Failed to stop mission')),
  });

  const downloadModelsMutation = useMutation({
    mutationFn: () => api.downloadRavenModels(),
    onSuccess: (data) => {
      toast.success(`Voice models: ${(data.results ?? []).join(', ') || 'requested'}`);
      queryClient.invalidateQueries({ queryKey: ['raven-voices'] });
    },
    onError: (err: unknown) => toast.error(errorText(err, 'Failed to download voice models')),
  });

  if (configLoading) {
    return <div className="text-slate-400 animate-pulse text-sm">Loading Raven settings…</div>;
  }
  if (configError || !config) {
    return <div className="text-red-400 text-sm">Raven settings could not be loaded.</div>;
  }

  const current = { ...config, ...draftConfig };
  const triage = missions.filter((m) => m.mission_type === 'admin_fix' && m.status === 'pending');
  const active = missions.filter((m) => ACTIVE.includes(m.status));
  const history = missions.filter((m) => m.status === 'completed' || m.status === 'failed').slice(0, 15);
  const scanOptions = SCAN_INTERVALS.some(([v]) => v === current.raven_scan_interval)
    ? SCAN_INTERVALS
    : [...SCAN_INTERVALS, [current.raven_scan_interval, `Every ${current.raven_scan_interval}s`] as [number, string]];

  return (
    <div className="space-y-8">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div>
          <h3 className="flex items-center gap-3 text-xl font-bold text-white">
            <ShieldAlert size={20} className="text-red-400" />
            Raven Mission Control
          </h3>
          <p className="mt-1 text-sm text-slate-400">
            Raven scans the containers&rsquo; logs and proposes a fix when a service keeps erroring. Nothing runs until you approve it here.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => setIsAuditLogOpen(true)} className={`${btn} text-slate-300`}>
            <History size={14} /> Audit log
          </button>
          <HelpTooltip docName="RAVEN_LEARNING_MEMORY.md" sectionTitle="Mission flow" label="Raven Mission Control" />
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <SettingCard icon={<PowerOff size={12} />} title="Health watch" hint="Pause or resume the background log scan.">
          <button
            onClick={() => updateConfigMutation.mutate({ raven_suspended: !current.raven_suspended })}
            disabled={updateConfigMutation.isPending}
            aria-pressed={!current.raven_suspended}
            className={`w-full min-h-11 rounded-xl text-xs font-black uppercase tracking-widest transition ${
              current.raven_suspended
                ? 'bg-red-500/20 text-red-300 border border-red-500/30 hover:bg-red-500/30'
                : 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 hover:bg-emerald-500/30'
            }`}
          >
            {current.raven_suspended ? 'Paused — tap to resume' : 'Watching — tap to pause'}
          </button>
        </SettingCard>

        <SettingCard icon={<Clock size={12} />} title="Scan frequency" hint="How often Raven reads the logs.">
          <select
            value={current.raven_scan_interval}
            aria-label="Scan frequency"
            onChange={(e) => updateConfigMutation.mutate({ raven_scan_interval: parseInt(e.target.value, 10) })}
            disabled={updateConfigMutation.isPending}
            className="glass-input w-full min-h-11 bg-black/30 text-sm"
          >
            {scanOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </SettingCard>

        <SettingCard icon={<AlertTriangle size={12} />} title="Error threshold" hint="Errors in one container's last 100 log lines before Raven proposes a fix.">
          <NumberSetting
            label="Error threshold"
            min={1}
            value={current.raven_error_threshold}
            onChange={(v) => setDraftConfig({ ...draftConfig, raven_error_threshold: v })}
            onSave={() => updateConfigMutation.mutate({ raven_error_threshold: current.raven_error_threshold })}
            dirty={draftConfig.raven_error_threshold != null && draftConfig.raven_error_threshold >= 1}
            busy={updateConfigMutation.isPending}
          />
        </SettingCard>

        <SettingCard icon={<Clock size={12} />} title="Mission time limit" hint="Longest a mission may run, in seconds. 0 means no limit.">
          <NumberSetting
            label="Mission time limit"
            min={0}
            step={60}
            value={current.raven_max_total_seconds ?? 0}
            onChange={(v) => setDraftConfig({ ...draftConfig, raven_max_total_seconds: v })}
            onSave={() => updateConfigMutation.mutate({ raven_max_total_seconds: current.raven_max_total_seconds })}
            dirty={draftConfig.raven_max_total_seconds != null && draftConfig.raven_max_total_seconds >= 0}
            busy={updateConfigMutation.isPending}
          />
        </SettingCard>
      </div>

      <div className="glass-card p-4 border border-white/10 space-y-3">
        <p className="text-[10px] font-black uppercase tracking-widest text-slate-500 flex items-center gap-2">
          <Volume2 size={12} /> Spoken replies (local text-to-speech)
        </p>
        <div className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
          <div>
            <span className="text-[10px] text-slate-500 uppercase font-black mb-1 block">Engine</span>
            <div className="min-h-11 flex items-center text-sm text-white font-mono bg-black/20 px-3 rounded-lg border border-white/5 truncate">
              {current.system_default_tts_engine}
            </div>
          </div>
          <label className="block">
            <span className="text-[10px] text-slate-500 uppercase font-black mb-1 block">Voice</span>
            <select
              value={current.system_default_tts_voice}
              onChange={(e) => updateConfigMutation.mutate({ system_default_tts_voice: e.target.value })}
              disabled={!voices.length || updateConfigMutation.isPending}
              className="glass-input w-full min-h-11 bg-black/30 text-sm"
            >
              {!voices.includes(current.system_default_tts_voice) && (
                <option value={current.system_default_tts_voice}>{current.system_default_tts_voice}</option>
              )}
              {voices.map((v) => <option key={v} value={v}>{v}</option>)}
            </select>
          </label>
          <button
            onClick={() => downloadModelsMutation.mutate()}
            disabled={downloadModelsMutation.isPending}
            className={`${btn} text-blue-300`}
          >
            {downloadModelsMutation.isPending ? 'Downloading…' : 'Download voice models'}
          </button>
        </div>
        {!voices.length && (
          <p className="text-xs text-slate-500">No voices installed yet. Download the voice models (about 320 MB) to choose one.</p>
        )}
      </div>

      <MissionSection icon={<Activity size={16} className="text-orange-400" />} title="Proposed fixes" loading={missionsLoading}
        empty="No proposed fixes. Raven has not seen a container keep failing.">
        {triage.map((mission) => (
          <div key={mission.id} className="glass-card p-4 border-l-4 border-l-orange-500/50 flex flex-col gap-3 min-w-0">
            <div className="min-w-0">
              <p className="text-sm font-bold text-white truncate">{mission.target_container || 'System'}</p>
              <p className="text-xs text-slate-400 line-clamp-2">{mission.error_summary}</p>
              <p className="text-[10px] text-slate-500 mt-1">Found {new Date(mission.queued_at ?? mission.created_at).toLocaleString()}</p>
            </div>
            <div className="flex flex-wrap gap-2">
              <button
                onClick={() => executeMissionMutation.mutate(mission.id)}
                disabled={executeMissionMutation.isPending && executeMissionMutation.variables === mission.id}
                className={`${btn} text-red-300`}
              >
                <Play size={12} /> Run fix
              </button>
              <button onClick={() => setDetailId(mission.id)} className={`${btn} text-slate-300`}>
                <Terminal size={12} /> Details
              </button>
              <button
                onClick={() => deleteMissionMutation.mutate(mission.id)}
                disabled={deleteMissionMutation.isPending}
                className={`${btn} text-slate-400`}
              >
                <Trash2 size={12} /> Dismiss
              </button>
            </div>
          </div>
        ))}
      </MissionSection>

      <MissionSection icon={<Activity size={16} className="text-emerald-400" />} title="Running now" loading={missionsLoading}
        empty="No missions are running.">
        {active.map((mission) => (
          <div key={mission.id} className="glass-card p-4 border-l-4 border-l-emerald-500/50 flex flex-col gap-3 min-w-0">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2 mb-1">
                <span className="bg-emerald-500/10 text-emerald-300 px-2 py-0.5 rounded text-[10px] font-black uppercase tracking-widest">{mission.status}</span>
                <span className="text-xs text-slate-400">#{mission.id} · {mission.mission_type}</span>
              </div>
              <p className="text-xs text-slate-300 line-clamp-2 break-words">{mission.proposed_mission}</p>
              <div className="w-full bg-black/40 rounded-full h-1.5 mt-2 overflow-hidden">
                <div className="bg-emerald-500 h-1.5 rounded-full transition-all duration-500" style={{ width: `${mission.progress || 0}%` }} />
              </div>
              <p className="text-[10px] text-slate-500 mt-1">
                {mission.started_at
                  ? `Started ${new Date(mission.started_at).toLocaleString()}`
                  : `Queued ${new Date(mission.queued_at ?? mission.created_at).toLocaleString()}`}
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <button onClick={() => setLiveMissionId(mission.id)} className={`${btn} text-blue-300`}>
                <Terminal size={12} /> Watch
              </button>
              <button
                onClick={() => {
                  if (window.confirm(`Stop mission #${mission.id}?`)) killMissionMutation.mutate(mission.id);
                }}
                disabled={killMissionMutation.isPending}
                className={`${btn} text-red-400`}
              >
                <Square size={12} /> Stop
              </button>
            </div>
          </div>
        ))}
      </MissionSection>

      <MissionSection icon={<Clock size={16} className="text-blue-400" />} title="Recent missions" loading={missionsLoading}
        empty="No finished missions yet.">
        {history.map((mission) => (
          <button
            key={mission.id}
            onClick={() => setDetailId(mission.id)}
            className={`w-full text-left glass-card p-4 min-h-11 border-l-4 hover:bg-white/5 transition min-w-0 ${
              mission.status === 'completed' ? 'border-l-emerald-500/40' : 'border-l-red-500/40'
            }`}
          >
            <div className="flex flex-wrap items-center gap-2 mb-1">
              <span className={`px-2 py-0.5 rounded text-[10px] font-black uppercase tracking-widest ${
                mission.status === 'completed' ? 'bg-emerald-500/10 text-emerald-300' : 'bg-red-500/10 text-red-300'
              }`}>
                {mission.status}
              </span>
              <span className="text-xs text-slate-500 font-mono">#{mission.id}</span>
              <span className="text-xs text-slate-400 truncate">{mission.target_container || mission.mission_type}</span>
            </div>
            <p className="text-xs text-slate-400 line-clamp-1 break-words">{mission.proposed_mission}</p>
            <p className="text-[10px] text-slate-500 mt-1">
              {mission.completed_at ? new Date(mission.completed_at).toLocaleString() : ''}
              {mission.duration != null ? ` · ${formatDuration(mission.duration)}` : ''}
            </p>
          </button>
        ))}
      </MissionSection>

      <section className="pt-6 border-t border-white/10">
        <h4 className="flex items-center gap-2 text-sm font-bold text-slate-300 mb-2 uppercase tracking-widest">
          <Search size={16} className="text-emerald-400" />
          Start a mission
        </h4>
        <p className="text-xs text-slate-500 mb-3">
          Queues a Raven mission with this brief and opens its live trace. To steer a finished
          mission, open it under Recent missions and send follow-up instructions.
        </p>
        <textarea
          value={missionPrompt}
          onChange={(e) => setMissionPrompt(e.target.value)}
          placeholder="What should Raven investigate or fix?"
          aria-label="Mission brief"
          rows={3}
          disabled={startMissionMutation.isPending}
          className="glass-input w-full text-sm resize-y"
        />
        <button
          onClick={() => startMissionMutation.mutate(missionPrompt.trim())}
          disabled={!missionPrompt.trim() || startMissionMutation.isPending}
          className={`${btn} mt-2 w-full sm:w-auto text-emerald-300`}
        >
          <Play size={12} />
          {startMissionMutation.isPending ? 'Queuing…' : 'Start mission'}
        </button>
      </section>

      <RavenAuditLog isOpen={isAuditLogOpen} onClose={() => setIsAuditLogOpen(false)} />
      <RavenLiveTrace isOpen={liveMissionId !== null} onClose={() => setLiveMissionId(null)} missionId={liveMissionId} />
      <RavenMissionDetail
        missionId={detailId}
        onClose={() => setDetailId(null)}
        onRefined={(id) => {
          refreshMissions();
          setLiveMissionId(id);
        }}
      />
    </div>
  );
}

function SettingCard({ icon, title, hint, children }: { icon: React.ReactNode; title: string; hint: string; children: React.ReactNode }) {
  return (
    <div className="glass-card p-4 border border-white/10 flex flex-col justify-between gap-3 min-w-0">
      <div>
        <p className="text-[10px] font-black uppercase tracking-widest text-slate-500 mb-1 flex items-center gap-2">{icon} {title}</p>
        <p className="text-sm text-slate-300">{hint}</p>
      </div>
      {children}
    </div>
  );
}

interface NumberSettingProps {
  label: string;
  value: number;
  min: number;
  step?: number;
  dirty: boolean;
  busy: boolean;
  onChange: (v: number) => void;
  onSave: () => void;
}

function NumberSetting({ label, value, min, step, dirty, busy, onChange, onSave }: NumberSettingProps) {
  return (
    <div className="flex gap-2">
      <input
        type="number"
        inputMode="numeric"
        aria-label={label}
        min={min}
        step={step}
        value={Number.isNaN(value) ? '' : value}
        onChange={(e) => onChange(parseInt(e.target.value, 10))}
        className="glass-input flex-1 min-w-0 min-h-11 text-sm"
      />
      <button onClick={onSave} disabled={busy || !dirty} className={`${btn} text-slate-200`}>
        Save
      </button>
    </div>
  );
}

function MissionSection({ icon, title, loading, empty, children }: {
  icon: React.ReactNode; title: string; loading: boolean; empty: string; children: React.ReactNode[];
}) {
  return (
    <section className="pt-6 border-t border-white/10">
      <h4 className="flex items-center gap-2 text-sm font-bold text-slate-300 mb-4 uppercase tracking-widest">{icon}{title}</h4>
      {loading ? (
        <p className="text-slate-500 text-sm">Loading…</p>
      ) : children.length === 0 ? (
        <p className="rounded-2xl border border-white/5 bg-white/5 px-4 py-6 text-center text-sm text-slate-500">{empty}</p>
      ) : (
        <div className="space-y-3">{children}</div>
      )}
    </section>
  );
}
