import { useState, useCallback, useMemo, useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { useAuth } from '../../context/AuthContext';
import { useWidgetStore } from '../../stores/widgetStore';
import { climateAttrs, modesFor, numberAttr, titleize } from '../../lib/climate';
import { api, apiClient } from '../../services/api';
import type { DeviceEntry, IWidgetProps } from '../../types/widget';
import { WidgetCard } from './WidgetCard';
import { 
  Star, 
  Power,       
  X, 
  Shield, 
  RefreshCw, 
  Layers,
  Thermometer,
  ArrowUp,
  ArrowDown,
  Sliders,
  LayoutGrid
} from 'lucide-react';
import toast from 'react-hot-toast';
import TokenBridge from '../../plugins/tokenBridge';
import { confirmDialog } from '../ui/confirmDialog';
import { errorText } from '../../lib/errorText';
import DeviceControl from '../devices/controls/registry';
import type { HubEntity } from '../../lib/dashboards';

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

// Stable empty reference: returning a fresh [] from the zustand selector
// makes React's useSyncExternalStore see a new snapshot every call while
// widget settings haven't loaded, causing "Maximum update depth exceeded".
const EMPTY_PINNED_DEVICES: string[] = [];

function isActive(state: string): boolean {
  return ACTIVE_STATES.has(state.toLowerCase());
}

function getToggleAction(domain: string, state: string): { service: string; label: string } | null {
  const active = isActive(state);

  switch (domain) {
    case 'light':
    case 'switch':
    case 'media_player':
    case 'fan':
      return { service: active ? 'turn_off' : 'turn_on', label: active ? 'Off' : 'On' };
    case 'cover':
      return { service: active ? 'close_cover' : 'open_cover', label: active ? 'Close' : 'Open' };
    case 'lock':
      return { service: active ? 'lock' : 'unlock', label: active ? 'Lock' : 'Unlock' };
    default:
      return null;
  }
}

function getDeviceIcon(domain: string): string {
  return DEVICE_ICONS[domain] || '📱';
}

function getDomainLabel(domain: string): string {
  return DOMAIN_LABELS[domain] || domain.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

/**
 * The device's Home Assistant area, or null when it has none. No guessing from
 * the name: a "Living Room" fallback put every unplaced device in one room.
 */
const deviceRoom = (device: DeviceEntry): string | null =>
  device.room && device.room !== 'unknown' ? titleize(device.room) : null;

const UNPLACED = 'No room';

const DeviceControlWidget = ({ settingsButton }: IWidgetProps) => {
  const { user, can } = useAuth();
  const canAssign = can('devices.assign');
  const togglePinnedDevice = useWidgetStore((s) => s.togglePinnedDevice);
  const pinnedDevices = useWidgetStore(
    (s) => s.userWidgets['device_control']?.pinned_devices ?? EMPTY_PINNED_DEVICES
  );

  const [devices, setDevices] = useState<DeviceEntry[]>([]);
  const [activityCutoff, setActivityCutoff] = useState<number>(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Tabs
  const [activeTab, setActiveTab] = useState<'favorites' | 'all' | 'room'>('favorites');

  // Assignments & Users
  const [assignments, setAssignments] = useState<{ id: number; device_id: string; username: string }[]>([]);
  const [allUsers, setAllUsers] = useState<{ username: string; display_name?: string }[]>([]);
  const [pendingEntity, setPendingEntity] = useState<string | null>(null);
  // The assignment read is separate: a failure used to look like "nobody can use this".
  const [assignmentsError, setAssignmentsError] = useState(false);

  // Detailed modal
  const [selectedDevice, setSelectedDevice] = useState<DeviceEntry | null>(null);
  // A thermostat command can round-trip through `loadDevices` before Home
  // Assistant publishes the new attribute. Hold the user's change briefly so
  // the panel does not snap back to the old mode/setpoint.
  const climatePatchRef = useRef<{
    entityId: string;
    attributes: Record<string, number | string>;
    until: number;
  } | null>(null);
  const [updatingAssignment, setUpdatingAssignment] = useState(false);


  // Escape closes the device dialog, matching every other sheet in the app.
  // The visible close button is sticky too: on a long thermostat panel it must
  // not scroll out of reach.
  useEffect(() => {
    if (!selectedDevice) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setSelectedDevice(null);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [selectedDevice]);

  const loadDevices = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // Load device states (isolated): failure here is shown as error
      const state = await api.getDeviceStates(['light', 'switch', 'media_player', 'cover', 'climate', 'lock', 'fan']);
      setDevices(state);
      // Keep an open detail dialog in step with the state that was just read.
      // Without this the modal kept the attributes it opened with, so a mode
      // change looked ignored until the dialog was closed and reopened.
      setSelectedDevice((current) => {
        if (!current) return current;
        const fresh = state.find((device) => device.entity_id === current.entity_id);
        if (!fresh) return current;
        const patch = climatePatchRef.current;
        if (patch && patch.entityId === fresh.entity_id && Date.now() < patch.until) {
          return { ...fresh, attributes: { ...fresh.attributes, ...patch.attributes } };
        }
        if (patch && patch.entityId === fresh.entity_id) climatePatchRef.current = null;
        return fresh;
      });
      setActivityCutoff(Date.now() - 30 * 24 * 60 * 60 * 1000);
    } catch (err) {
      console.error('Failed to load device states:', err);
      setError('Failed to load devices. Check that the home hub is reachable.');
    }

    // Load user device assignments (isolated): failure here only logs
    try {
      const userDevices = await api.getDevices();
      setAssignments(userDevices);
      setAssignmentsError(false);
    } catch (err) {
      console.error('Failed to load device assignments:', err);
      // Keep whatever assignments we already had, but say the read failed: the
      // "who can use it" boxes otherwise render an authoritative empty set.
      setAssignmentsError(true);
    }

    // Who devices can be shared with, for the assignment panel (isolated).
    // Not getUsers(): that is admin-only, and home managers assign too.
    if (canAssign) {
      try {
        setAllUsers(await api.getSharingRecipients());
      } catch (err) {
        console.error('Failed to load users:', err);
      }
    }

    setLoading(false);
  }, [canAssign]);

  useEffect(() => {
    let active = true;
    Promise.resolve().then(() => {
      if (active) {
        loadDevices();
      }
    });
    return () => {
      active = false;
    };
  }, [loadDevices]);

  // Can the current user control this device?
  //
  // `devices` comes from `/execute/entity/search`, which already drops every
  // entity this user may neither see nor control, honouring both the device
  // assignment and any admin-set entity protection. Presence in that list is
  // therefore the permission answer. This used to be re-derived from the
  // assignment list, which was wrong twice over: it hid entities an admin had
  // explicitly permitted through a protection grant (no assignment row), and
  // it re-implemented a security rule in the browser, where a stale copy
  // silently disagrees with the backend. The server is the single authority.
  const hasControlPermission = useCallback((entityId: string) => {
    return devices.some((d) => d.entity_id === entityId);
  }, [devices]);

  // Away = the server's presence for this user is somewhere other than home
  // (GPS against Home Assistant's home zone). Unknown counts as home, so a
  // missing fix never blocks the door; it only skips the extra question.
  const username = user?.username;
  const isAwayFromHome = useCallback(async (): Promise<boolean> => {
    if (!username) return false;
    try {
      const resp = await api.getUserPresence(username);
      const room = resp.presence?.room?.toLowerCase();
      return Boolean(room) && room !== 'home';
    } catch {
      return false;
    }
  }, [username]);

  const callHAService = useCallback(async (domain: string, service: string, entityId: string, serviceData: unknown = null) => {
    if (!hasControlPermission(entityId)) {
      toast.error('Access Denied: this device is no longer available to your account.');
      return;
    }

    const wantsGarageOpen =
      entityId.toLowerCase().includes('garage') &&
      (service === 'turn_on' || service === 'open_cover' || service === 'unlock');
    if (wantsGarageOpen) {
      const away = await isAwayFromHome();
      if (away) {
        const ok = await confirmDialog({ title: 'Open the garage?', body: 'You appear to be away from home. Open the garage door anyway?', confirmLabel: 'Open', danger: true });
        if (!ok) return;
      }
    }

    // Opening a lock or a cover was a single unconfirmed tap, and the garage
    // case above only asked when presence said away.
    const opensSomething =
      service === 'unlock' || service === 'open_cover' || service === 'turn_on' && /(lock|garage|cover|gate)/.test(entityId.toLowerCase());
    if (opensSomething && !wantsGarageOpen) {
      const ok = await confirmDialog({
        title: 'Open it?',
        body: `Send ${service.replace('_', ' ')} to ${entityId}?`,
        confirmLabel: 'Open',
        danger: true,
      });
      if (!ok) return;
    }

    setPendingEntity(entityId);
    try {
      const resp = await apiClient.post('/execute/ha_service', {
        domain,
        service,
        entity_id: entityId,
        service_data: serviceData ?? null,
      });
      // A refused command arrives as a FAILURE body with HTTP 200, so this used
      // to toast "Command sent" for a device the person may not touch.
      if (resp.data?.status === 'FAILURE') {
        throw new Error(resp.data.message || 'The home hub refused that');
      }
      toast.success('Command sent');
      // Refresh device states in background after HA executes
      loadDevices();
    } catch (err) {
      console.error('HA Service Error:', err);
      toast.error(errorText(err, 'Could not send that command. Check the home hub.'));
    } finally {
      setPendingEntity(null);
    }
  }, [hasControlPermission, loadDevices, isAwayFromHome]);

  const toggleDevice = useCallback(
    async (entityId: string, currentState: string) => {
      if (!hasControlPermission(entityId)) {
        toast.error('Access Denied: this device is no longer available to your account.');
        return;
      }

      const domain = entityId.split('.')[0];
      const action = getToggleAction(domain, currentState);
      if (!action) {
        toast.error(`No generic power toggle is available for ${getDomainLabel(domain)} devices.`);
        return;
      }

      await callHAService(domain, action.service, entityId);
    },
    [hasControlPermission, callHAService]
  );

  // Persists device favorites in store + DB
  const toggleFavorite = useCallback(
    async (entityId: string) => {
      try {
        const currentPinned = useWidgetStore.getState().userWidgets['device_control']?.pinned_devices || [];
        await togglePinnedDevice('device_control', entityId);
        const nowPinned = !currentPinned.includes(entityId);
        toast.success(
          nowPinned
            ? 'Pinned: shows on home-screen Device Buttons widget'
            : 'Removed from favorites / home widget'
        );
        try {
          await TokenBridge.refreshWidgets();
        } catch {
          // web
        }
      } catch {
        toast.error('Failed to update favorite status');
      }
    },
    [togglePinnedDevice]
  );

  // Open modal & load detailed settings
  const handleOpenDetail = (device: DeviceEntry) => {
    setSelectedDevice(device);
  };

  const selectedToggleAction = selectedDevice
    ? getToggleAction(selectedDevice.domain, selectedDevice.state)
    : null;

  // The open device in the shape the control library takes; kept stable so a drawn change survives renders.
  const selectedHubEntity = useMemo<HubEntity | null>(() => (selectedDevice ? {
    entity_id: selectedDevice.entity_id, domain: selectedDevice.domain, name: selectedDevice.friendly_name || selectedDevice.entity_id,
    state: selectedDevice.state, attributes: { ...(selectedDevice.attributes ?? {}) },
    device_class: typeof selectedDevice.attributes?.device_class === 'string' ? selectedDevice.attributes.device_class : null,
    unit: null, icon: null, area_id: null, device_id: null, last_changed: '',
  } : null), [selectedDevice]);
  const selectedClimate = selectedDevice?.domain === 'climate' ? climateAttrs(selectedDevice) : null;
  const targetTemperature = numberAttr(selectedClimate?.temperature);
  const lowSetpoint = numberAttr(selectedClimate?.target_temp_low);
  const highSetpoint = numberAttr(selectedClimate?.target_temp_high);
  const heatCool = selectedClimate?.hvac_mode === 'heat_cool'
    && typeof lowSetpoint === 'number' && typeof highSetpoint === 'number';
  const climateMode = selectedClimate?.hvac_mode ?? selectedDevice?.state?.toLowerCase() ?? '';
  const climateStep = numberAttr(selectedClimate?.target_temp_step) ?? 1;

  /** Paint the thermostat optimistically; `loadDevices` merges it until HA echoes. */
  const patchSelectedClimate = useCallback((attributes: Record<string, number | string>) => {
    if (!selectedDevice || selectedDevice.domain !== 'climate') return;
    const { entity_id: entityId } = selectedDevice;
    climatePatchRef.current = {
      entityId,
      attributes: { ...climatePatchRef.current?.attributes, ...attributes },
      until: Date.now() + 6000,
    };
    setSelectedDevice((current) => (
      current && current.entity_id === entityId
        ? { ...current, attributes: { ...current.attributes, ...attributes } }
        : current
    ));
  }, [selectedDevice]);

  // Who may use the selected device. A device can be shared: ticking a member
  // adds them and unticking removes only them.
  const assignedUsers = useMemo(
    () => new Set(assignments.filter((a) => a.device_id === selectedDevice?.entity_id).map((a) => a.username)),
    [assignments, selectedDevice],
  );

  const handleAssign = async (username: string, assign: boolean) => {
    if (!selectedDevice) return;
    setUpdatingAssignment(true);
    const targetEntity = selectedDevice.entity_id;
    try {
      if (assign) {
        await api.updateDeviceAssignment({ username, device_id: targetEntity });
        toast.success(`${username} can use it`);
      } else {
        await api.deleteDeviceAssignment(targetEntity, username);
        toast.success(`${username} can no longer use it`);
      }
      setAssignments(await api.getDevices());
    } catch (e) {
      console.error(e);
      toast.error('Failed to update the assignment');
    } finally {
      setUpdatingAssignment(false);
    }
  };

  // Filtered lists
  const favoriteDevicesList = useMemo(() => {
    return devices.filter((d) => pinnedDevices.includes(d.entity_id));
  }, [devices, pinnedDevices]);

  // Active in the last 30 days: recently updated/changed, currently active,
  // or pinned as a favorite (so favorites never disappear when idle).
  // cutoff is captured when devices load so this memo stays pure.
  const activeDevices = useMemo(() => {
    if (!activityCutoff) return devices;
    return devices.filter((d) => {
      if (pinnedDevices.includes(d.entity_id)) return true;
      if (isActive(d.state)) return true;
      if (d.last_activated && d.last_activated >= activityCutoff) return true;
      // No timestamp from HA; keep the device rather than hide it
      if (!d.last_activated) return true;
      return false;
    });
  }, [devices, pinnedDevices, activityCutoff]);

  const allDevicesGrouped = useMemo(() => {
    const groups: Record<string, DeviceEntry[]> = {};
    for (const d of activeDevices) {
      const dom = d.domain || 'other';
      if (!groups[dom]) groups[dom] = [];
      groups[dom].push(d);
    }
    return Object.entries(groups).map(([domain, devs]) => ({
      domain,
      label: getDomainLabel(domain),
      devices: devs.sort((a, b) => (a.friendly_name || '').localeCompare(b.friendly_name || '')),
    })).sort((a, b) => a.label.localeCompare(b.label));
  }, [activeDevices]);

  // Home Assistant areas, alphabetical, with unplaced devices last.
  const roomGroups = useMemo(() => {
    const groups: Record<string, DeviceEntry[]> = {};
    for (const d of activeDevices) {
      const room = deviceRoom(d) ?? UNPLACED;
      (groups[room] ??= []).push(d);
    }
    return Object.entries(groups)
      .sort(([a], [b]) => (a === UNPLACED ? 1 : b === UNPLACED ? -1 : a.localeCompare(b)))
      .map(([room, devs]) => ({
        room,
        devices: devs.sort((x, y) => (x.friendly_name || '').localeCompare(y.friendly_name || '')),
      }));
  }, [activeDevices]);
  const hasRooms = roomGroups.some((g) => g.room !== UNPLACED);

  const renderRoomGroups = () =>
    roomGroups.map((group) => (
      <div key={group.room} className="space-y-1.5">
        <p className="text-[9px] font-black uppercase text-slate-500 tracking-wider mt-2 mb-1 border-b border-slate-800/40 pb-0.5">
          {group.room} ({group.devices.length})
        </p>
        {group.devices.map(renderDeviceRow)}
      </div>
    ));

  // Render a standard device row
  const renderDeviceRow = (device: DeviceEntry) => {
    const isFav = pinnedDevices.includes(device.entity_id);
    const isDeviceActive = isActive(device.state);
    const room = deviceRoom(device);

    return (
      <div
        key={device.entity_id}
        onClick={() => handleOpenDetail(device)}
        className="flex items-center justify-between gap-2 p-2.5 rounded-xl bg-slate-900/30 border border-slate-800/40 hover:bg-slate-800/20 hover:border-white/5 transition-all duration-200 cursor-pointer group"
      >
        <div className="flex items-center gap-2 min-w-0 flex-1">
          <span className="text-base p-1.5 bg-slate-950/40 border border-white/5 rounded-lg shrink-0">
            {getDeviceIcon(device.domain)}
          </span>
          <div className="min-w-0">
            <div className="flex items-center gap-1.5">
              <p className="text-xs font-semibold text-white group-hover:text-purple-300 transition-colors truncate">
                {device.friendly_name || device.entity_id}
              </p>
            </div>
            <p className="text-[10px] text-slate-500 flex items-center gap-1 mt-0.5">
              <span>{getDomainLabel(device.domain)}</span>
              {room && (
                <>
                  <span>•</span>
                  <span className="truncate">{room}</span>
                </>
              )}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-1 flex-shrink-0" onClick={(e) => e.stopPropagation()}>
          <button
            onClick={() => toggleFavorite(device.entity_id)}
            className="p-1.5 text-slate-500 hover:text-amber-400 transition-colors pointer-coarse:min-h-11 pointer-coarse:min-w-11 pointer-coarse:flex pointer-coarse:items-center pointer-coarse:justify-center"
            title={isFav ? 'Remove Favorite' : 'Favorite'}
            aria-label={`${isFav ? 'Unfavorite' : 'Favorite'} ${device.friendly_name || device.entity_id}`}
            aria-pressed={isFav}
          >
            <Star size={14} className={isFav ? 'fill-amber-400 text-amber-400' : ''} />
          </button>
          
          {getToggleAction(device.domain, device.state) ? (
            <button
              onClick={() => toggleDevice(device.entity_id, device.state)}
              disabled={pendingEntity === device.entity_id}
              className={`text-xs px-3 py-1 rounded-lg font-semibold transition-all duration-200 pointer-coarse:min-h-11 ${
                isDeviceActive
                  ? 'bg-green-500/10 border border-green-500/30 text-green-400 hover:bg-green-500/20'
                  : 'bg-slate-950/40 border border-slate-800 text-slate-400 hover:text-white hover:border-slate-700'
              } disabled:opacity-30 disabled:cursor-not-allowed`}
            >
              <Power size={12} className="hidden sm:inline mr-1" />
              {getToggleAction(device.domain, device.state)?.label}
            </button>
          ) : (
            <button
              disabled
              className="text-xs px-3 py-1 rounded-lg font-semibold pointer-coarse:min-h-11 bg-slate-900/40 border border-slate-800 text-slate-500 cursor-not-allowed"
            >
              No Toggle
            </button>
          )}
        </div>
      </div>
    );
  };

  return (
    <WidgetCard
      title="Device Controls"
      isLoading={loading}
      error={error}
      onRetry={loadDevices}
      settingsButton={settingsButton}
      isExpandable={true}
      icon="📱"
      actions={
        <div className="flex items-center gap-1.5">
          <button
            onClick={loadDevices}
            disabled={loading}
            className="p-1 hover:bg-white/5 rounded text-slate-400 hover:text-white transition-colors pointer-coarse:min-h-11 pointer-coarse:min-w-11 pointer-coarse:flex pointer-coarse:items-center pointer-coarse:justify-center"
            title="Refresh device states"
            aria-label="Refresh device states"
          >
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      }
      expandedChildren={
        <div className="space-y-6 py-2">
          {/* Dashboard quick telemetry metrics */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="bg-slate-900/30 border border-slate-800/80 p-4 rounded-xl flex items-center gap-4">
              <span className="p-3 bg-purple-500/10 border border-purple-500/20 rounded-xl text-purple-400 text-xl">💡</span>
              <div>
                <p className="text-xs text-slate-500">Active Lights</p>
                <p className="text-lg font-bold text-white">
                  {devices.filter((d) => d.domain === 'light' && isActive(d.state)).length} active
                </p>
              </div>
            </div>
            <div className="bg-slate-900/30 border border-slate-800/80 p-4 rounded-xl flex items-center gap-4">
              <span className="p-3 bg-green-500/10 border border-green-500/20 rounded-xl text-green-400 text-xl">🔌</span>
              <div>
                <p className="text-xs text-slate-500">Switches On</p>
                <p className="text-lg font-bold text-white">
                  {devices.filter((d) => d.domain === 'switch' && isActive(d.state)).length} active
                </p>
              </div>
            </div>
            <div className="bg-slate-900/30 border border-slate-800/80 p-4 rounded-xl flex items-center gap-4">
              <span className="p-3 bg-amber-500/10 border border-amber-500/20 rounded-xl text-amber-400 text-xl">⭐</span>
              <div>
                <p className="text-xs text-slate-500">Starred Favorites</p>
                <p className="text-lg font-bold text-white">{favoriteDevicesList.length} configured</p>
              </div>
            </div>
          </div>

          {/* Full Screen Layout Grid */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {/* Left Col: Favorites List */}
            <div className="glass-card p-4 flex flex-col h-[400px] border border-white/5 bg-slate-900/10">
              <h5 className="text-xs font-black uppercase tracking-widest text-slate-400 mb-3 flex items-center gap-1.5">
                <Star size={12} className="fill-amber-400 text-amber-400" /> Favorites
              </h5>
              <div className="flex-1 overflow-y-auto space-y-2 pr-1">
                {favoriteDevicesList.length === 0 ? (
                  <div className="h-full flex flex-col items-center justify-center text-center p-6">
                    <span className="text-2xl mb-2">⭐</span>
                    <p className="text-xs text-slate-400">No favorite devices configured</p>
                    <p className="text-[10px] text-slate-600 mt-1">Star devices in the 'All Devices' tab for quick access.</p>
                  </div>
                ) : (
                  favoriteDevicesList.map(renderDeviceRow)
                )}
              </div>
            </div>

            {/* Middle Col: devices by Home Assistant area */}
            <div className="glass-card p-4 flex flex-col h-[400px] border border-white/5 bg-slate-900/10">
              <h5 className="text-xs font-black uppercase tracking-widest text-slate-400 mb-3 flex items-center gap-1.5">
                <LayoutGrid size={12} className="text-indigo-400" /> By Room
              </h5>
              <div className="flex-1 overflow-y-auto space-y-2 pr-1">
                {hasRooms ? renderRoomGroups() : (
                  <div className="h-full flex flex-col items-center justify-center text-center p-6">
                    <p className="text-xs text-slate-400">No devices have a room yet</p>
                    <p className="text-[10px] text-slate-600 mt-1">Assign areas to devices in the home hub and they group here.</p>
                  </div>
                )}
              </div>
            </div>

            {/* Right Col: All Devices */}
            <div className="glass-card p-4 flex flex-col h-[400px] border border-white/5 bg-slate-900/10">
              <h5 className="text-xs font-black uppercase tracking-widest text-slate-400 mb-3 flex items-center gap-1.5">
                <Layers size={12} className="text-slate-400" /> All Assigned Devices
              </h5>
              <div className="flex-1 overflow-y-auto space-y-4 pr-1">
                {allDevicesGrouped.map((group) => (
                  <div key={group.domain} className="space-y-2">
                    <p className="text-[10px] font-bold text-slate-500 border-b border-slate-800 pb-1 uppercase tracking-wide">
                      {group.label} ({group.devices.length})
                    </p>
                    <div className="space-y-2">
                      {group.devices.map(renderDeviceRow)}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      }
    >
      {/* Dynamic Tab bar */}
      <div className="flex border-b border-white/5 mb-3 select-none">
        <button
          onClick={() => setActiveTab('favorites')}
          className={`flex-1 text-center py-2 text-xs font-bold transition-all relative pointer-coarse:min-h-11 ${
            activeTab === 'favorites' ? 'text-white' : 'text-slate-500 hover:text-slate-300'
          }`}
        >
          Favorites
          {activeTab === 'favorites' && (
            <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-gradient-to-r from-purple-500 to-indigo-500" />
          )}
        </button>
        <button
          onClick={() => setActiveTab('all')}
          className={`flex-1 text-center py-2 text-xs font-bold transition-all relative pointer-coarse:min-h-11 ${
            activeTab === 'all' ? 'text-white' : 'text-slate-500 hover:text-slate-300'
          }`}
        >
          All ({devices.length})
          {activeTab === 'all' && (
            <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-gradient-to-r from-purple-500 to-indigo-500" />
          )}
        </button>
        <button
          onClick={() => setActiveTab('room')}
          className={`flex-1 text-center py-2 text-xs font-bold transition-all relative pointer-coarse:min-h-11 ${
            activeTab === 'room' ? 'text-white' : 'text-slate-500 hover:text-slate-300'
          }`}
        >
          Rooms
          {activeTab === 'room' && (
            <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-gradient-to-r from-purple-500 to-indigo-500" />
          )}
        </button>
      </div>

      {/* Tab Contents */}
      <div className="space-y-2 pr-1">
        {activeTab === 'favorites' && (
          favoriteDevicesList.length === 0 ? (
            <div className="flex flex-col items-center justify-center text-center py-8">
              <span className="text-xl mb-1.5 opacity-50">⭐</span>
              <p className="text-xs text-slate-400 font-medium">No favorite devices yet</p>
              <button
                onClick={() => setActiveTab('all')}
                className="text-[10px] text-indigo-400 hover:text-indigo-300 font-bold mt-2 hover:underline"
              >
                Browse All Devices →
              </button>
            </div>
          ) : (
            favoriteDevicesList.map(renderDeviceRow)
          )
        )}

        {activeTab === 'all' && (
          allDevicesGrouped.map((group) => (
            <div key={group.domain} className="space-y-1.5">
              <p className="text-[9px] font-black uppercase text-slate-500 tracking-wider mt-2 mb-1 border-b border-slate-800/40 pb-0.5">
                {group.label}
              </p>
              {group.devices.map(renderDeviceRow)}
            </div>
          ))
        )}

        {activeTab === 'room' && (
          hasRooms ? renderRoomGroups() : (
            <div className="text-center py-6">
              <p className="text-[10px] text-slate-500">No devices have a room yet</p>
              <p className="text-[9px] text-slate-600 mt-0.5">Assign areas in the home hub and devices group here.</p>
            </div>
          )
        )}
      </div>

      {/* Device Details Dialog */}
      {/* Portalled for the same reason as the expanded card: a transformed
          ancestor would otherwise anchor this "fixed" dialog to the page. */}
      {selectedDevice && createPortal(
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <div className="fixed inset-0 bg-slate-950/80 backdrop-blur-sm" onClick={() => setSelectedDevice(null)} />
          
          <div
            role="dialog"
            aria-modal="true"
            aria-label="Device details"
            className="relative w-full max-w-md max-h-[90dvh] overflow-y-auto bg-slate-900 border border-white/10 p-5 sm:p-6 rounded-2xl shadow-2xl flex flex-col gap-4 animate-in fade-in zoom-in duration-200"
          >
            <div className="sticky -top-5 sm:-top-6 z-20 -mx-5 sm:-mx-6 -mt-5 sm:-mt-6 flex items-center justify-between gap-2 border-b border-white/10 bg-slate-900/95 px-4 py-2 backdrop-blur">
              <span className="text-[10px] font-bold uppercase tracking-wider text-slate-500">Device details</span>
              <button
                onClick={() => setSelectedDevice(null)}
                aria-label="Close device details"
                className="flex min-h-11 min-w-11 items-center justify-center rounded-lg p-1 text-slate-400 hover:bg-white/5 hover:text-white"
              >
                <X size={18} />
              </button>
            </div>

            {/* Title */}
            <div>
              <div className="flex items-center gap-2">
                <span className="text-2xl">{getDeviceIcon(selectedDevice.domain)}</span>
                <div>
                  <h3 className="text-sm font-bold text-white">{selectedDevice.friendly_name || selectedDevice.entity_id}</h3>
                </div>
              </div>
              <p className="text-[10px] text-slate-400 mt-2 flex items-center gap-2">
                <span className="bg-slate-800 px-2 py-0.5 rounded border border-slate-700">Domain: {getDomainLabel(selectedDevice.domain)}</span>
                {deviceRoom(selectedDevice) && (
                  <span className="bg-slate-800 px-2 py-0.5 rounded border border-slate-700">Room: {deviceRoom(selectedDevice)}</span>
                )}
              </p>
            </div>

            {/* Quick Actions */}
            <div className="flex items-center justify-between p-3 rounded-xl bg-slate-950/40 border border-white/5">
              <div>
                <p className="text-xs font-semibold text-slate-400">{['climate', 'cover'].includes(selectedDevice.domain) ? 'Power Status' : 'Favourite'}</p>
                <p className={`text-xs font-bold mt-0.5 ${isActive(selectedDevice.state) ? 'text-green-400' : 'text-slate-500'}`}>
                  {['climate', 'cover'].includes(selectedDevice.domain) ? `Currently ${selectedDevice.state}`
                    : pinnedDevices.includes(selectedDevice.entity_id) ? 'Shown first on the dashboard' : 'Star it to show it first'}
                </p>
              </div>
              <div className="flex items-center gap-1.5">
                <button
                  onClick={() => toggleFavorite(selectedDevice.entity_id)}
                  aria-label={pinnedDevices.includes(selectedDevice.entity_id) ? 'Remove from favourites' : 'Add to favourites'}
                  aria-pressed={pinnedDevices.includes(selectedDevice.entity_id)}
                  className="p-2 max-sm:min-h-11 max-sm:min-w-11 max-sm:flex max-sm:items-center max-sm:justify-center hover:bg-white/5 rounded-lg text-slate-400 hover:text-amber-400 transition-colors"
                >
                  <Star size={16} className={pinnedDevices.includes(selectedDevice.entity_id) ? 'fill-amber-400 text-amber-400' : ''} />
                </button>
                {/* The control library below has its own power switch; this one stays for thermostats and covers. */}
                {!['climate', 'cover'].includes(selectedDevice.domain) ? null : selectedToggleAction ? (
                  <button
                    onClick={() => toggleDevice(selectedDevice.entity_id, selectedDevice.state)}
                    className={`text-xs font-bold px-3 py-1.5 max-sm:min-h-11 rounded-lg border transition-all ${
                      isActive(selectedDevice.state)
                        ? 'bg-red-500/10 border-red-500/30 text-red-400 hover:bg-red-500/20'
                        : 'bg-green-500/10 border-green-500/30 text-green-400 hover:bg-green-500/20'
                    }`}
                  >
                    {selectedToggleAction.label} Device
                  </button>
                ) : (
                  <button
                    disabled
                    className="text-xs font-bold px-3 py-1.5 max-sm:min-h-11 rounded-lg border bg-slate-900/40 border-slate-800 text-slate-500 cursor-not-allowed"
                  >
                    No Toggle
                  </button>
                )}
              </div>
            </div>

            {/* Custom controls depending on device domain */}
            <div className="space-y-4 py-2 border-t border-b border-white/5">
              {/* Lights, media and everything else: the device control library (components/devices/controls). */}
              {selectedHubEntity && !['climate', 'cover'].includes(selectedDevice.domain) && (
                <DeviceControl entity={selectedHubEntity} name={selectedDevice.friendly_name || selectedDevice.entity_id}
                  disabled={selectedDevice.state === 'unavailable'} onChanged={() => void loadDevices()} />
              )}

              {selectedDevice.domain === 'climate' && (
                <div className="space-y-3">
                  <h4 className="text-xs font-bold text-slate-400 flex items-center gap-1">
                    <Thermometer size={12} /> Climate Thermostat Controls
                  </h4>
                  {heatCool ? (
                    <div className="grid grid-cols-2 gap-2">
                      <div className="rounded-xl border border-white/5 bg-white/[0.03] p-2.5">
                        <p className="text-[10px] font-bold uppercase tracking-wider text-amber-300">Heat</p>
                        <p className="mt-0.5 text-xl font-black text-white tabular-nums">
                          {lowSetpoint == null ? 'No data' : `${lowSetpoint}°`}
                        </p>
                        <div className="mt-1 flex items-center gap-2">
                          <button
                            disabled={lowSetpoint == null || highSetpoint == null}
                            aria-label="Lower heat setpoint"
                            onClick={() => {
                              if (lowSetpoint == null || highSetpoint == null) return;
                              const next = Math.min(lowSetpoint - climateStep, highSetpoint);
                              patchSelectedClimate({ target_temp_low: next });
                              callHAService('climate', 'set_temperature', selectedDevice.entity_id, {
                                target_temp_low: next,
                                target_temp_high: highSetpoint,
                              });
                            }}
                            className="flex h-9 w-9 max-sm:h-11 max-sm:w-11 items-center justify-center rounded-full border border-slate-700 bg-slate-800 font-bold text-white transition-colors hover:bg-slate-700 disabled:opacity-40"
                          >
                            -
                          </button>
                          <button
                            disabled={lowSetpoint == null || highSetpoint == null}
                            aria-label="Raise heat setpoint"
                            onClick={() => {
                              if (lowSetpoint == null || highSetpoint == null) return;
                              const next = Math.min(lowSetpoint + climateStep, highSetpoint);
                              patchSelectedClimate({ target_temp_low: next });
                              callHAService('climate', 'set_temperature', selectedDevice.entity_id, {
                                target_temp_low: next,
                                target_temp_high: highSetpoint,
                              });
                            }}
                            className="flex h-9 w-9 max-sm:h-11 max-sm:w-11 items-center justify-center rounded-full border border-slate-700 bg-slate-800 font-bold text-white transition-colors hover:bg-slate-700 disabled:opacity-40"
                          >
                            +
                          </button>
                        </div>
                      </div>
                      <div className="rounded-xl border border-white/5 bg-white/[0.03] p-2.5">
                        <p className="text-[10px] font-bold uppercase tracking-wider text-cyan-300">Cool</p>
                        <p className="mt-0.5 text-xl font-black text-white tabular-nums">
                          {highSetpoint == null ? 'No data' : `${highSetpoint}°`}
                        </p>
                        <div className="mt-1 flex items-center gap-2">
                          <button
                            disabled={lowSetpoint == null || highSetpoint == null}
                            aria-label="Lower cool setpoint"
                            onClick={() => {
                              if (lowSetpoint == null || highSetpoint == null) return;
                              const next = Math.max(highSetpoint - climateStep, lowSetpoint);
                              patchSelectedClimate({ target_temp_high: next });
                              callHAService('climate', 'set_temperature', selectedDevice.entity_id, {
                                target_temp_low: lowSetpoint,
                                target_temp_high: next,
                              });
                            }}
                            className="flex h-9 w-9 max-sm:h-11 max-sm:w-11 items-center justify-center rounded-full border border-slate-700 bg-slate-800 font-bold text-white transition-colors hover:bg-slate-700 disabled:opacity-40"
                          >
                            -
                          </button>
                          <button
                            disabled={lowSetpoint == null || highSetpoint == null}
                            aria-label="Raise cool setpoint"
                            onClick={() => {
                              if (lowSetpoint == null || highSetpoint == null) return;
                              const next = Math.max(highSetpoint + climateStep, lowSetpoint);
                              patchSelectedClimate({ target_temp_high: next });
                              callHAService('climate', 'set_temperature', selectedDevice.entity_id, {
                                target_temp_low: lowSetpoint,
                                target_temp_high: next,
                              });
                            }}
                            className="flex h-9 w-9 max-sm:h-11 max-sm:w-11 items-center justify-center rounded-full border border-slate-700 bg-slate-800 font-bold text-white transition-colors hover:bg-slate-700 disabled:opacity-40"
                          >
                            +
                          </button>
                        </div>
                      </div>
                    </div>
                  ) : (
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="text-xs text-slate-500">Target Temperature</p>
                        <p className="text-2xl font-black text-white mt-1">
                          {targetTemperature == null ? 'No data' : `${targetTemperature}°`}
                        </p>
                      </div>
                      <div className="flex gap-2">
                        <button
                          disabled={targetTemperature == null}
                          aria-label="Lower target temperature"
                          onClick={() => {
                            if (targetTemperature == null) return;
                            const next = targetTemperature - climateStep;
                            patchSelectedClimate({ temperature: next });
                            callHAService('climate', 'set_temperature', selectedDevice.entity_id, { temperature: next });
                          }}
                          className="w-11 h-11 rounded-full bg-slate-800 border border-slate-700 flex items-center justify-center font-bold text-white hover:bg-slate-700 transition-colors disabled:opacity-40"
                        >
                          -
                        </button>
                        <button
                          disabled={targetTemperature == null}
                          aria-label="Raise target temperature"
                          onClick={() => {
                            if (targetTemperature == null) return;
                            const next = targetTemperature + climateStep;
                            patchSelectedClimate({ temperature: next });
                            callHAService('climate', 'set_temperature', selectedDevice.entity_id, { temperature: next });
                          }}
                          className="w-11 h-11 rounded-full bg-slate-800 border border-slate-700 flex items-center justify-center font-bold text-white hover:bg-slate-700 transition-colors disabled:opacity-40"
                        >
                          +
                        </button>
                      </div>
                    </div>
                  )}
                  <div className="space-y-1">
                    <p className="text-xs text-slate-500">HVAC Operation Mode</p>
                    <div className="flex flex-wrap gap-2">
                      {modesFor(selectedDevice).map((mode) => (
                        <button
                          key={mode}
                          onClick={() => {
                            patchSelectedClimate({ hvac_mode: mode });
                            callHAService('climate', 'set_hvac_mode', selectedDevice.entity_id, { hvac_mode: mode });
                          }}
                          aria-pressed={climateMode === mode}
                          className={`text-xs font-semibold px-3 py-1.5 max-sm:min-h-11 rounded-lg border uppercase ${
                            climateMode === mode
                              ? 'bg-indigo-600/20 border-indigo-500 text-white'
                              : 'bg-slate-950/20 border-slate-800 text-slate-500 hover:text-slate-300'
                          }`}
                        >
                          {titleize(mode)}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              )}

              {selectedDevice.domain === 'cover' && (
                <div className="space-y-3">
                  <h4 className="text-xs font-bold text-slate-400 flex items-center gap-1">
                    <Sliders size={12} /> Shade Cover Controls
                  </h4>
                  <div className="grid grid-cols-3 gap-2">
                    <button
                      onClick={() => callHAService('cover', 'open_cover', selectedDevice.entity_id)}
                      className="flex items-center justify-center gap-1 bg-slate-800 hover:bg-slate-700 text-white py-2 max-sm:min-h-11 rounded-xl text-xs font-bold transition-colors"
                    >
                      <ArrowUp size={14} /> Open
                    </button>
                    <button
                      onClick={() => callHAService('cover', 'stop_cover', selectedDevice.entity_id)}
                      className="flex items-center justify-center gap-1 bg-slate-800 hover:bg-slate-700 text-white py-2 max-sm:min-h-11 rounded-xl text-xs font-bold transition-colors"
                    >
                      Stop
                    </button>
                    <button
                      onClick={() => callHAService('cover', 'close_cover', selectedDevice.entity_id)}
                      className="flex items-center justify-center gap-1 bg-slate-800 hover:bg-slate-700 text-white py-2 max-sm:min-h-11 rounded-xl text-xs font-bold transition-colors"
                    >
                      <ArrowDown size={14} /> Close
                    </button>
                  </div>
                </div>
              )}

            </div>

            {/* Who may use this device: anyone whose role can assign devices */}
            {canAssign && (
              <fieldset className="space-y-2" disabled={updatingAssignment}>
                <legend className="text-xs font-bold text-slate-400 flex items-center gap-1.5">
                  <Shield size={12} className="text-indigo-400" /> Who can use it
                </legend>
                {assignmentsError ? (
                  // An unticked box is a claim about access; say when we could
                  // not read who actually has it.
                  <p className="text-[10px] text-amber-200">
                    Could not read who can use this device. Reopen the panel to try again.
                  </p>
                ) : (
                  <p className="text-[10px] text-slate-500">
                    Everyone ticked can control it, as can anyone whose role covers every device.
                  </p>
                )}
                {allUsers.map((u) => (
                  <label key={u.username} className="flex items-center gap-3 min-h-11 rounded-xl px-3 bg-slate-950/20 text-sm text-slate-200">
                    <input
                      type="checkbox"
                      checked={assignedUsers.has(u.username)}
                      onChange={(e) => void handleAssign(u.username, e.target.checked)}
                      className="h-4 w-4"
                    />
                    {u.display_name || u.username}
                  </label>
                ))}
              </fieldset>
            )}
          </div>
        </div>,
        document.body,
      )}
    </WidgetCard>
  );
};

export default DeviceControlWidget;
