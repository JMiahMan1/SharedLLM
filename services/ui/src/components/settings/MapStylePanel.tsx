import { useEffect, useState } from 'react';
import { Map as MapIcon } from 'lucide-react';
import { getMapStyle, setMapStyle, subscribeMapStyle, type MapStylePref } from '../../lib/mapStyle';

const CHOICES: { value: MapStylePref; label: string; hint: string }[] = [
  { value: 'auto', label: 'Auto', hint: 'Dark from sunset to sunrise' },
  { value: 'light', label: 'Light', hint: 'Always light' },
  { value: 'dark', label: 'Dark', hint: 'Always dark' },
];

/** Per-device choice of light or dark map tiles (Wander, the member page, widgets). */
export default function MapStylePanel() {
  const [style, setStyle] = useState<MapStylePref>(getMapStyle);
  useEffect(() => subscribeMapStyle(() => setStyle(getMapStyle())), []);

  return (
    <section className="glass-panel rounded-2xl p-4 space-y-3" aria-labelledby="map-style-heading" data-testid="map-style-panel">
      <div className="px-1">
        <h2 id="map-style-heading" className="text-sm font-semibold text-slate-400 uppercase tracking-wider flex items-center gap-2">
          <MapIcon size={16} className="text-purple-400" />
          Map style
        </h2>
        <p className="text-xs text-slate-500 mt-1">
          {CHOICES.find((choice) => choice.value === style)?.hint}. Applies to this device only.
        </p>
      </div>
      <div role="radiogroup" aria-labelledby="map-style-heading" className="grid grid-cols-3 gap-2">
        {CHOICES.map((choice) => (
          <button
            key={choice.value}
            type="button"
            role="radio"
            aria-checked={style === choice.value}
            onClick={() => setMapStyle(choice.value)}
            className={`min-h-11 rounded-xl px-3 text-sm font-medium transition-colors ${
              style === choice.value
                ? 'bg-[var(--site-accent-soft)] text-[var(--site-accent-text)] border border-[var(--site-accent-border)]'
                : 'bg-white/5 text-slate-300 hover:bg-white/10'
            }`}
          >
            {choice.label}
          </button>
        ))}
      </div>
    </section>
  );
}
