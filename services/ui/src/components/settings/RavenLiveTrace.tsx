import { useEffect, useRef, useState } from 'react';
import { Terminal, Loader } from 'lucide-react';
import Modal from '../ui/Modal';

interface RavenLiveTraceProps {
  isOpen: boolean;
  onClose: () => void;
  missionId: number | null;
}

interface StreamEvent {
  type: string;
  data: string;
}

export default function RavenLiveTrace({ isOpen, onClose, missionId }: RavenLiveTraceProps) {
  const [logs, setLogs] = useState<StreamEvent[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!isOpen || !missionId) return;

    setLogs([{ type: 'system', data: `Initializing connection to Raven Mission #${missionId}...` }]);
    
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const token = localStorage.getItem('jarvis_api_key') || '';
    const wsUrl = `${protocol}//${window.location.host}/api/raven/missions/${missionId}/stream?token=${encodeURIComponent(token)}`;

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      setIsConnected(true);
      setLogs((prev) => [...prev, { type: 'system', data: `Connection established. Listening to stream...` }]);
    };

    ws.onmessage = (event) => {
      try {
        const parsed = JSON.parse(event.data);
        setLogs((prev) => [...prev, parsed]);
      } catch {
        setLogs((prev) => [...prev, { type: 'system', data: `Unknown output: ${event.data}` }]);
      }
    };

    ws.onerror = () => {
      setLogs((prev) => [...prev, { type: 'result_error', data: `WebSocket error occurred.` }]);
    };

    ws.onclose = () => {
      setIsConnected(false);
      setLogs((prev) => [...prev, { type: 'system', data: `Connection closed.` }]);
    };

    return () => {
      if (ws.readyState === 1) ws.close();
    };
  }, [isOpen, missionId]);

  // Auto-scroll
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [logs]);

  const getLogColor = (type: string) => {
    switch (type) {
      case 'reasoning':
        return 'text-blue-400';
      case 'action':
        return 'text-yellow-400 font-bold';
      case 'action_payload':
        return 'text-yellow-300/80';
      case 'result_success':
        return 'text-emerald-400 font-bold';
      case 'result_error':
        return 'text-red-400 font-bold';
      case 'system':
      default:
        return 'text-slate-400';
    }
  };

  const renderLogData = (log: StreamEvent) => {
    if (log.type === 'action_payload') {
      try {
        const parsed = JSON.parse(log.data);
        return (
          <div className="pl-6 mt-1 mb-2">
            <div className="bg-white/5 border-l-2 border-yellow-500/50 p-3 rounded-r text-xs font-mono">
              <pre className="whitespace-pre-wrap break-words text-yellow-300/80">
                {JSON.stringify(parsed, null, 2)}
              </pre>
            </div>
          </div>
        );
      } catch {
        return (
          <div className="pl-6 mt-1 mb-2">
            <div className="bg-white/5 border-l-2 border-yellow-500/50 p-2 rounded-r text-xs">
              {log.data}
            </div>
          </div>
        );
      }
    }

    if (log.type === 'reasoning') {
      return <span className="italic opacity-80">{log.data}</span>;
    }

    return <span>{log.data}</span>;
  };

  return (
    <Modal isOpen={isOpen} onClose={onClose} title={`Raven Live Trace - Mission #${missionId}`} size="4xl">
      <div className="flex flex-col h-[600px] bg-black border border-white/10 rounded-lg overflow-hidden font-mono text-sm shadow-inner shadow-black/50">
        {/* Header Bar */}
        <div className="bg-white/5 border-b border-white/10 px-4 py-2 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Terminal size={14} className="text-slate-400" />
            <span className="text-xs uppercase tracking-widest text-slate-400 font-bold">Terminal Output</span>
          </div>
          <div className="flex items-center gap-2">
            {isConnected ? (
              <span className="flex items-center gap-1.5 text-[10px] text-emerald-400 uppercase tracking-widest">
                <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
                Connected
              </span>
            ) : (
              <span className="flex items-center gap-1.5 text-[10px] text-red-400 uppercase tracking-widest">
                <span className="w-2 h-2 rounded-full bg-red-400"></span>
                Disconnected
              </span>
            )}
          </div>
        </div>

        {/* Terminal Window */}
        <div 
          ref={scrollRef}
          className="flex-1 overflow-y-auto p-4 space-y-1.5 custom-scrollbar"
        >
          {logs.map((log, i) => (
            <div key={i} className={`whitespace-pre-wrap ${getLogColor(log.type)}`}>
              <span className="opacity-50 select-none mr-2 text-xs">[{new Date().toISOString().split('T')[1].slice(0, -1)}]</span>
              {renderLogData(log)}
            </div>
          ))}
          {isConnected && (
            <div className="flex items-center gap-2 text-slate-500 pt-2 opacity-50">
              <Loader size={12} className="animate-spin" /> Waiting for telemetry...
            </div>
          )}
        </div>
      </div>
    </Modal>
  );
}
