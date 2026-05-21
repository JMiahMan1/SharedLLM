import { useState } from 'react';
import { useBackgroundLocation } from '../../hooks/useBackgroundLocation';
import { useHaptics } from '../../hooks/useHaptics';
import { MapPin, Satellite, Battery, Zap } from 'lucide-react';

const LocationPanel = () => {
  const { trigger } = useHaptics();
  const { latitude, longitude, accuracy, speed, isTracking, error, interval, startTracking, stopTracking } = useBackgroundLocation();
  const [showDetails, setShowDetails] = useState(false);

  const handleToggle = () => {
    trigger('light');
    if (isTracking) {
      stopTracking();
    } else {
      startTracking();
    }
  };

  const speedMph = speed ? (speed * 2.237).toFixed(1) : '0';
  const isTransit = interval === 'transit';

  return (
    <div className="glass-panel rounded-2xl p-4 space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Satellite size={18} className={isTracking ? 'text-green-400' : 'text-slate-500'} />
          <div>
            <p className="text-white text-sm font-medium">Location Tracking</p>
            <p className="text-xs text-slate-400">
              {isTracking
                ? isTransit
                  ? `Active road tracking. Sending telemetry every 30 seconds`
                  : `Low power sleep mode active. Geofence reporting locked`
                : 'Location paused'}
            </p>
          </div>
        </div>

        <button
          onClick={handleToggle}
          className={`w-12 h-7 rounded-full relative transition-colors ${
            isTracking ? 'bg-green-500' : 'bg-slate-600'
          }`}
        >
          <div
            className={`absolute top-1 w-5 h-5 rounded-full bg-white shadow transition-transform ${
              isTracking ? 'translate-x-6' : 'translate-x-1'
            }`}
          />
        </button>
      </div>

      {isTracking && (
        <>
          <div className="flex items-center gap-4 text-xs text-slate-400">
            <div className="flex items-center gap-1">
              <MapPin size={12} />
              <span>{latitude?.toFixed(4)}, {longitude?.toFixed(4)}</span>
            </div>
            {accuracy && <span>±{accuracy.toFixed(0)}m</span>}
          </div>

          <div className="flex items-center gap-3">
            <div className={`p-2 rounded-lg ${isTransit ? 'bg-cyan-500/20' : 'bg-green-500/20'}`}>
              {isTransit ? <Zap size={16} className="text-cyan-400" /> : <Battery size={16} className="text-green-400" />}
            </div>
            <div className="flex-1">
              <p className="text-xs text-white font-medium">
                {isTransit ? 'In Transit' : 'Stationary'}
              </p>
              <p className="text-[10px] text-slate-400">
                Speed: {speedMph} mph · Sync: {isTransit ? '30s' : '15min'}
              </p>
            </div>
          </div>

          <button
            onClick={() => { trigger('light'); setShowDetails(!showDetails); }}
            className="w-full text-xs text-purple-400 hover:text-purple-300 transition-colors"
          >
            {showDetails ? 'Hide details' : 'Show details'}
          </button>

          {showDetails && (
            <div className="glass-card p-3 space-y-2 text-xs">
              <div className="flex justify-between">
                <span className="text-slate-400">Latitude</span>
                <span className="text-white font-mono">{latitude?.toFixed(6)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-400">Longitude</span>
                <span className="text-white font-mono">{longitude?.toFixed(6)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-400">Accuracy</span>
                <span className="text-white">{accuracy?.toFixed(0)}m</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-400">Speed</span>
                <span className="text-white">{speedMph} mph</span>
              </div>
            </div>
          )}
        </>
      )}

      {error && <p className="text-xs text-red-400">{error}</p>}
    </div>
  );
};

export default LocationPanel;
