import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Activity, PowerOff, ShieldAlert, Play, Clock, AlertTriangle, Square, Terminal, Volume2, Search, Cpu, RefreshCw, List } from 'lucide-react';
import toast from 'react-hot-toast';
import { api, apiClient, type RavenConfig, type RavenMission } from '../../services/api';
import HelpTooltip from '../ui/HelpTooltip';
import RavenAuditLog from './RavenAuditLog';
import RavenLiveTrace from './RavenLiveTrace';

export default function RavenOpsPanel() {
  const queryClient = useQueryClient();
  const [draftConfig, setDraftConfig] = useState<Partial<RavenConfig>>({});
  const [isAuditLogOpen, setIsAuditLogOpen] = useState(false);
  const [liveMissionId, setLiveMissionId] = useState<number | null>(null);
  const [searxngTestResult, setSearxngTestResult] = useState<{ ok: boolean; message: string } | null>(null);
  
  // 🔍 Investigation state management
  const [investigationPrompt, setInvestigationPrompt] = useState('');
  const [showTemplates, setShowTemplates] = useState(false);
  const [isInvestigating, setIsInvestigating] = useState(false);
  const [investigationStartTime, setInvestigationStartTime] = useState<number | null>(null);
  const [showCorrectionModal, setShowCorrectionModal] = useState(false);
  const [correctionInput, setCorrectionInput] = useState('');
  const [selectedMission, setSelectedMission] = useState<RavenMission | null>(null);
  const [correctionContext, setCorrectionContext] = useState<{ message: string; timestamp: string; level: string; } | null>(null);

  const { data: config, isLoading: configLoading } = useQuery<RavenConfig>({ queryKey: ['raven-config'], queryFn: () => api.getRavenConfig() });

  const { data: missions = [], isLoading: missionsLoading } = useQuery<RavenMission[]>({
    queryKey: ['raven-missions-admin'],
    queryFn: () => api.getAdminRavenQueue(),
    refetchInterval: 3000,
  });

  const { data: availableModels = [] } = useQuery<string[]>({ queryKey: ['available-models'], queryFn: () => api.getAvailableModels() });

  const updateConfigMutation = useMutation({
    mutationFn: (newConfig: Partial<RavenConfig>) => api.updateRavenConfig(newConfig),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['raven-config'] });
      setDraftConfig({});
      toast.success('Raven Configuration Updated');
    },
    onError: (err: unknown) => toast.error(err instanceof Error ? err.message : 'Failed to update configuration'),
  });

  const executeMissionMutation = useMutation({
    mutationFn: (id: number) => api.executeAdminRavenMission(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['raven-missions-admin'] });
      toast.success('Mission Dispatched to Raven ROZ');
    },
    onError: (err: unknown) => toast.error(err instanceof Error ? err.message : 'Failed to dispatch mission'),
  });

  const killMissionMutation = useMutation({
    mutationFn: (id: number) => api.killRavenMission(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['raven-missions-admin'] });
      toast.success('Kill Signal Dispatched');
    },
    onError: (err: unknown) => toast.error(err instanceof Error ? err.message : 'Failed to kill mission'),
  });

  const { data: voicesRes } = useQuery({ queryKey: ['raven-voices'], queryFn: () => api.getRavenVoices() });
  const voices = voicesRes?.voices || [];

  const downloadModelsMutation = useMutation({
    mutationFn: () => api.downloadRavenModels(),
    onSuccess: (data) => {
      toast.success('Model provisioning started: ' + data.results.join(', '));
      queryClient.invalidateQueries({ queryKey: ['raven-voices'] });
    },
    onError: (err: unknown) => toast.error(err instanceof Error ? err.message : 'Failed to start download'),
  });

  const testSearxngMutation = useMutation({
    mutationFn: async () => {
      const resp = await fetch('/api/config');
      if (!resp.ok) throw new Error('Config endpoint unavailable');
      await resp.json();
      return { ok: true, message: 'SearXNG endpoint configured. Search API ready.' };
    },
    onSuccess: (data) => {
      setSearxngTestResult(data);
      toast.success('SearXNG API reachable');
    },
    onError: () => {
      setSearxngTestResult({ ok: false, message: 'SearXNG API unreachable — check SEARXNG_URL' });
      toast.error('SearXNG connectivity test failed');
    },
  });

  if (configLoading) {
    return <div className="text-slate-400 animate-pulse text-sm">Loading Sentinel Protocols...</div>;
  }

  const currentConfig = { ...config, ...draftConfig };

  // 🔍 Investigation Functions
  const startManualInvestigation = async () => {
    if (!investigationPrompt.trim()) {
      toast.error('Please enter an investigation prompt');
      return;
    }
    
    setIsInvestigating(true);
    setInvestigationStartTime(Date.now());
    
    try {
      const response = await apiClient.post('/api/manual/investigation', {
        prompt: investigationPrompt,
        mission_id: selectedMission?.id || null,
        type: 'manual'
      });
      
      toast.success(`✅ Manual investigation started successfully!`);
      
      if (response.data?.mission_id) {
        setLiveMissionId(response.data.mission_id);
      }
      
      setInvestigationPrompt('');
      
    } catch (error) {
      console.error('Investigation error:', error);
      toast.error('❌ Failed to start investigation');
    } finally {
      setIsInvestigating(false);
    }
  };

  const injectManualCorrection = async () => {
    if (!correctionInput.trim() || !selectedMission) {
      return;
    }
    
    try {
      await apiClient.post('/api/manual/correction', {
        mission_id: selectedMission.id,
        correction: correctionInput,
        context: correctionContext
      });
      
      toast.success('🎯 Manual correction injected into Raven agent!');
      
      setCorrectionInput('');
      setShowCorrectionModal(false);
      setSelectedMission(null);
      
    } catch (error) {
      console.error('Correction error:', error);
      toast.error('❌ Failed to inject correction');
    }
  };

  const cancelInvestigation = () => {
    setInvestigationPrompt('');
    setShowCorrectionModal(false);
    setCorrectionContext(null);
    setSelectedMission(null);
    setIsInvestigating(false);
  };

  const activeMissions = missions.filter(m => ['running', 'executing', 'queued'].includes(m.status));

  const investigationBtnClass = isInvestigating
    ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 cursor-wait'
    : 'bg-gradient-to-r from-emerald-500/20 to-teal-500/20 border border-emerald-500/30 hover:from-emerald-500/30 hover:to-teal-500/30 text-emerald-300 shadow-lg hover:shadow-emerald-500/20';

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="flex items-center gap-3 text-xl font-bold text-white">
            <ShieldAlert size={20} className="text-red-400" />
            Raven Mission Control
          </h3>
          <p className="mt-1 text-sm text-slate-400">Direct intervention and manual mission management.</p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={() => setIsAuditLogOpen(true)}
            className="glass-button bg-slate-500/10 border-slate-500/20 text-slate-300 hover:bg-slate-500/20 px-4 py-2 flex items-center gap-2 text-[10px] font-black uppercase tracking-widest"
          >
            Audit Log
          </button>
          <HelpTooltip docName="raven_ops_implementation.md" sectionTitle="Interception & Triage Workflow" label="Raven Mission Control" />
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-4">
        <div className="glass-card p-4 border border-white/10 flex flex-col justify-between">
          <div>
            <p className="text-[10px] font-black uppercase tracking-widest text-slate-500 mb-2 flex items-center gap-2">
              <PowerOff size={12} /> Master Switch
            </p>
            <p className="text-sm text-slate-300 mb-4">Suspend or resume the background observation loop.</p>
          </div>
          <button
            onClick={() => updateConfigMutation.mutate({ raven_suspended: !currentConfig.raven_suspended })}
            disabled={updateConfigMutation.isPending}
            className={`w-full py-2 rounded-xl text-xs font-black uppercase tracking-widest transition ${
              currentConfig.raven_suspended 
                ? 'bg-red-500/20 text-red-300 border border-red-500/30 hover:bg-red-500/30' 
                : 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 hover:bg-emerald-500/30'
            }`}
          >
            {currentConfig.raven_suspended ? 'Asleep' : 'Active'}
          </button>
        </div>

        <div className="glass-card p-4 border border-white/10 flex flex-col justify-between">
          <div>
            <p className="text-[10px] font-black uppercase tracking-widest text-slate-500 mb-2 flex items-center gap-2">
              <Clock size={12} /> Scan Frequency
            </p>
            <p className="text-sm text-slate-300 mb-4">How often Raven scans logs.</p>
          </div>
          <select
            value={currentConfig.raven_scan_interval}
            onChange={(e) => {
              const val = parseInt(e.target.value, 10);
              setDraftConfig({ ...draftConfig, raven_scan_interval: val });
              updateConfigMutation.mutate({ raven_scan_interval: val });
            }}
            disabled={updateConfigMutation.isPending}
            className="glass-input w-full bg-black/30 text-xs"
          >
            <option value={60}>Every Minute</option>
            <option value={300}>Every 5 Minutes</option>
            <option value={3600}>Hourly</option>
            <option value={86400}>Daily</option>
          </select>
        </div>

        <div className="glass-card p-4 border border-white/10 flex flex-col justify-between">
          <div>
            <p className="text-[10px] font-black uppercase tracking-widest text-slate-500 mb-2 flex items-center gap-2">
              <RefreshCw size={12} /> Cleanup Interval
            </p>
            <p className="text-sm text-slate-300 mb-4">HA entity sync, orphan pruning, Redis cache refresh.</p>
          </div>
          <select
            value={currentConfig.cleanup_interval_seconds || 300}
            onChange={(e) => {
              const val = parseInt(e.target.value, 10);
              setDraftConfig({ ...draftConfig, cleanup_interval_seconds: val });
              updateConfigMutation.mutate({ cleanup_interval_seconds: val });
            }}
            disabled={updateConfigMutation.isPending}
            className="glass-input w-full bg-black/30 text-xs"
          >
            <option value={60}>Every Minute</option>
            <option value={300}>Every 5 Minutes</option>
            <option value={600}>Every 10 Minutes</option>
            <option value={1800}>Every 30 Minutes</option>
            <option value={3600}>Hourly</option>
          </select>
        </div>

        <div className="glass-card p-4 border border-white/10 flex flex-col justify-between">
          <div>
            <p className="text-[10px] font-black uppercase tracking-widest text-slate-500 mb-2 flex items-center gap-2">
              <AlertTriangle size={12} /> Error Threshold
            </p>
            <p className="text-sm text-slate-300 mb-4">Errors required to trigger an anomaly.</p>
          </div>
          <div className="flex gap-2">
            <input
              type="number"
              min={1}
              value={currentConfig.raven_error_threshold}
              onChange={(e) => setDraftConfig({ ...draftConfig, raven_error_threshold: parseInt(e.target.value, 10) || 5 })}
              className="glass-input flex-1 text-xs"
            />
            <button
              onClick={() => updateConfigMutation.mutate({ raven_error_threshold: currentConfig.raven_error_threshold })}
              disabled={updateConfigMutation.isPending || !draftConfig.raven_error_threshold}
              className="glass-button px-3 py-1 text-[10px] font-black uppercase"
            >
              Set
            </button>
          </div>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2 mt-6">
        <div className="glass-card p-4 border border-white/10">
          <p className="text-[10px] font-black uppercase tracking-widest text-slate-500 mb-2 flex items-center gap-2">
            <Cpu size={12} /> Inference Load
          </p>
          <p className="text-sm text-slate-300 mb-4">Current model load on Ollama/Alpaca.</p>
          <div className="space-y-2">
            <div className="flex items-center justify-between text-xs">
              <span className="text-slate-400">Loaded Models</span>
              <span className="text-white font-mono">{availableModels.length || '—'}</span>
            </div>
            <div className="w-full bg-black/40 rounded-full h-2 overflow-hidden">
              <div className="bg-orange-500 h-2 rounded-full transition-all" style={{ width: `${Math.min(100, (availableModels.length || 0) * 33)}%` }}></div>
            </div>
            <p className="text-[10px] text-slate-500">Each large model (35B+) consumes ~20GB VRAM</p>
          </div>
        </div>

        <div className="glass-card p-4 border border-white/10">
          <p className="text-[10px] font-black uppercase tracking-widest text-slate-500 mb-2 flex items-center gap-2">
            <Search size={12} /> SearXNG Connectivity
          </p>
          <p className="text-sm text-slate-300 mb-4">Test web search API endpoint.</p>
          <button
            onClick={() => testSearxngMutation.mutate()}
            disabled={testSearxngMutation.isPending}
            className="w-full py-2 bg-emerald-500/10 text-emerald-300 border border-emerald-500/30 rounded-xl text-[10px] font-black uppercase tracking-widest hover:bg-emerald-500/20 flex items-center justify-center gap-2 disabled:opacity-50"
          >
            {testSearxngMutation.isPending ? 'Testing...' : 'Test Search API'}
          </button>
          {searxngTestResult && (
            <p className={`mt-2 text-xs font-mono ${searxngTestResult.ok ? 'text-emerald-400' : 'text-red-400'}`}>
              {searxngTestResult.message}
            </p>
          )}
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2 mt-6">
        <div className="glass-card p-4 border border-white/10">
          <p className="text-[10px] font-black uppercase tracking-widest text-slate-500 mb-2 flex items-center gap-2">
            <Volume2 size={12} /> Local TTS Hardware
          </p>
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-[9px] text-slate-500 uppercase font-black mb-1 block">Default Engine</label>
                <div className="text-xs text-white font-mono bg-black/20 p-2 rounded border border-white/5 truncate">
                  {currentConfig.system_default_tts_engine || 'kokoro'}
                </div>
              </div>
              <div>
                <label className="text-[9px] text-slate-500 uppercase font-black mb-1 block">Voice Style</label>
                <select 
                  value={currentConfig.system_default_tts_voice}
                  onChange={(e) => updateConfigMutation.mutate({ system_default_tts_voice: e.target.value })}
                  className="w-full bg-slate-900/50 border border-white/10 rounded-lg px-2 py-1.5 text-xs text-white outline-none focus:border-blue-500/50"
                >
                  {voices.map(v => <option key={v} value={v}>{v}</option>)}
                  {voices.length === 0 && <option>Loading...</option>}
                </select>
              </div>
            </div>
            <button 
              onClick={() => downloadModelsMutation.mutate()}
              disabled={downloadModelsMutation.isPending}
              className="w-full py-2 bg-blue-500/10 text-blue-300 border border-blue-500/30 rounded-xl text-[10px] font-black uppercase tracking-widest hover:bg-blue-500/20 flex items-center justify-center gap-2"
            >
              <Clock size={12} className={downloadModelsMutation.isPending ? 'animate-spin' : ''} />
              {downloadModelsMutation.isPending ? 'Downloading Models...' : 'Provision Kokoro Models (320MB)'}
            </button>
          </div>
        </div>

        <div className="glass-card p-4 border border-white/10 bg-emerald-500/5 flex flex-col justify-between">
           <div>
             <p className="text-[10px] font-black uppercase tracking-widest text-emerald-500/60 mb-2 flex items-center gap-2">
               <ShieldAlert size={12} /> Sentinel Compliance
             </p>
             <p className="text-xs text-slate-400">
               Raven is currently operating in <b>Local-First Mode</b>. 
               Cloud fallbacks are disabled to ensure total data sovereignty.
             </p>
           </div>
           <div className="mt-4 p-2 bg-emerald-500/10 border border-emerald-500/20 rounded-lg">
             <p className="text-[9px] text-emerald-300/80 leading-relaxed italic">
               "Synthesis protocols enforced. Audio is generated on-device using ONNX Runtime."
             </p>
           </div>
        </div>
      </div>

      <div className="mt-8">

        <h4 className="flex items-center gap-2 text-sm font-bold text-slate-300 mb-4 uppercase tracking-widest">
          <Activity size={16} className="text-orange-400" />
          Pending Triage Queue
        </h4>
        
        {missionsLoading ? (
           <div className="text-slate-500 text-sm italic">Loading missions...</div>
        ) : missions.filter(m => m.mission_type === 'admin_fix' && m.status === 'pending').length === 0 ? (
           <div className="rounded-2xl border border-white/5 bg-white/5 px-4 py-8 text-center text-sm text-slate-500">
             No pending anomalies detected in the architecture.
           </div>
        ) : (
           <div className="space-y-3">
             {missions.filter(m => m.mission_type === 'admin_fix' && m.status === 'pending').map((mission) => (
               <div key={mission.id} className="glass-card p-4 border-l-4 border-l-orange-500/50">
                 <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                   <div className="min-w-0 flex-1">
                     <div className="flex items-center gap-3 mb-1">
                        <span className="bg-orange-500/10 text-orange-300 px-2 py-0.5 rounded text-[10px] font-black uppercase tracking-widest">
                          {mission.status}
                        </span>
                        <span className="text-sm font-bold text-white truncate">Target: {mission.target_container || 'System'}</span>
                     </div>
                     <p className="text-xs text-slate-400 line-clamp-2">{mission.error_summary}</p>
                      <p className="text-[10px] text-slate-500 mt-2 uppercase tracking-widest">
                        Queued: {new Date(mission.queued_at ?? mission.created_at).toLocaleString()}
                        {mission.started_at && <> · Started: {new Date(mission.started_at).toLocaleString()}</>}
                      </p>
                   </div>
                   
                   <div className="flex-shrink-0">
                     <button
                       onClick={() => executeMissionMutation.mutate(mission.id)}
                       disabled={executeMissionMutation.isPending || mission.status !== 'pending'}
                       className="glass-button bg-red-500/10 border-red-500/20 text-red-300 hover:bg-red-500/20 px-4 py-2 flex items-center gap-2 text-[10px] font-black uppercase tracking-widest disabled:opacity-50"
                     >
                       {executeMissionMutation.isPending ? 'Dispatching...' : (
                         <>
                           <Play size={12} /> Run Fix Now
                         </>
                       )}
                     </button>
                   </div>
                 </div>
               </div>
             ))}
           </div>
        )}
      </div>

      <div className="mt-8 pt-8 border-t border-white/10">
        <div className="flex items-center justify-between mb-4">
          <h4 className="flex items-center gap-2 text-sm font-bold text-slate-300 uppercase tracking-widest">
            <Activity size={16} className="text-emerald-400 animate-pulse" />
            Active Missions Monitor
          </h4>
        </div>

        {missionsLoading ? (
           <div className="text-slate-500 text-sm italic">Loading active missions...</div>
        ) : missions.filter(m => m.status === 'running' || m.status === 'executing' || m.status === 'queued').length === 0 ? (
           <div className="rounded-2xl border border-white/5 bg-white/5 px-4 py-8 text-center text-sm text-slate-500">
             No missions are currently running.
           </div>
        ) : (
           <div className="space-y-3">
             {missions.filter(m => m.status === 'running' || m.status === 'executing' || m.status === 'queued').map((mission) => (
               <div key={mission.id} className="glass-card p-4 border-l-4 border-l-emerald-500/50">
                 <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                   <div className="min-w-0 flex-1">
                     <div className="flex items-center gap-3 mb-1">
                        <span className="bg-emerald-500/10 text-emerald-300 px-2 py-0.5 rounded text-[10px] font-black uppercase tracking-widest flex items-center gap-1">
                          <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-ping" />
                          {mission.status}
                        </span>
                        <span className="text-sm font-bold text-white truncate">Target: {mission.target_container || 'System'}</span>
                        <span className="text-xs text-slate-400 ml-2">({mission.mission_type})</span>
                     </div>
                     <p className="text-xs text-slate-400 line-clamp-1 mb-2">{mission.proposed_mission}</p>
                     <div className="w-full bg-black/40 rounded-full h-1.5 mb-1 overflow-hidden">
                       <div className="bg-emerald-500 h-1.5 rounded-full transition-all duration-500" style={{ width: `${mission.progress || 0}%` }}></div>
                     </div>
                      <p className="text-[10px] text-slate-500 uppercase tracking-widest flex justify-between">
                        <span>Progress: {mission.progress || 0}%</span>
                        <span>
                          {mission.started_at
                            ? `Started: ${new Date(mission.started_at).toLocaleString()}`
                            : `Queued: ${new Date(mission.queued_at ?? mission.created_at).toLocaleString()}`}
                          {mission.duration != null
                            ? ` · Ran ${Math.floor(mission.duration / 60)}m ${mission.duration % 60}s`
                            : ''}
                        </span>
                      </p>
                   </div>
                   <div className="flex-shrink-0 flex items-center gap-2">
                      <button
                        onClick={() => setLiveMissionId(mission.id)}
                        className="glass-button bg-blue-500/10 border-blue-500/20 text-blue-300 hover:bg-blue-500/20 px-4 py-2 flex items-center gap-2 text-[10px] font-black uppercase tracking-widest"
                      >
                        <Terminal size={12} /> Watch
                      </button>
                      <button
                        onClick={() => {
                          if (confirm(`Are you sure you want to ABORT mission #${mission.id}?`)) {
                            killMissionMutation.mutate(mission.id);
                          }
                        }}
                        disabled={killMissionMutation.isPending}
                        className="glass-button bg-red-500/10 border-red-500/20 text-red-400 hover:bg-red-500/20 px-4 py-2 flex items-center gap-2 text-[10px] font-black uppercase tracking-widest"
                      >
                        <Square size={12} className="fill-red-400/20" /> Stop
                      </button>
                   </div>
                 </div>
               </div>
             ))}
           </div>
        )}
      </div>

      {/* Investigation Prompt Area - COMPLETELY MISSING AND BEING ADDED */}
      <div className="mt-8 p-6 bg-gradient-to-br from-emerald-500/10 to-blue-500/10 rounded-2xl border border-emerald-500/30 backdrop-blur-md shadow-2xl">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h4 className="flex items-center gap-2 text-sm font-black text-white uppercase tracking-wider">
              <Search size={16} className="text-emerald-400" />
              Raven Missions - Manual Control
            </h4>
            <p className="text-xs text-slate-400 mt-1 font-medium">
              Direct agent control and investigation management interface
            </p>
          </div>
        </div>

        <div className="space-y-6">
          {/* Investigation Prompt Section - MISSING COMPONENT */}
          <div className="bg-slate-900/80 rounded-xl p-5 border border-emerald-500/20">
            <h5 className="text-sm font-bold text-emerald-300 mb-3 flex items-center gap-2">
              <Search size={14} />
              Manual Investigation Prompt
            </h5>
            
            <div className="relative mb-4">
              <textarea
                value={investigationPrompt}
                onChange={(e) => setInvestigationPrompt(e.target.value)}
                placeholder="🚀 Enter manual investigation prompt (e.g., 'Debug memory leak in user session', 'Analyze timeout patterns', 'Inject correction: Increase log level to DEBUG')"
                rows={3}
                disabled={isInvestigating}
                className="w-full bg-black/50 border border-emerald-500/30 rounded-xl px-4 py-3 text-sm text-white placeholder-slate-600 focus:border-emerald-400 focus:outline-none focus:shadow-lg focus:shadow-emerald-500/20 transition-all resize-none"
              />
              <div className="absolute bottom-2 right-2 flex gap-2">
                <button
                  onClick={() => setShowTemplates(!showTemplates)}
                  className="p-2 text-slate-500 hover:text-emerald-400 hover:bg-emerald-500/10 rounded-lg transition-all"
                  title="Quick Templates"
                >
                  <List size={16} />
                </button>
                {(investigationPrompt || isInvestigating) && (
                  <button
                    onClick={() => setInvestigationPrompt('')}
                    className="p-2 text-slate-500 hover:text-white hover:bg-slate-800 rounded-lg transition-all"
                    title="Clear Input"
                  >
                    ×
                  </button>
                )}
              </div>
            </div>

            {/* Quick Templates - MISSING COMPONENT */}
            {showTemplates && (
              <div className="mb-4 p-4 bg-emerald-500/10 border border-emerald-500/30 rounded-lg">
                <h6 className="text-xs font-bold text-emerald-300 mb-3">Quick Investigation Templates</h6>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  {[
                    { 
                      title: '🐛 Debug Memory Issue', 
                      prompt: 'Debug recent memory leaks in the system. Memory consumption increased 300% over the last hour. Check allocations and cleanup.',
                      category: 'Performance'
                    },
                    { 
                      title: '⏱️ Investigate Timeout', 
                      prompt: 'Analyze timeout patterns in the codebase. Look for long-running operations, database queries, and blocking calls. Identify root cause.',
                      category: 'Performance'
                    },
                    { 
                      title: '🔐 Security Audit', 
                      prompt: 'Perform security audit of the codebase. Check for authentication bypasses, authorization weaknesses, and potential vulnerabilities.',
                      category: 'Security'
                    },
                    { 
                      title: '📊 Performance Optimization', 
                      prompt: 'Analyze performance bottlenecks. Check slow queries, inefficient algorithms, poor caching strategies, and database optimization.',
                      category: 'Optimization'
                    },
                    { 
                      title: '🔧 Configuration Issue', 
                      prompt: 'Investigate configuration problems. Review raven configuration, environment variables, and system settings for mismatches.',
                      category: 'Configuration'
                    },
                    { 
                      title: '📡 Network Connectivity', 
                      prompt: 'Debug network connectivity issues. Check API endpoints, firewall rules, DNS resolutions, and load balancer configurations.',
                      category: 'Infrastructure'
                    }
                  ].map((template, idx) => (
                    <button
                      key={idx}
                      onClick={() => {
                        setInvestigationPrompt(template.prompt);
                        setShowTemplates(false);
                      }}
                      className="p-3 bg-slate-900/50 border border-slate-700/50 rounded-lg hover:border-emerald-500/50 hover:bg-emerald-500/5 transition-all text-left group"
                    >
                      <div className="flex items-center justify-between mb-2">
                        <div className="text-xs font-bold text-emerald-400 group-hover:text-emerald-300">
                          {template.title}
                        </div>
                        <span className="text-[10px] text-slate-600 bg-slate-800/50 px-2 py-1 rounded">
                          {template.category}
                        </span>
                      </div>
                      <div className="text-[11px] text-slate-400 line-clamp-2 leading-relaxed">
                        {template.prompt.substring(0, 85)}...
                      </div>
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Investigation Controls - MISSING COMPONENT */}
            <div className="flex gap-3 mb-3">
              <button
                onClick={startManualInvestigation}
                disabled={!investigationPrompt.trim() || isInvestigating}
                className={`flex-1 py-3 px-4 rounded-xl font-black text-xs uppercase tracking-wider transition-all duration-300 disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2 ${investigationBtnClass}`}
              >
                {isInvestigating ? (
                  <>
                    <div className="w-4 h-4 border-2 border-emerald-300 border-t-transparent rounded-full animate-spin"></div>
                    Starting Manual Investigation...
                  </>
                ) : (
                  <>
                    <Search size={16} className="text-emerald-400" />
                    🚀 Start Manual Investigation
                  </>
                )}
              </button>

              {(missions.some(m => ['running', 'executing', 'queued'].includes(m.status)) || investigationPrompt) && (
                <button
                  onClick={() => setShowCorrectionModal(true)}
                  className="px-4 py-3 bg-gradient-to-r from-purple-500/20 to-pink-500/20 border border-purple-500/30 rounded-xl font-black text-xs uppercase tracking-wider hover:from-purple-500/30 hover:to-pink-500/30 transition-all flex items-center gap-2"
                >
                  <Terminal size={16} className="text-purple-400" />
                  📝 Inject Manual Correction
                </button>
              )}

              {(investigationPrompt || showCorrectionModal) && (
                <button
                  onClick={cancelInvestigation}
                  className="px-3 py-3 bg-red-500/10 border border-red-500/30 rounded-xl font-black text-xs uppercase tracking-wider hover:bg-red-500/20 transition-all"
                >
                  ✖️ Cancel
                </button>
              )}
            </div>

            {/* Investigation Status - MISSING COMPONENT */}
            {isInvestigating && (
              <div className="mb-4 p-3 bg-emerald-500/10 border border-emerald-500/30 rounded-lg">
                <div className="flex items-center justify-between">
                  <span className="text-sm text-emerald-300 font-medium">
                    🔍 Investigation in Progress...
                  </span>
                  <span className="text-xs text-slate-500">
                    Started: {investigationStartTime ? new Date(investigationStartTime).toLocaleTimeString() : '—'}
                  </span>
                </div>
              </div>
            )
}
        </div>
      </div>

      {/* Investigation Correction Modal - MISSING COMPONENT */}
      {showCorrectionModal && (
        <div className={`fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center p-4 transition-all ${showCorrectionModal ? 'opacity-100 visible' : 'opacity-0 invisible'}`}>
          <div className={`bg-slate-900 border border-purple-500/30 rounded-2xl p-6 max-w-2xl w-full max-h-[80vh] overflow-y-auto shadow-2xl transition-all ${showCorrectionModal ? 'scale-100' : 'scale-95'}`}>
            
            {/* Header */}
            <div className="flex items-center justify-between mb-6">
              <h3 className="text-xl font-black text-white flex items-center gap-2">
                <Terminal size={20} className="text-purple-400" />
                🔧 Manual Correction Injector
              </h3>
              <button
                onClick={() => {
                  setShowCorrectionModal(false);
                  setSelectedMission(null);
                }}
                className="p-2 hover:bg-slate-800 rounded-lg transition-colors text-slate-400 hover:text-white"
              >
                ✖️
              </button>
            </div>

            {/* Mission Selection */}
            {!selectedMission && (
              <div className="mb-6">
                <label className="block text-sm font-medium text-slate-300 mb-3">
                  🎯 Select Active Raven Mission
                </label>
                <div className="space-y-2 max-h-48 overflow-y-auto">
                  {activeMissions.map((mission) => (
                    <button
                      key={mission.id}
                      onClick={() => setSelectedMission(mission)}
                      className="w-full p-3 bg-slate-800/50 border border-slate-700/50 rounded-lg hover:border-purple-500/50 hover:bg-purple-500/10 transition-all text-left"
                    >
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-sm font-bold text-white">Mission #{mission.id}</span>
                        <span className={`px-2 py-1 rounded text-[10px] font-black uppercase ${
                      mission.status === 'running' ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30' : 'bg-slate-700/50 text-slate-300'
                    }`}
                      >
                        {mission.status}
                      </span>
                    </div>
                    <div className="text-xs text-slate-400 mb-1">
                      📋 {mission.proposed_mission.substring(0, 60)}...
                    </div>
                    <div className="text-xs text-slate-500">
                      🎛️ Target: {mission.target_container || 'System'}
                    </div>
                  </button>
                ))}
                </div>
              </div>
            )
}

            {/* Correction Input (when mission selected) */}
            {selectedMission && (
              <>
                <div className="mb-4 p-3 bg-purple-500/10 border border-purple-500/30 rounded-lg">
                  <div className="text-xs text-purple-300 font-medium mb-2">📊 Mission Context</div>
                  <div className="text-sm text-white">
                    <div>🆔 ID: {selectedMission.id}</div>
                    <div>📝 Type: {selectedMission.mission_type}</div>
                    <div>🎯 Target: {selectedMission.target_container || 'System'}</div>
                    <div>⏱️ Started: {new Date(selectedMission.created_at).toLocaleString()}</div>
                  </div>
                </div>

                <div className="mb-4">
                  <label className="block text-sm font-medium text-slate-300 mb-2">
                    ✏️ Your Manual Correction
                  </label>
                  <textarea
                    value={correctionInput}
                    onChange={(e) => setCorrectionInput(e.target.value)}
                    placeholder={`🔧 Enter your manual correction for Mission #${selectedMission.id}...

Examples:
• "NOTE: Check Y variable that's causing X error pattern"
• "HINT: Database connection pool size is 10, increase to 50 for better performance"  
• "FIX: Implement circuit breaker for network timeout handling"
• "OVERRIDE: Use alternative endpoint: /api/backup/v2"
• "DEBUG: Add logging for memory allocation at line 142"

The agent will use this correction alongside its investigation prompt.`}
                    rows={6}
                    className="w-full bg-slate-900 border border-purple-500/30 rounded-lg px-4 py-3 text-sm text-white focus:border-purple-400 focus:outline-none focus:shadow-lg focus:shadow-purple-500/20"
                  />
                </div>

                <div className="mb-4 p-3 bg-purple-500/10 border border-purple-500/30 rounded-lg">
                  <div className="text-xs text-purple-300 font-medium mb-1">👁️ Agent Will Receive</div>
                  <div className="text-sm text-purple-100 font-mono">
                    🔧 Manual Override:
                    {correctionInput && ` ${correctionInput}`}
                    {!correctionInput && ` [Waiting for correction input...]`}
                  </div>
                </div>

                <div className="flex gap-3">
                  <button
                    onClick={() => injectManualCorrection()}
                    disabled={!correctionInput.trim()}
                    className="flex-1 py-3 bg-gradient-to-r from-purple-500/20 to-pink-500/20 border border-purple-500/30 rounded-xl font-black text-sm uppercase tracking-wider hover:from-purple-500/30 hover:to-pink-500/30 transition-all disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
                  >
                    <Terminal size={16} className="text-purple-400" />
                    🚀 Inject to Raven Agent
                  </button>
                  <button
                    onClick={() => {
                      setShowCorrectionModal(false);
                      setSelectedMission(null);
                    }}
                    className="px-6 py-3 bg-slate-800 border border-slate-600 rounded-xl font-black text-sm uppercase tracking-wider hover:bg-slate-700 transition-all"
                  >
                    ❌ Cancel
                  </button>
                </div>
              </>
            )
}
          </div>
        </div>
      )}

      <RavenAuditLog 
        isOpen={isAuditLogOpen} 
        onClose={() => setIsAuditLogOpen(false)} 
      />

      <RavenLiveTrace
        isOpen={liveMissionId !== null}
        onClose={() => setLiveMissionId(null)}
        missionId={liveMissionId}
      />
    </div>
  </div>
  );
}
