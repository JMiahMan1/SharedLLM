import { useState, useCallback, useMemo } from 'react';
import { useWidgetStore } from '../../stores/widgetStore';
import { api } from '../../services/api';
import type { DeviceEntry, DeviceSortMode } from '../../types/widget';

const DEVICE_ICONS: Record<string, string> = {
  light: '💡',
  switch: '🔌',
  media_player: '📺',
  cover: '🪟',
  climate: '🌡️',
  lock: '🔒',
  fan: '🌀',
  sensor: '📊',
  vacuum: '🤖',
  camera: '📷',
  script: '⚡',
  automation: '🔄',
};

const DOMAIN_LABELS: Record<string, string> = {
  light: 'Lights',
  switch: 'Switches',
  media_player: 'Media',
  cover: 'Covers',
  climate: 'Climate',
  lock: 'Locks',
  fan: 'Fans',
  sensor: 'Sensors',
  vacuum: 'Vacuums',
  camera: 'Cameras',
};

const ACTIVE_STATES = new Set(['on', 'playing', 'open', 'home', 'cooling', 'heating', 'unlocked', 'cleaning']);

function isActive(state: string): boolean {
  return ACTIVE_STATES.has(state.toLowerCase());
}

function getDeviceIcon(domain: string): string {
  return DEVICE_ICONS[domain] || '📱';
}

