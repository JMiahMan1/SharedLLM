import { useState, useEffect, useMemo } from 'react';
import { Timer, X, Plus } from 'lucide-react';
import type { UserWidgetSettings } from '../../types/widget';

interface AmbientTimerWidgetProps {
  userSettings: UserWidgetSettings;
  onTogglePin: () => void;
}

interface ActiveTimer {
  id: string;
  title: string;
  durationMs: number;
  remainingMs: number;
  createdAt: number;
}

const AmbientTimerWidget = ({ userSettings, onTogglePin }: AmbientTimerWidgetProps) => {
  const [timers, setTimers] = useState<ActiveTimer[]>([]);
  const [newDuration, setNewDuration] = useState(300);
  const [newTitle, setNewTitle] = useState('');

  useEffect(() => {
    const interval = setInterval(() => {
      setTimers((prev) =>
        prev
          .map((t) => ({ ...t, remainingMs: t.remainingMs - 1000 }))
          .filter((t) => t.remainingMs > 0)
      );
    }, 1000);
    return () => clearInterval(interval);
  }, []);

  const formatTime = (ms: number) => {
    const seconds = Math.max(0, Math.floor(ms / 1000));
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
    return `${m}:${String(s).padStart(2, '0')}`;
  };

  const totalProgress = useMemo(() => {
    if (timers.length === 0) return 0;
    return timers.reduce((sum, t) => sum + (1 - t.remainingMs / t.durationMs), 0) / timers.length * 100;
  }, [timers]);

  const addTimer = () => {
    const timer: ActiveTimer = {
      id: Date.now().toString(),
      title: newTitle || `Timer ${timers.length + 1}`,
      durationMs: newDuration * 1000,
      remainingMs: newDuration * 1000,
      createdAt: Date.now(),
    };
    setTimers((prev) => [...prev, timer]);
    setNewTitle('');
    setNewDuration(300);
  };

  const removeTimer = (id: string) => {
    setTimers((prev) => prev.filter((t) => t.id !== id));
  };

  return (
    <div className="glass-card h-full p-5 relative">
      <button
        onClick={onTogglePin}
        className="absolute top-3 right-3 text-slate-500 hover:text-purple-400 transition-colors"
        title={userSettings.is_pinned ? 'Unpin widget' : 'Pin widget'}
      >
        <Timer size={16} className={userSettings.is_pinned ? 'text-purple-400' : ''} />
      </button>

      <h3 className="text-lg font-bold text-white mb-4 flex items-center gap-2">
        <Timer size={18} className="text-purple-400" />
        Ambient Timer
      </h3>

      {timers.length > 0 && (
        <div className="space-y-3 mb-4">
          {timers.map((timer) => (
            <div key={timer.id} className="glass-card p-3">
              <div className="flex items-center justify-between mb-2">
                <span className="text-sm font-semibold text-white">{timer.title}</span>
                <button onClick={() => removeTimer(timer.id)} className="text-slate-500 hover:text-red-400 transition-colors">
                  <X size={14} />
                </button>
              </div>
              <div className="w-full h-2 bg-slate-800 rounded-full overflow-hidden mb-1">
                <div
                  className="h-full bg-gradient-to-r from-purple-500 to-purple-400 rounded-full transition-all duration-1000"
                  style={{ width: `${(timer.remainingMs / timer.durationMs) * 100}%` }}
                />
              </div>
              <p className="text-xs font-mono text-purple-400">{formatTime(timer.remainingMs)}</p>
            </div>
          ))}
        </div>
      )}

      <div className="flex gap-2">
        <input
          type="number"
          value={newDuration}
          onChange={(e) => setNewDuration(Math.max(1, Number(e.target.value)))}
          className="glass-input w-20 px-3 py-2 text-sm"
          placeholder="Sec"
          min={1}
        />
        <input
          type="text"
          value={newTitle}
          onChange={(e) => setNewTitle(e.target.value)}
          className="glass-input flex-1 px-3 py-2 text-sm"
          placeholder="Timer name..."
        />
        <button
          onClick={addTimer}
          className="glass-button px-4 py-2 text-sm text-purple-400 hover:text-purple-300 transition-colors"
        >
          <Plus size={16} />
        </button>
      </div>

      {timers.length > 0 && (
        <div className="mt-3">
          <p className="text-[10px] font-black uppercase tracking-widest text-slate-500 mb-1">Total Progress</p>
          <div className="w-full h-2 bg-slate-800 rounded-full overflow-hidden">
            <div
              className="h-full bg-gradient-to-r from-purple-500 to-pink-400 rounded-full transition-all duration-1000"
              style={{ width: `${totalProgress}%` }}
            />
          </div>
          <p className="text-xs text-purple-400 mt-1">{Math.round(totalProgress)}% complete</p>
        </div>
      )}
    </div>
  );
};

export default AmbientTimerWidget;
