import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ShieldAlert, Search, Terminal } from 'lucide-react';
import toast from 'react-hot-toast';
import { api, apiClient, type RavenMission } from '../../services/api';
import HelpTooltip from '../ui/HelpTooltip';

interface RavenWorkspaceInvestigationPanelProps {
  workspaceId: string;
  workspaceName: string;
}

export default function RavenWorkspaceInvestigationPanel({ 
  workspaceId, 
  workspaceName 
}: RavenWorkspaceInvestigationPanelProps) {
  const queryClient = useQueryClient();
  
  // Investigation state management (workspace-scoped)
  const [investigationPrompt, setInvestigationPrompt] = useState('');
  const [showTemplates, setShowTemplates] = useState(false);
  const [isInvestigating, setIsInvestigating] = useState(false);
  const [investigationStartTime, setInvestigationStartTime] = useState<number | null>(null);
  const [showCorrectionModal, setShowCorrectionModal] = useState(false);
  const [correctionInput, setCorrectionInput] = useState('');
  const [selectedMission, setSelectedMission] = useState<RavenMission | null>(null);
  const [correctionContext, setCorrectionContext] = useState<{ message: string; timestamp: string; level: string; } | null>(null);

  const { data: missions = [] } = useQuery<RavenMission[]>({
    queryKey: ['workspace-raven-missions', workspaceId],
    queryFn: () => api.getWorkspaceRavenMissions(workspaceId),
    refetchInterval: 3000,
  });

  const investigationTemplates = [
    {
      id: 'debug-memory',
      name: '🐛 Debug Memory Issue',
      description: 'Memory leak detection and analysis',
      prompt: `Analyze the workspace for potential memory leaks in services and identify patterns that may indicate gradual memory consumption. Focus on:
      - Service-level memory usage patterns
      - Resource allocation anomalies  
      - Garbage collection efficiency
      - Memory growth trends over time`
    },
    {
      id: 'investigate-timeout',
      name: '⏱️ Investigate Timeout',
      description: 'Timeout pattern analysis and resolution',
      prompt: `Investigate timeout patterns in the workspace environment. Look for:
      - Slow-running processes or operations
      - Network latency issues between services
      - Database query performance bottlenecks
      - Resource contention and queue delays`
    },
    {
      id: 'security-audit',
      name: '🔐 Security Audit', 
      description: 'Security vulnerability assessment',
      prompt: `Conduct a comprehensive security audit of the workspace. Examine:
      - Authentication and authorization mechanisms
      - Data encryption and access controls
      - Network security configurations
      - Security event logs and anomalies`
    },
    {
      id: 'performance-optimization',
      name: '📊 Performance Optimization',
      description: 'Bottleneck analysis and optimization',
      prompt: `Analyze workspace performance bottlenecks and optimization opportunities. Focus on:
      - System resource utilization (CPU, memory, disk I/O)
      - Network throughput and latency
      - Database query optimization
      - Application scaling and load balancing`
    },
    {
      id: 'configuration-issue',
      name: '🔧 Configuration Issue',
      description: 'Configuration validation and troubleshooting',
      prompt: `Validate workspace configuration and troubleshoot configuration-related issues. Check:
      - Service configuration files and settings
      - Environment variables and secrets management
      - Network and database connectivity
      - Resource limits and quotas`
    },
    {
      id: 'network-connectivity',
      name: '📡 Network Connectivity',
      description: 'Network troubleshooting and diagnostics',
      prompt: `Diagnose network connectivity and infrastructure issues within the workspace. Investigate:
      - Network latency and bandwidth utilization
      - DNS resolution and service discovery
      - Firewall and security group configurations
      - Load balancer and proxy configurations`
    }
  ];

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
        workspace_id: workspaceId,
        mission_id: selectedMission?.id || null,
        type: 'workspace_manual'
      });
      
      toast.success(`✅ Workspace investigation started successfully!`);
      
      if (response.data?.mission_id) {
        queryClient.invalidateQueries({ queryKey: ['workspace-raven-missions', workspaceId] });
      }
      
      setInvestigationPrompt('');
      
    } catch (error) {
      console.error('Workspace investigation error:', error);
      toast.error('❌ Failed to start workspace investigation');
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
        workspace_id: workspaceId,
        mission_id: selectedMission.id,
        correction: correctionInput,
        context: correctionContext
      });
      
      toast.success('🎯 Manual correction injected into Raven agent!');
      
      setCorrectionInput('');
      setShowCorrectionModal(false);
      setSelectedMission(null);
      queryClient.invalidateQueries({ queryKey: ['workspace-raven-missions', workspaceId] });
      
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
          <h4 className="flex items-center gap-2 text-lg font-bold text-white">
            <ShieldAlert size={18} className="text-emerald-400" />
            Investigation - {workspaceName}
          </h4>
          <p className="text-sm text-slate-400">Workspace-scoped manual investigation and correction.</p>
        </div>
        <HelpTooltip docName="raven_ops_implementation.md" sectionTitle="Workspace Investigation" label="Workspace Investigation" />
      </div>

      <div className="glass-card p-4 border border-white/10">
        <h5 className="text-sm font-black uppercase tracking-widest text-slate-500 mb-3">
          Investigation Templates
        </h5>
        <div className="grid gap-2">
          {investigationTemplates.map((template) => (
            <button
              key={template.id}
              onClick={() => {
                setInvestigationPrompt(template.prompt);
                setShowTemplates(false);
              }}
              className="p-3 bg-slate-800/50 border border-slate-700/50 rounded-lg hover:border-emerald-500/50 hover:bg-emerald-500/10 transition-all text-left"
            >
              <div className="flex items-center justify-between mb-1">
                <span className="text-sm font-bold text-white">{template.name}</span>
                <span className="text-xs text-slate-500">{template.description}</span>
              </div>
            </button>
          ))}
        </div>
        <button
          onClick={() => setShowTemplates(!showTemplates)}
          className="w-full mt-3 text-xs text-emerald-400 hover:text-emerald-300 transition-colors"
        >
          {showTemplates ? 'Hide Templates' : 'Show Templates'}
        </button>
      </div>

      <div className="glass-card p-4 border border-white/10">
        <h5 className="text-sm font-black uppercase tracking-widest text-slate-500 mb-3">
          Investigation Controls
        </h5>
        
        <div className="space-y-3">
          <textarea
            value={investigationPrompt}
            onChange={(e) => setInvestigationPrompt(e.target.value)}
            placeholder="Enter investigation prompt or use templates above..."
            className="w-full p-3 bg-slate-900 border border-slate-700 rounded-lg text-sm text-white placeholder-slate-500 focus:border-emerald-500 focus:outline-none resize-none"
            rows={3}
          />
          
          <div className="flex gap-3">
            <button
              onClick={startManualInvestigation}
              disabled={!investigationPrompt.trim() || isInvestigating}
              className={`flex-1 py-2 px-4 rounded-lg font-black text-xs uppercase tracking-wider transition-all duration-300 disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2 ${investigationBtnClass}`}
            >
              {isInvestigating ? (
                <>
                  <div className="w-3 h-3 border-2 border-emerald-300 border-t-transparent rounded-full animate-spin"></div>
                  Starting...
                </>
              ) : (
                <>
                  <Search size={14} className="text-emerald-400" />
                  🚀 Start Investigation
                </>
              )}
            </button>
            
            {(activeMissions.length > 0 || investigationPrompt) && (
              <button
                onClick={() => setShowCorrectionModal(true)}
                className="px-3 py-2 bg-gradient-to-r from-purple-500/20 to-pink-500/20 border border-purple-500/30 rounded-lg font-black text-xs uppercase tracking-wider hover:from-purple-500/30 hover:to-pink-500/30 transition-all flex items-center gap-2"
              >
                <Terminal size={14} className="text-purple-400" />
                📝 Inject Correction
              </button>
            )}
            
            {(investigationPrompt || showCorrectionModal) && (
              <button
                onClick={cancelInvestigation}
                className="px-3 py-2 bg-red-500/10 border border-red-500/30 rounded-lg font-black text-xs uppercase tracking-wider hover:bg-red-500/20 transition-all"
              >
                ✖️ Cancel
              </button>
            )}
          </div>
        </div>
        
        {isInvestigating && (
          <div className="mt-3 p-2 bg-emerald-500/10 border border-emerald-500/30 rounded-lg">
            <div className="flex items-center justify-between">
              <span className="text-xs text-emerald-300 font-medium">
                🔍 Workspace investigation in progress...
              </span>
              <span className="text-xs text-slate-500">
                Started: {investigationStartTime ? new Date(investigationStartTime).toLocaleTimeString() : '—'}
              </span>
            </div>
          </div>
        )}
      </div>

      {activeMissions.length > 0 && (
        <div className="glass-card p-4 border border-white/10">
          <h5 className="text-sm font-black uppercase tracking-widest text-slate-500 mb-3">
            Active Missions for Correction Injection
          </h5>
          <div className="space-y-2 max-h-32 overflow-y-auto">
            {activeMissions.map((mission) => (
              <button
                key={mission.id}
                onClick={() => {
                  setSelectedMission(mission);
                  setShowCorrectionModal(true);
                  setCorrectionContext({
                    message: `Workspace ${workspaceName} intervention request`,
                    timestamp: new Date().toISOString(),
                    level: 'manual'
                  });
                }}
                className="w-full p-2 bg-slate-800/50 border border-slate-700/50 rounded-lg hover:border-purple-500/50 hover:bg-purple-500/10 transition-all text-left"
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs font-bold text-white">Mission #{mission.id}</span>
                  <span className={`px-2 py-1 rounded text-[10px] font-black uppercase ${
                    mission.status === 'running' ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30' : 'bg-slate-700/50 text-slate-300'
                  }`}
                  >
                    {mission.status}
                  </span>
                </div>
              </button>
            ))}
          </div>
        </div>
      )}

      {showCorrectionModal && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center p-4 transition-all">
          <div className="bg-slate-900 border border-purple-500/30 rounded-2xl p-6 max-w-2xl w-full max-h-[80vh] overflow-y-auto shadow-2xl">
            
            <div className="flex items-center justify-between mb-6">
              <h3 className="text-xl font-black text-white flex items-center gap-2">
                <Terminal size={20} className="text-purple-400" />
                🔧 Workspace Correction Injector
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

            <div className="mb-4 p-3 bg-purple-500/10 border border-purple-500/30 rounded-lg">
              <span className="text-sm text-purple-300">
                Target: Workspace <strong>{workspaceName}</strong> - Mission #{selectedMission?.id}
              </span>
            </div>

            <textarea
              value={correctionInput}
              onChange={(e) => setCorrectionInput(e.target.value)}
              placeholder="Enter manual correction for the Raven agent..."
              className="w-full p-3 bg-slate-900 border border-slate-700 rounded-lg text-sm text-white placeholder-slate-500 focus:border-purple-500 focus:outline-none resize-none mb-4"
              rows={4}
            />

            <div className="flex gap-3">
              <button
                onClick={injectManualCorrection}
                disabled={!correctionInput.trim()}
                className="flex-1 py-3 bg-gradient-to-r from-purple-500 to-pink-500 rounded-xl font-black text-sm uppercase tracking-wider hover:from-purple-600 hover:to-pink-600 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
              >
                📝 Inject Correction
              </button>
              <button
                onClick={() => {
                  setShowCorrectionModal(false);
                  setSelectedMission(null);
                }}
                className="px-6 py-3 bg-slate-700/50 border border-slate-600 rounded-xl font-black text-sm uppercase tracking-wider hover:bg-slate-600 transition-all"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