function getDomainLabel(domain: string): string {
  return DOMAIN_LABELS[domain] || domain.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

const SortModeButtons: Record<DeviceSortMode, { label: string; icon: string }> = {
  most_used: { label: 'Used', icon: '📊' },
  by_time: { label: 'Time', icon: '🕐' },
  favorites: { label: 'Favorites', icon: '⭐' },
  off: { label: 'Off', icon: '⏻' },
};

type DeviceWithFavorite = DeviceEntry & { isFavorite?: boolean };

const DeviceControlWidget = () => {
  const [devices, setDevices] = useState<DeviceWithFavorite[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
 
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set(['light', 'switch', 'media_player']));
  const [favorites, setFavorites] = useState<Set<string>>(new Set());

  const sort_mode = useWidgetStore((s) => s.userWidgets['device_control']?.sort_mode);

  const loadDevices = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const state = await api.getDeviceStates(['light', 'switch', 'media_player', 'cover', 'climate', 'lock', 'fan']);
      const mapped: DeviceWithFavorite[] = state.map((d) => ({
        ...d,
        isFavorite: favorites.has(d.entity_id),
      }));
      setDevices(mapped);
    } catch {
      setError('Failed to load devices');
    } finally {
      setLoading(false);
    }
  }, [favorites]);

  const toggleDevice = useCallback(
    async (entityId: string, currentState: string) => {
      const newState = isActive(currentState) ? 'off' : 'on';
      const targetState = newState === 'on' ? 'off' : 'on';

      setDevices((prev) =>
        prev.map((d) => (d.entity_id === entityId ? { ...d, state: targetState } : d))
      );

      try {
        await api.toggleDevice(entityId, newState);
        setDevices((prev) =>
          prev.map((d) => (d.entity_id === entityId ? { ...d, state: newState } : d))
        );
      } catch {
        setDevices((prev) =>
          prev.map((d) => (d.entity_id === entityId ? { ...d, state: currentState } : d))
        );
      }
    },
    []
  );

  const toggleFavorite = useCallback(
    (entityId: string) => {
      setFavorites((prev) => {
        const next = new Set(prev);
        if (next.has(entityId)) {
          next.delete(entityId);
        } else {
          next.add(entityId);
        }
        return next;
      });
      setDevices((prev) =>
        prev.map((d) => (d.entity_id === entityId ? { ...d, isFavorite: !favorites.has(entityId) } : d))
      );
    },
    [favorites]
  );

  const groupedDevices = useMemo(() => {
    const list = [...devices];

    if (sort_mode === 'favorites') {
      list.sort((a, b) => (b.isFavorite ? 1 : 0) - (a.isFavorite ? 1 : 0));
    } else if (sort_mode === 'off') {
      list.sort((a, b) => (isActive(a.state) ? 1 : 0) - (isActive(b.state) ? 1 : 0));
    } else if (sort_mode === 'by_time') {
      list.sort((a, b) => (b.last_activated || 0) - (a.last_activated || 0));
    }

    const groups: Map<string, DeviceWithFavorite[]> = new Map();
    for (const device of list) {
      const domain = device.domain || 'unknown';
      const existing = groups.get(domain) || [];
      existing.push(device);
      groups.set(domain, existing);
    }

    const result: { domain: string; label: string; devices: DeviceWithFavorite[] }[] = [];
    for (const [domain, devs] of groups) {
      result.push({ domain, label: getDomainLabel(domain), devices: devs });
    }
    return result.sort((a, b) => a.label.localeCompare(b.label));
  }, [devices, sort_mode]);

  const toggleGroup = useCallback((domain: string) => {
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(domain)) {
        next.delete(domain);
      } else {
        next.add(domain);
      }
      return next;
    });
  }, []);

  const activeCount = devices.filter((d) => isActive(d.state)).length;

  return (
    <div className="glass-card h-full p-5 relative flex flex-col">
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-bold text-white text-lg">Device Control</h3>
        <div className="flex items-center gap-2">
          <span className="text-xs text-slate-400">
            {activeCount}/{devices.length} active
          </span>
          <button
            onClick={loadDevices}
            disabled={loading}
            className="text-slate-500 hover:text-white disabled:opacity-50 transition-colors"
            title="Refresh"
          >
            ↻
          </button>
        </div>
      </div>

      <div className="flex gap-1 mb-3">
        {(Object.keys(SortModeButtons) as DeviceSortMode[]).map((mode) => {
          const btn = SortModeButtons[mode];
          const active = sort_mode === mode;
          return (
            <button
              key={mode}
              onClick={() => {
                useWidgetStore.getState().setSortingMode('device_control', mode);
              }}
              className={`text-xs px-2 py-1 rounded-md transition-colors ${
                active
                  ? 'bg-indigo-600 text-white'
                  : 'bg-slate-800/50 text-slate-400 hover:text-white hover:bg-slate-700/50'
              }`}
            >
              {btn.icon} {btn.label}
            </button>
          );
        })}
      </div>

      {error && (
        <div className="bg-red-900/30 border border-red-800/50 rounded-lg p-3 mb-3">
          <p className="text-xs text-red-400">{error}</p>
        </div>
      )}

      {loading ? (
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center">
            <div className="text-2xl mb-2 animate-pulse">⏳</div>
            <p className="text-xs text-slate-500">Loading devices...</p>
          </div>
        </div>
      ) : groupedDevices.length === 0 ? (
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center">
            <p className="text-sm text-slate-400 mb-2">No devices found</p>
            <button
              onClick={loadDevices}
              className="text-xs text-indigo-400 hover:text-indigo-300"
            >
              Try again
            </button>
          </div>
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto space-y-2 pr-1">
          {groupedDevices.map((group) => (
            <div key={group.domain} className="border border-slate-800/50 rounded-lg overflow-hidden">
              <button
                onClick={() => toggleGroup(group.domain)}
                className="w-full flex items-center justify-between px-3 py-2 bg-slate-800/30 hover:bg-slate-800/50 transition-colors"
              >
                <span className="text-xs font-medium text-slate-300">
                  {group.label} ({group.devices.length})
                </span>
                <span className="text-xs text-slate-500">{expandedGroups.has(group.domain) ? '▾' : '▸'}</span>
              </button>

              {expandedGroups.has(group.domain) && (
                <div className="p-2 space-y-1">
                  {group.devices.map((device) => (
                    <div
                      key={device.entity_id}
                      className="flex items-center justify-between px-2 py-1.5 rounded-md hover:bg-slate-800/30 transition-colors"
                    >
                      <div className="flex items-center gap-2 min-w-0 flex-1">
                        <span className="text-sm flex-shrink-0">{getDeviceIcon(device.domain)}</span>
                        <div className="min-w-0">
                          <p className="text-xs text-white truncate">{device.friendly_name || device.entity_id}</p>
                          <p className={`text-xs ${isActive(device.state) ? 'text-green-400' : 'text-slate-500'}`}>
                            {device.state}
                          </p>
                        </div>
                      </div>

                      <div className="flex items-center gap-1 flex-shrink-0">
                        <button
                          onClick={() => toggleFavorite(device.entity_id)}
                          className="text-xs hover:scale-110 transition-transform"
                        >
                          {device.isFavorite ? '⭐' : '☆'}
                        </button>
                        <button
                          onClick={() => toggleDevice(device.entity_id, device.state)}
                          className={`text-xs px-2 py-0.5 rounded-md transition-colors font-medium ${
                            isActive(device.state)
                              ? 'bg-red-600/20 text-red-400 hover:bg-red-600/30'
                              : 'bg-green-600/20 text-green-400 hover:bg-green-600/30'
                          }`}
                        >
                          {isActive(device.state) ? 'Off' : 'On'}
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default DeviceControlWidget;
