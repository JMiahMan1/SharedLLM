import { useEffect, useRef, useState } from 'react';
import { Terminal, Loader, Download } from 'lucide-react';
import Modal from '../ui/Modal';
import { api } from '../../services/api';
import { storageGetSync } from '../../lib/storage';
import { downloadMissionLog, type MissionLogEvent } from '../../lib/missionLog';
import { MissionTimeline } from './MissionTimeline';

interface RavenLiveTraceProps {
  isOpen: boolean;
  onClose: () => void;
  missionId: number | null;
}

const TERMINAL = ['completed', 'failed', 'cancelled'];

/** A running mission's events as they happen (WebSocket), with the stored log as fallback. */
export default function RavenLiveTrace({ isOpen, onClose, missionId }: RavenLiveTraceProps) {
  const [logs, setLogs] = useState<MissionLogEvent[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const [missionResult, setMissionResult] = useState<string | null>(null);
  const [missionStatus, setMissionStatus] = useState<string>('');
  const scrollRef = useRef<HTMLDivElement>(null);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!isOpen || !missionId) return;

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const token = storageGetSync('jarvis_api_key') || '';
    const wsUrl = `${protocol}//${window.location.host}/api/raven/missions/${missionId}/stream?token=${encodeURIComponent(token)}`;
    let stopped = false;

    const fetchHttpLogs = () => {
      api.getMissionLogs(missionId).then((resp) => {
        if (stopped || !resp || !Array.isArray(resp.logs)) return;
        setLogs(resp.logs.map((line) => {
          try {
            return JSON.parse(line) as MissionLogEvent;
          } catch {
            return { type: 'system', data: line };
          }
        }));
      }).catch(() => {});
    };

    // Deferred so the effect does not set state synchronously.
    requestAnimationFrame(() => {
      if (!stopped) setLogs([{ type: 'system', data: `Connecting to mission #${missionId}…` }]);
    });
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      setIsConnected(true);
      setLogs([]); // the server replays the history, then streams
    };
    ws.onmessage = (event) => {
      let parsed: MissionLogEvent;
      try {
        parsed = JSON.parse(event.data);
      } catch {
        parsed = { type: 'system', data: String(event.data) };
      }
      setLogs((prev) => [...prev, parsed]);
    };
    ws.onclose = () => {
      if (stopped) return;
      setIsConnected(false);
      fetchHttpLogs();
    };

    const pollInterval = setInterval(() => {
      api.getRavenMission(missionId).then((mission) => {
        if (stopped || !mission) return;
        setMissionStatus(mission.status);
        if (mission.result) setMissionResult(mission.result);
      }).catch(() => {});
      if (wsRef.current?.readyState !== WebSocket.OPEN) fetchHttpLogs();
    }, 4000);

    return () => {
      stopped = true;
      clearInterval(pollInterval);
      ws.close();
    };
  }, [isOpen, missionId]);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [logs]);

  const finished = TERMINAL.includes(missionStatus);

  return (
    <Modal isOpen={isOpen} onClose={onClose} title={`Mission #${missionId ?? ''} (live)`} size="4xl">
      <div className="flex flex-col h-[70vh] sm:h-[600px] bg-black border-t border-white/10 overflow-hidden text-sm">
        <div className="bg-white/5 border-b border-white/10 px-4 py-1 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-3">
            <Terminal size={14} className="text-slate-400" />
            {isConnected ? (
              <span className="flex items-center gap-1.5 text-[10px] text-emerald-400 uppercase tracking-widest">
                <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" /> Live
              </span>
            ) : (
              <span className="flex items-center gap-1.5 text-[10px] text-slate-400 uppercase tracking-widest">
                <span className="w-2 h-2 rounded-full bg-slate-500" /> {finished ? 'Finished' : 'Polling saved log'}
              </span>
            )}
          </div>
          <button
            onClick={() => missionId && downloadMissionLog(missionId, logs, missionResult)}
            className="min-h-11 px-3 flex items-center gap-1 text-[10px] font-black uppercase tracking-widest text-slate-400 hover:text-slate-200"
          >
            <Download size={12} /> Download
          </button>
        </div>

        <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 custom-scrollbar">
          <MissionTimeline events={logs} showPayloads emptyText="No events yet." />
          {isConnected && !finished && (
            <div className="flex items-center gap-2 text-slate-500 pt-2 opacity-50 text-xs">
              <Loader size={12} className="animate-spin" /> Waiting for the next step…
            </div>
          )}
        </div>

        {finished && missionResult && (
          <div className="border-t border-white/10">
            <div className="bg-white/5 px-3 py-1.5 border-b border-white/10 flex items-center gap-2">
              <span className={`px-2 py-0.5 rounded text-[10px] font-black uppercase tracking-widest ${
                missionStatus === 'completed' ? 'bg-emerald-500/10 text-emerald-400' : 'bg-red-500/10 text-red-400'
              }`}>
                {missionStatus}
              </span>
              <span className="text-[10px] font-bold uppercase tracking-widest text-slate-400">Result</span>
            </div>
            <div className="max-h-32 overflow-y-auto p-3 custom-scrollbar text-xs font-mono text-emerald-300/80 whitespace-pre-wrap break-words bg-black/30">
              {missionResult}
            </div>
          </div>
        )}
      </div>
    </Modal>
  );
}
