import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Play, Pause, SkipForward, SkipBack, Volume2, Cast, Search } from 'lucide-react';
import { api } from '../services/api';
import { useHaptics } from '../hooks/useHaptics';

const Media = () => {
  const { trigger } = useHaptics();
  const [playing, setPlaying] = useState(false);
  const [volume, setVolume] = useState(70);
  const [selectedTarget, setSelectedTarget] = useState<string>('');
  const [searchQuery, setSearchQuery] = useState('');

  const { data: entities = [] } = useQuery({
    queryKey: ['media-entities'],
    queryFn: () => api.getEntities(),
    select: (data) => data.filter((e) => e.domain === 'media_player'),
  });

  const mediaTargets = entities.map((entity) => ({
    id: entity.entity_id,
    name: entity.friendly_name || entity.entity_id,
    room: entity.entity_id.split('.')[1]?.replace(/_/g, ' ') || 'Unknown',
    type: entity.entity_id.includes('tv') ? 'tv' : 'speaker',
    online: entity.state !== 'unavailable' && entity.state !== 'unknown',
  }));

  const handleTransport = (action: string) => {
    trigger('light');
    if (action === 'play') setPlaying((p) => !p);
  };

  const filteredTargets = mediaTargets.filter(
    (t) =>
      t.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      t.room.toLowerCase().includes(searchQuery.toLowerCase())
  );

  return (
    <div className="space-y-6 max-w-4xl mx-auto">
      <h1 className="text-2xl font-bold text-white">Media</h1>

      <div className="glass-panel rounded-2xl p-4 md:p-6 border border-cyan-500/20">
        <div className="flex flex-col sm:flex-row items-start sm:items-center gap-4">
          <div className="w-20 h-20 sm:w-24 sm:h-24 rounded-xl bg-gradient-to-br from-cyan-500/30 to-purple-500/30 flex items-center justify-center shrink-0">
            <Play size={32} className="text-cyan-400" />
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-white font-medium text-lg">No Active Playback</p>
            <p className="text-sm text-slate-400">Select a target and content to begin</p>
          </div>
        </div>

        <div className="mt-6">
          <div className="w-full h-1 bg-white/10 rounded-full overflow-hidden">
            <div className="h-full bg-cyan-400 rounded-full transition-all" style={{ width: '35%' }} />
          </div>
          <div className="flex items-center justify-between mt-2">
            <span className="text-xs text-slate-500">1:23</span>
            <span className="text-xs text-slate-500">3:45</span>
          </div>
        </div>

        <div className="flex items-center justify-center gap-6 mt-4">
          <button onClick={() => handleTransport('prev')} className="text-slate-400 hover:text-white transition-colors p-2">
            <SkipBack size={22} />
          </button>
          <button
            onClick={() => handleTransport('play')}
            className="w-12 h-12 rounded-full bg-cyan-500/20 border border-cyan-500/30 flex items-center justify-center text-cyan-400 hover:bg-cyan-500/30 transition-colors"
          >
            {playing ? <Pause size={22} /> : <Play size={22} />}
          </button>
          <button onClick={() => handleTransport('next')} className="text-slate-400 hover:text-white transition-colors p-2">
            <SkipForward size={22} />
          </button>
        </div>

        <div className="flex items-center gap-3 mt-4">
          <Volume2 size={16} className="text-slate-400 shrink-0" />
          <input
            type="range"
            min="0"
            max="100"
            value={volume}
            onChange={(e) => setVolume(Number(e.target.value))}
            className="flex-1 accent-cyan-400"
          />
          <span className="text-xs text-slate-400 w-8 text-right">{volume}%</span>
        </div>
      </div>

      <div className="glass-panel rounded-2xl p-4 md:p-6">
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">Cast To</h2>

        <div className="relative mb-4">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <input
            type="text"
            placeholder="Search devices..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full glass-input pl-9 h-10 text-sm rounded-xl"
          />
        </div>

        <div className="space-y-2">
          {filteredTargets.map((target) => (
            <button
              key={target.id}
              onClick={() => { trigger('light'); setSelectedTarget(target.id); }}
              className={`w-full flex items-center justify-between p-3 rounded-xl transition-colors ${
                selectedTarget === target.id
                  ? 'bg-cyan-500/20 border border-cyan-500/30'
                  : 'bg-white/5 hover:bg-white/10'
              } ${!target.online ? 'opacity-50' : ''}`}
            >
              <div className="flex items-center gap-3">
                <Cast size={18} className="text-slate-400 shrink-0" />
                <div className="text-left min-w-0">
                  <p className="text-white text-sm font-medium truncate">{target.name}</p>
                  <p className="text-xs text-slate-400">{target.room}</p>
                </div>
              </div>
              <div className={`w-2 h-2 rounded-full shrink-0 ${target.online ? 'bg-green-400' : 'bg-slate-600'}`} />
            </button>
          ))}
          {filteredTargets.length === 0 && mediaTargets.length > 0 && (
            <p className="text-sm text-slate-500 text-center py-4">No devices match "{searchQuery}"</p>
          )}
          {mediaTargets.length === 0 && (
            <p className="text-sm text-slate-500 text-center py-4">No media players found. Check Home Assistant connection.</p>
          )}
        </div>
      </div>
    </div>
  );
};

export default Media;
