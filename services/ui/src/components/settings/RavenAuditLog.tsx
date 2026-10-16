import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ArrowLeft, CheckCircle, Download, History, XCircle } from 'lucide-react';
import { api, type RavenMission } from '../../services/api';
import { downloadMissionLog, parseMissionLog } from '../../lib/missionLog';
import Modal from '../ui/Modal';
import { MissionTimeline } from './MissionTimeline';

interface RavenAuditLogProps {
  isOpen: boolean;
  onClose: () => void;
}

const AUDITED = ['completed', 'failed', 'running', 'executing'];

const formatTs = (iso?: string | null) => (iso ? new Date(iso).toLocaleString() : '—');
const formatDuration = (seconds?: number | null) => {
  if (seconds == null) return '—';
  const m = Math.floor(seconds / 60);
  return m > 0 ? `${m}m ${seconds % 60}s` : `${seconds % 60}s`;
};

/** Every mission Raven has run, with its full execution log. */
export default function RavenAuditLog({ isOpen, onClose }: RavenAuditLogProps) {
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const { data: missions = [], isLoading } = useQuery<RavenMission[]>({
    queryKey: ['raven-missions-audit'],
    queryFn: () => api.getAdminRavenQueue(),
    enabled: isOpen,
    refetchInterval: 5000,
  });

  // The log and result come from the mission itself, not the list row.
  const { data: selected } = useQuery({
    queryKey: ['raven-mission', selectedId],
    queryFn: () => api.getRavenMission(selectedId as number),
    enabled: isOpen && selectedId != null,
    refetchInterval: (q) => (['running', 'executing'].includes(q.state.data?.status ?? '') ? 4000 : false),
  });

  const auditMissions = missions
    .filter((m) => AUDITED.includes(m.status))
    .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());

  const events = parseMissionLog(selected?.output_log);

  return (
    <Modal isOpen={isOpen} onClose={onClose} title="Raven Audit Log" size="5xl">
      {/* Phones show the list or one mission; wider screens show both side by side. */}
      <div className="flex flex-col md:flex-row h-[75vh] md:h-[600px] gap-4 p-4 sm:p-6 min-h-0">
        <div className={`${selectedId != null ? 'hidden md:flex' : 'flex'} md:w-1/3 flex-col md:border-r border-white/10 md:pr-4 min-h-0`}>
          {isLoading && missions.length === 0 ? (
            <div className="text-slate-500 text-sm italic p-4">Loading audit log…</div>
          ) : auditMissions.length === 0 ? (
            <div className="text-slate-500 text-sm p-4 text-center">No missions have run yet.</div>
          ) : (
            <div className="overflow-y-auto space-y-2 pr-1 custom-scrollbar flex-1">
              {auditMissions.map((mission) => (
                <button
                  key={mission.id}
                  onClick={() => setSelectedId(mission.id)}
                  className={`w-full text-left p-3 min-h-11 rounded-lg border transition ${
                    selectedId === mission.id ? 'bg-white/10 border-white/20' : 'bg-black/20 border-white/5 hover:bg-white/5'
                  }`}
                >
                  <div className="flex items-center justify-between gap-2 mb-1">
                    <span className="text-xs font-bold text-white truncate">
                      #{mission.id} · {mission.target_container || mission.mission_type}
                    </span>
                    {mission.status === 'completed' ? (
                      <CheckCircle size={14} className="text-emerald-400 shrink-0" aria-label="completed" />
                    ) : mission.status === 'failed' ? (
                      <XCircle size={14} className="text-red-400 shrink-0" aria-label="failed" />
                    ) : (
                      <span className="w-2.5 h-2.5 rounded-full bg-emerald-400 animate-pulse shrink-0" aria-label="running" />
                    )}
                  </div>
                  <div className="text-[11px] text-slate-400 truncate mb-1">{mission.error_summary || mission.proposed_mission}</div>
                  <div className="text-[10px] text-slate-500">{formatTs(mission.created_at)}</div>
                </button>
              ))}
            </div>
          )}
        </div>

        <div className={`${selectedId == null ? 'hidden md:flex' : 'flex'} md:w-2/3 flex-col min-w-0 min-h-0`}>
          {selectedId == null ? (
            <div className="flex-1 flex flex-col items-center justify-center text-slate-500">
              <History size={48} className="mb-4 opacity-20" />
              <p className="text-sm">Choose a mission to see its log.</p>
            </div>
          ) : !selected ? (
            <p className="text-sm text-slate-500 p-4">Loading mission #{selectedId}…</p>
          ) : (
            <div className="flex flex-col h-full min-h-0 gap-3">
              <div>
                <button
                  onClick={() => setSelectedId(null)}
                  className="md:hidden mb-2 min-h-11 inline-flex items-center gap-1 text-xs text-slate-300"
                >
                  <ArrowLeft size={14} /> All missions
                </button>
                <div className="flex flex-wrap items-center gap-3 mb-2">
                  <span className={`px-2 py-0.5 rounded text-[10px] font-black uppercase tracking-widest ${
                    selected.status === 'completed' ? 'bg-emerald-500/10 text-emerald-400' :
                    selected.status === 'failed' ? 'bg-red-500/10 text-red-400' : 'bg-blue-500/10 text-blue-400'
                  }`}>
                    {selected.status}
                  </span>
                  <h3 className="text-lg font-bold text-white">Mission #{selected.id}</h3>
                  <button
                    onClick={() => downloadMissionLog(selected.id, events, selected.result)}
                    className="ml-auto inline-flex items-center gap-1.5 min-h-11 px-3 rounded text-[11px] font-bold uppercase tracking-widest bg-white/10 hover:bg-white/20 border border-white/10 text-slate-200"
                  >
                    <Download size={12} /> Download
                  </button>
                </div>
                <p className="text-sm text-slate-300 line-clamp-3 break-words">{selected.proposed_mission}</p>
                <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-[11px] text-slate-400">
                  <div><span className="text-slate-500">Queued:</span> {formatTs(selected.queued_at ?? selected.created_at)}</div>
                  <div><span className="text-slate-500">Started:</span> {formatTs(selected.started_at)}</div>
                  <div><span className="text-slate-500">Finished:</span> {formatTs(selected.completed_at)}</div>
                  <div><span className="text-slate-500">Run time:</span> {formatDuration(selected.duration)}</div>
                </div>
                {selected.error_summary && (
                  <div className="mt-2 text-xs text-slate-400 p-2 bg-black/30 rounded border border-white/5">
                    <strong>Trigger:</strong> {selected.error_summary}
                  </div>
                )}
              </div>

              <div className="flex-1 min-h-0 overflow-y-auto p-3 custom-scrollbar bg-black/40 border border-white/10 rounded-lg">
                <MissionTimeline events={events} showPayloads emptyText="No execution log was recorded." />
              </div>

              {selected.result && (
                <div className="max-h-[30%] overflow-y-auto p-3 custom-scrollbar text-xs font-mono text-emerald-300/80 whitespace-pre-wrap break-words bg-black/40 border border-white/10 rounded-lg">
                  {selected.result}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </Modal>
  );
}
