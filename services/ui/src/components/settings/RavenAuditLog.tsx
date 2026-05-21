import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { History, FileText, CheckCircle, XCircle } from 'lucide-react';
import { api, type RavenMission } from '../../services/api';
import Modal from '../ui/Modal';

interface RavenAuditLogProps {
  isOpen: boolean;
  onClose: () => void;
}

export default function RavenAuditLog({ isOpen, onClose }: RavenAuditLogProps) {
  const [selectedMission, setSelectedMission] = useState<RavenMission | null>(null);

  const { data: missions = [], isLoading } = useQuery<RavenMission[]>({
    queryKey: ['raven-missions-audit'],
    queryFn: () => api.getAdminRavenQueue(),
    enabled: isOpen,
  });

  const auditMissions = missions.filter(
    (m) => m.status === 'completed' || m.status === 'failed'
  ).sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());

  const renderLog = (logData: string | null | undefined) => {
    if (!logData) return <div className="text-slate-500 italic">No execution log available.</div>;
    try {
      const parsed = JSON.parse(logData);
      if (Array.isArray(parsed)) {
        return (
          <div className="space-y-2">
            {parsed.map((entry, idx) => {
              const timeStr = entry.timestamp ? new Date(entry.timestamp * 1000).toISOString().split('T')[1].slice(0,-1) : '';
              let textColor = 'text-slate-400';
              if (entry.type === 'action') textColor = 'text-yellow-400 font-bold';
              else if (entry.type === 'action_payload') textColor = 'text-yellow-300/80';
              else if (entry.type === 'result_success') textColor = 'text-emerald-400 font-bold';
              else if (entry.type === 'result_error') textColor = 'text-red-400 font-bold';
              else if (entry.type === 'reasoning') textColor = 'text-blue-400';
              
              return (
                <div key={idx} className={textColor}>
                  <span className="opacity-50 select-none mr-2">[{timeStr}]</span>
                  {entry.type === 'action_payload' ? (
                    <div className="pl-6 mt-1 mb-2">
                      <div className="bg-white/5 border-l-2 border-yellow-500/50 p-2 rounded-r text-xs">
                        {entry.data}
                      </div>
                    </div>
                  ) : (
                    <span>{entry.data}</span>
                  )}
                </div>
              );
            })}
          </div>
        );
      }
    } catch {
      // Not JSON, just return raw string
    }
    return <div>{logData}</div>;
  };

  return (
    <Modal isOpen={isOpen} onClose={onClose} title="Raven Audit Log" size="4xl">
      <div className="flex h-[600px] gap-4">
        {/* Left Side: Mission List */}
        <div className="w-1/3 flex flex-col border-r border-white/10 pr-4">
          {isLoading ? (
            <div className="text-slate-500 text-sm italic p-4">Loading audit log...</div>
          ) : auditMissions.length === 0 ? (
            <div className="text-slate-500 text-sm p-4 text-center">No historical missions found.</div>
          ) : (
            <div className="overflow-y-auto space-y-2 pr-2 custom-scrollbar flex-1">
              {auditMissions.map((mission) => (
                <button
                  key={mission.id}
                  onClick={() => setSelectedMission(mission)}
                  className={`w-full text-left p-3 rounded-lg border transition ${
                    selectedMission?.id === mission.id
                      ? 'bg-white/10 border-white/20'
                      : 'bg-black/20 border-white/5 hover:bg-white/5'
                  }`}
                >
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-xs font-bold text-white truncate max-w-[120px]">
                      {mission.target_container || 'System'}
                    </span>
                    {mission.status === 'completed' ? (
                      <CheckCircle size={14} className="text-emerald-400" />
                    ) : (
                      <XCircle size={14} className="text-red-400" />
                    )}
                  </div>
                  <div className="text-[10px] text-slate-400 truncate mb-1">
                    {mission.error_summary || mission.proposed_mission}
                  </div>
                  <div className="text-[9px] text-slate-500 uppercase tracking-widest">
                    {new Date(mission.created_at).toLocaleString()}
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Right Side: Mission Details */}
        <div className="w-2/3 flex flex-col pl-2 min-w-0">
          {selectedMission ? (
            <div className="flex flex-col h-full">
              <div className="mb-4">
                <div className="flex items-center gap-3 mb-2">
                  <span className={`px-2 py-0.5 rounded text-[10px] font-black uppercase tracking-widest ${
                    selectedMission.status === 'completed' ? 'bg-emerald-500/10 text-emerald-400' : 'bg-red-500/10 text-red-400'
                  }`}>
                    {selectedMission.status}
                  </span>
                  <h3 className="text-lg font-bold text-white">Mission #{selectedMission.id}</h3>
                </div>
                <p className="text-sm text-slate-300">{selectedMission.proposed_mission}</p>
                {selectedMission.error_summary && (
                  <div className="mt-2 text-xs text-slate-400 p-2 bg-black/30 rounded border border-white/5">
                    <strong>Trigger:</strong> {selectedMission.error_summary}
                  </div>
                )}
              </div>

              <div className="flex-1 flex flex-col min-h-0 space-y-4">
                <div className="flex-1 flex flex-col min-h-0 bg-black/40 border border-white/10 rounded-lg overflow-hidden">
                  <div className="bg-white/5 px-3 py-1.5 border-b border-white/10 flex items-center gap-2">
                    <FileText size={12} className="text-slate-400" />
                    <span className="text-[10px] font-bold uppercase tracking-widest text-slate-400">Execution Log</span>
                  </div>
                  <div className="flex-1 overflow-y-auto p-3 custom-scrollbar text-xs font-mono text-slate-300 whitespace-pre-wrap">
                    {renderLog(selectedMission.output_log)}
                  </div>
                </div>

                {selectedMission.result && (
                  <div className="h-1/3 flex flex-col min-h-0 bg-black/40 border border-white/10 rounded-lg overflow-hidden">
                    <div className="bg-white/5 px-3 py-1.5 border-b border-white/10 flex items-center gap-2">
                      <FileText size={12} className="text-slate-400" />
                      <span className="text-[10px] font-bold uppercase tracking-widest text-slate-400">Final Result</span>
                    </div>
                    <div className="flex-1 overflow-y-auto p-3 custom-scrollbar text-xs font-mono text-emerald-300/80 whitespace-pre-wrap">
                      {selectedMission.result}
                    </div>
                  </div>
                )}
              </div>
            </div>
          ) : (
            <div className="flex-1 flex flex-col items-center justify-center text-slate-500">
              <History size={48} className="mb-4 opacity-20" />
              <p className="text-sm">Select a mission from the audit log to view details.</p>
            </div>
          )}
        </div>
      </div>
    </Modal>
  );
}
