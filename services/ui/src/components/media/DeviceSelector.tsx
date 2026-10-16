import { useEffect, useRef } from 'react';
import { Cast, Headphones } from 'lucide-react';
import type { DeviceOption } from './types';

const chipClass = (active: boolean, online: boolean) =>
  `flex items-center gap-2.5 px-3.5 py-2.5 min-h-11 rounded-xl border shrink-0 transition-all text-left min-w-[150px] max-w-[220px] relative overflow-hidden ${
    active
      ? 'bg-cyan-500/15 border-cyan-500/40 shadow-lg shadow-cyan-500/5'
      : online
        ? 'bg-white/5 border-white/10 hover:bg-white/10 hover:border-white/20'
        : 'bg-white/3 border-white/5 opacity-50'
  }`;

/**
 * Where playback goes: this device's web player first, then every speaker/TV.
 * A horizontal strip on phones; it wraps into rows on wider screens so nothing
 * is hidden behind a sideways scroll on a desktop.
 */
export function DeviceSelector({
  selectedTarget,
  devices,
  onSelect,
  webPlayerPlaying,
}: {
  selectedTarget: string;
  devices: DeviceOption[];
  onSelect: (id: string) => void;
  webPlayerPlaying: boolean;
}) {
  const online = devices.filter((d) => d.online).length;
  // On a phone the strip scrolls sideways: keep the chosen device in view
  // (e.g. a speaker the page adopted because it was already playing).
  const stripRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const chip = stripRef.current?.querySelector<HTMLElement>('[aria-pressed="true"]');
    chip?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' });
  }, [selectedTarget, devices.length]);
  return (
    <div className="glass-panel rounded-2xl p-4">
      <div className="flex items-center justify-between mb-3 gap-2">
        <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-wider flex items-center gap-1.5">
          <Cast size={12} />Play on
        </h2>
        <span className="text-[11px] text-slate-500">{online} speaker{online === 1 ? '' : 's'} online</span>
      </div>
      <div ref={stripRef} className="flex gap-2 overflow-x-auto sm:flex-wrap sm:overflow-visible custom-scrollbar pb-1">
        <button type="button" onClick={() => onSelect('')} aria-pressed={!selectedTarget} className={chipClass(!selectedTarget, true)}>
          <Headphones size={16} className="text-cyan-400 shrink-0" />
          <span className="min-w-0 flex-1">
            <span className="block text-white text-sm font-medium truncate">This device</span>
            <span className="block text-[11px] text-slate-500 truncate">
              {webPlayerPlaying ? 'Playing now' : 'Web player'}
            </span>
          </span>
          {webPlayerPlaying && <span className="w-2 h-2 rounded-full bg-cyan-400 shrink-0 animate-pulse" aria-hidden />}
        </button>
        {devices.map((d) => (
          <button
            type="button"
            key={d.id}
            onClick={() => onSelect(d.id)}
            aria-pressed={selectedTarget === d.id}
            className={chipClass(selectedTarget === d.id, d.online)}
          >
            <span className={`w-2.5 h-2.5 rounded-full shrink-0 ${d.online ? 'bg-green-400' : 'bg-slate-600'}`} aria-hidden />
            <span className="min-w-0 flex-1">
              <span className="block text-white text-sm font-medium truncate">{d.name}</span>
              <span className="block text-[11px] text-slate-500 truncate">{d.online ? d.detail : 'Offline'}</span>
            </span>
          </button>
        ))}
        {devices.length === 0 && (
          <p className="flex items-center px-3 text-slate-500 text-xs">
            No speakers found. Home Assistant players appear here once they are set up.
          </p>
        )}
      </div>
    </div>
  );
}
