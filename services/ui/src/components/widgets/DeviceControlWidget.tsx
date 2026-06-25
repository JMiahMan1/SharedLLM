import { useState, useCallback, useMemo, useEffect } from 'react';
import { useAuth } from '../../context/AuthContext';
import { useWidgetStore } from '../../stores/widgetStore';
import { api, apiClient } from '../../services/api';
import type { DeviceEntry, IWidgetProps } from '../../types/widget';
import { WidgetCard } from './WidgetCard';
import { 
  Star, 
  Power, 
  Tv, 
  Bluetooth, 
  MapPin, 
  Lock, 
  Volume2, 
  Play, 
  Pause, 
  SkipForward, 
  SkipBack, 
  X, 
  Shield, 
  RefreshCw, 
  Layers,
  Thermometer,
  ArrowUp,
  ArrowDown,
  Info,
  Sliders
} from 'lucide-react';
import toast from 'react-hot-toast';

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

const resolveDeviceRoom = (device: DeviceEntry): string => {
  if (device.room && device.room !== 'unknown') return device.room;
  const name = (device.friendly_name || device.entity_id).toLowerCase();
  if (name.includes('living') || name.includes('tv') || name.includes('couch')) return 'Living Room';
  if (name.includes('bedroom') || name.includes('bed') || name.includes('sleep')) return 'Bedroom';
  if (name.includes('kitchen') || name.includes('cook') || name.includes('dining') || name.includes('spots')) return 'Kitchen';
  if (name.includes('office') || name.includes('desk') || name.includes('work')) return 'Office';
  if (name.includes('garage') || name.includes('car')) return 'Garage';
  return 'Living Room'; // Fallback
};

const DeviceControlWidget = ({ settingsButton }: IWidgetProps) => {
  const { user, role } = useAuth();
  const togglePinnedDevice = useWidgetStore((s) => s.togglePinnedDevice);
  const pinnedDevices = useWidgetStore(
    (s) => s.userWidgets['device_control']?.pinned_devices || []
  );

  const [devices, setDevices] = useState<DeviceEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Tabs
  const [activeTab, setActiveTab] = useState<'favorites' | 'all' | 'room'>('favorites');

  // Room presence state
  const [currentRoom, setCurrentRoom] = useState<string>('Living Room');
  const [bleScanning, setBleScanning] = useState(false);

  // Assignments & Users
  const [assignments, setAssignments] = useState<{ id: number; device_id: string; username: string }[]>([]);
  const [allUsers, setAllUsers] = useState<{ username: string; display_name?: string; is_admin?: boolean }[]>([]);

  // Detailed modal
  const [selectedDevice, setSelectedDevice] = useState<DeviceEntry | null>(null);
  const [adminAssignments, setAdminAssignments] = useState<string[]>([]);
  const [updatingAssignment, setUpdatingAssignment] = useState(false);

  // Local attribute state for controls inside modal
  const [brightness, setBrightness] = useState<number>(100);
  const [temperature, setTemperature] = useState<number>(72);
  const [volume, setVolume] = useState<number>(50);

  const loadDevices = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // Load device states from server
      const state = await api.getDeviceStates(['light', 'switch', 'media_player', 'cover', 'climate', 'lock', 'fan']);
      setDevices(state);

      // Load user device assignments
      const userDevices = await api.getDevices();
      setAssignments(userDevices);

      // Load system users for admin assignments panel
      if (role === 'admin') {
        const users = await api.getUsers();
        setAllUsers(users);
      }
    } catch (err) {
      console.error(err);
      setError('Failed to load device information');
    } finally {
      setLoading(false);
    }
  }, [role]);

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

  // Check if current user has permission to control this device
  const hasControlPermission = useCallback((entityId: string) => {
    if (role === 'admin') return true;
    return assignments.some(
      (a) => a.device_id === entityId && a.username.toLowerCase() === user?.username?.toLowerCase()
    );
  }, [role, assignments, user]);

  const callHAService = useCallback(async (domain: string, service: string, entityId: string, serviceData: unknown = null) => {
    if (!hasControlPermission(entityId)) {
      toast.error('Access Denied: You are not assigned to control this device.');
      return;
    }

    try {
      await apiClient.post('/execute/ha_service', {
        domain,
        service,
        entity_id: entityId,
        service_data: serviceData,
      });
      toast.success('Command sent successfully');
      loadDevices();
    } catch (err) {
      console.error('HA Service Error:', err);
      toast.error('Failed to send command to device');
    }
  }, [hasControlPermission, loadDevices]);

  const toggleDevice = useCallback(
    async (entityId: string, currentState: string) => {
      if (!hasControlPermission(entityId)) {
        toast.error('Access Denied: You are not assigned to control this device.');
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
        toast.success(currentPinned.includes(entityId) ? 'Removed from favorites' : 'Added to favorites');
      } catch {
        toast.error('Failed to update favorite status');
      }
    },
    [togglePinnedDevice]
  );

  // Scan BLE room beacon simulator
  const handleBLEScan = () => {
    setBleScanning(true);
    setTimeout(() => {
      const rooms = ['Living Room', 'Bedroom', 'Kitchen', 'Office'];
      const currentIdx = rooms.indexOf(currentRoom);
      const nextRoom = rooms[(currentIdx + 1) % rooms.length];
      setCurrentRoom(nextRoom);
      setBleScanning(false);
      toast.success(`BLE Beacon detected: Entered ${nextRoom}`);
    }, 1800);
  };

  // Open modal & load detailed settings
  const handleOpenDetail = async (device: DeviceEntry) => {
    setSelectedDevice(device);
    
    // Set initial values from state/attributes
    if (device.domain === 'light') {
      setBrightness(100);
    } else if (device.domain === 'climate') {
      setTemperature(72);
    } else if (device.domain === 'media_player') {
      setVolume(50);
    }

    // Filter assignments for this specific device
    const deviceAssignedUsers = assignments
      .filter((a) => a.device_id === device.entity_id)
      .map((a) => a.username);
    setAdminAssignments(deviceAssignedUsers);
  };

  const selectedToggleAction = selectedDevice
    ? getToggleAction(selectedDevice.domain, selectedDevice.state)
    : null;

  // Handle Admin User Assignment changes
  const handleToggleUserAssignment = async (username: string) => {
    if (!selectedDevice) return;
    
    setUpdatingAssignment(true);
    const targetEntity = selectedDevice.entity_id;
    const isAssigned = adminAssignments.includes(username);

    try {
      if (isAssigned) {
        // Unassign
        await api.deleteDeviceAssignment(targetEntity);
        setAdminAssignments((prev) => prev.filter((u) => u !== username));
        toast.success(`Revoked assignment for ${username}`);
      } else {
        // Assign
        await api.updateDeviceAssignment({ username, device_id: targetEntity });
        setAdminAssignments((prev) => [...prev, username]);
        toast.success(`Assigned control to ${username}`);
      }
      
      // Reload assignments list
      const userDevices = await api.getDevices();
      setAssignments(userDevices);
    } catch (e) {
      console.error(e);
      toast.error('Failed to update user assignment');
    } finally {
      setUpdatingAssignment(false);
    }
  };

  // Filtered lists
  const favoriteDevicesList = useMemo(() => {
    return devices.filter((d) => pinnedDevices.includes(d.entity_id));
  }, [devices, pinnedDevices]);

  const allDevicesGrouped = useMemo(() => {
    const groups: Record<string, DeviceEntry[]> = {};
    for (const d of devices) {
      const dom = d.domain || 'other';
      if (!groups[dom]) groups[dom] = [];
      groups[dom].push(d);
    }
    return Object.entries(groups).map(([domain, devs]) => ({
      domain,
      label: getDomainLabel(domain),
      devices: devs.sort((a, b) => (a.friendly_name || '').localeCompare(b.friendly_name || '')),
    })).sort((a, b) => a.label.localeCompare(b.label));
  }, [devices]);

  const roomDevicesList = useMemo(() => {
    return devices.filter((d) => resolveDeviceRoom(d) === currentRoom);
  }, [devices, currentRoom]);

  // Render a standard device row
  const renderDeviceRow = (device: DeviceEntry) => {
    const isFav = pinnedDevices.includes(device.entity_id);
    const isDeviceActive = isActive(device.state);
    const hasControl = hasControlPermission(device.entity_id);
    const room = resolveDeviceRoom(device);

    return (
      <div
        key={device.entity_id}
        onClick={() => handleOpenDetail(device)}
        className="flex items-center justify-between p-3 rounded-xl bg-slate-900/30 border border-slate-800/40 hover:bg-slate-800/20 hover:border-white/5 transition-all duration-200 cursor-pointer group"
      >
        <div className="flex items-center gap-3 min-w-0 flex-1">
          <span className="text-xl p-2 bg-slate-950/40 border border-white/5 rounded-lg shrink-0">
            {getDeviceIcon(device.domain)}
          </span>
          <div className="min-w-0">
            <div className="flex items-center gap-1.5">
              <p className="text-xs font-semibold text-white group-hover:text-purple-300 transition-colors truncate">
                {device.friendly_name || device.entity_id}
              </p>
              {!hasControl && (
                <span className="text-[9px] bg-red-950/40 border border-red-500/20 text-red-400 px-1 py-0.2 rounded flex items-center gap-0.5">
                  <Lock size={8} /> Restricted
                </span>
              )}
            </div>
            <p className="text-[10px] text-slate-500 flex items-center gap-1 mt-0.5">
              <span>{getDomainLabel(device.domain)}</span>
              <span>•</span>
              <span>{room}</span>
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2 flex-shrink-0" onClick={(e) => e.stopPropagation()}>
          <button
            onClick={() => toggleFavorite(device.entity_id)}
            className="p-1.5 text-slate-500 hover:text-amber-400 transition-colors"
            title={isFav ? 'Remove Favorite' : 'Favorite'}
          >
            <Star size={14} className={isFav ? 'fill-amber-400 text-amber-400' : ''} />
          </button>
          
          {getToggleAction(device.domain, device.state) ? (
            <button
              onClick={() => toggleDevice(device.entity_id, device.state)}
              disabled={!hasControl}
              className={`text-xs px-3 py-1 rounded-lg font-semibold transition-all duration-200 ${
                isDeviceActive
                  ? 'bg-green-500/10 border border-green-500/30 text-green-400 hover:bg-green-500/20'
                  : 'bg-slate-950/40 border border-slate-800 text-slate-400 hover:text-white hover:border-slate-700'
              } disabled:opacity-30 disabled:cursor-not-allowed`}
            >
              <Power size={12} className="inline mr-1" />
              {getToggleAction(device.domain, device.state)?.label}
            </button>
          ) : (
            <button
              disabled
              className="text-xs px-3 py-1 rounded-lg font-semibold bg-slate-900/40 border border-slate-800 text-slate-500 cursor-not-allowed"
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
            className="p-1 hover:bg-white/5 rounded text-slate-400 hover:text-white transition-colors"
            title="Refresh device states"
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

            {/* Middle Col: BLE & Room Context */}
            <div className="glass-card p-4 flex flex-col h-[400px] border border-white/5 bg-slate-900/10">
              <div className="flex items-center justify-between mb-3">
                <h5 className="text-xs font-black uppercase tracking-widest text-slate-400 flex items-center gap-1.5">
                  <Bluetooth size={12} className="text-indigo-400" /> BLE Room Scan
                </h5>
                <button
                  onClick={handleBLEScan}
                  disabled={bleScanning}
                  className="text-[10px] font-bold text-indigo-400 hover:text-indigo-300 disabled:opacity-50 transition-colors flex items-center gap-1"
                >
                  <MapPin size={10} className={bleScanning ? 'animate-pulse' : ''} />
                  {bleScanning ? 'Scanning...' : 'Scan Beacons'}
                </button>
              </div>

              <div className="bg-indigo-950/20 border border-indigo-500/20 rounded-xl p-3 mb-4 flex items-center justify-between">
                <div>
                  <p className="text-[10px] text-indigo-400 font-bold uppercase tracking-wider">BLE Presence Location</p>
                  <p className="text-sm font-black text-white mt-0.5">{currentRoom}</p>
                </div>
                <div className="h-2 w-2 rounded-full bg-indigo-500 animate-ping shrink-0" />
              </div>

              <div className="flex-1 overflow-y-auto space-y-2 pr-1">
                {roomDevicesList.length === 0 ? (
                  <div className="h-full flex flex-col items-center justify-center text-center p-6">
                    <span className="text-2xl mb-2">📡</span>
                    <p className="text-xs text-slate-400">No BLE devices in {currentRoom}</p>
                    <p className="text-[10px] text-slate-600 mt-1">Try scanning for a different room beacon.</p>
                  </div>
                ) : (
                  roomDevicesList.map(renderDeviceRow)
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
          className={`flex-1 text-center py-2 text-xs font-bold transition-all relative ${
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
          className={`flex-1 text-center py-2 text-xs font-bold transition-all relative ${
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
          className={`flex-1 text-center py-2 text-xs font-bold transition-all relative ${
            activeTab === 'room' ? 'text-white' : 'text-slate-500 hover:text-slate-300'
          }`}
        >
          BLE ({currentRoom.split(' ')[0]})
          {activeTab === 'room' && (
            <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-gradient-to-r from-purple-500 to-indigo-500" />
          )}
        </button>
      </div>

      {/* Tab Contents */}
      <div className="flex-1 overflow-y-auto space-y-2 pr-1 max-h-[300px]">
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
          <div className="space-y-3">
            {/* BLE scan header */}
            <div className="flex items-center justify-between p-2 rounded-xl bg-indigo-950/10 border border-indigo-500/10">
              <div className="min-w-0">
                <p className="text-[9px] font-bold text-indigo-400 uppercase tracking-wide">Current Room (BLE Scan)</p>
                <p className="text-xs font-black text-white mt-0.5">{currentRoom}</p>
              </div>
              <button
                onClick={handleBLEScan}
                disabled={bleScanning}
                className="text-[10px] bg-indigo-600 text-white font-bold px-2.5 py-1 rounded-lg hover:bg-indigo-500 transition-colors disabled:opacity-50 flex items-center gap-1"
              >
                <Bluetooth size={10} className={bleScanning ? 'animate-pulse' : ''} />
                {bleScanning ? 'Scanning...' : 'Scan'}
              </button>
            </div>

            <div className="space-y-2">
              {roomDevicesList.length === 0 ? (
                <div className="text-center py-6">
                  <p className="text-[10px] text-slate-500">No controllable devices in {currentRoom}</p>
                  <p className="text-[9px] text-slate-600 mt-0.5">Assigned devices update dynamically.</p>
                </div>
              ) : (
                roomDevicesList.map(renderDeviceRow)
              )}
            </div>
          </div>
        )}
      </div>

      {/* Device Details Dialog */}
      {selectedDevice && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <div className="fixed inset-0 bg-slate-950/80 backdrop-blur-sm" onClick={() => setSelectedDevice(null)} />
          
          <div className="relative w-full max-w-md bg-slate-900 border border-white/10 p-6 rounded-2xl shadow-2xl flex flex-col gap-4 animate-in fade-in zoom-in duration-200">
            <button
              onClick={() => setSelectedDevice(null)}
              className="absolute top-4 right-4 text-slate-400 hover:text-white p-1 rounded hover:bg-white/5"
            >
              <X size={18} />
            </button>

            {/* Title */}
            <div>
              <div className="flex items-center gap-2">
                <span className="text-2xl">{getDeviceIcon(selectedDevice.domain)}</span>
                <div>
                  <h3 className="text-sm font-bold text-white">{selectedDevice.friendly_name || selectedDevice.entity_id}</h3>
                  <p className="text-[10px] font-mono text-slate-500 truncate max-w-xs">{selectedDevice.entity_id}</p>
                </div>
              </div>
              <p className="text-[10px] text-slate-400 mt-2 flex items-center gap-2">
                <span className="bg-slate-800 px-2 py-0.5 rounded border border-slate-700">Domain: {getDomainLabel(selectedDevice.domain)}</span>
                <span className="bg-slate-800 px-2 py-0.5 rounded border border-slate-700">Location: {resolveDeviceRoom(selectedDevice)}</span>
              </p>
            </div>

            {/* Quick Actions */}
            <div className="flex items-center justify-between p-3 rounded-xl bg-slate-950/40 border border-white/5">
              <div>
                <p className="text-xs font-semibold text-slate-400">Power Status</p>
                <p className={`text-xs font-bold mt-0.5 ${isActive(selectedDevice.state) ? 'text-green-400' : 'text-slate-500'}`}>
                  Currently {selectedDevice.state}
                </p>
              </div>
              <div className="flex items-center gap-1.5">
                <button
                  onClick={() => toggleFavorite(selectedDevice.entity_id)}
                  className="p-2 hover:bg-white/5 rounded-lg text-slate-400 hover:text-amber-400 transition-colors"
                >
                  <Star size={16} className={pinnedDevices.includes(selectedDevice.entity_id) ? 'fill-amber-400 text-amber-400' : ''} />
                </button>
                {selectedToggleAction ? (
                  <button
                    onClick={() => toggleDevice(selectedDevice.entity_id, selectedDevice.state)}
                    className={`text-xs font-bold px-3 py-1.5 rounded-lg border transition-all ${
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
                    className="text-xs font-bold px-3 py-1.5 rounded-lg border bg-slate-900/40 border-slate-800 text-slate-500 cursor-not-allowed"
                  >
                    No Toggle
                  </button>
                )}
              </div>
            </div>

            {/* Custom controls depending on device domain */}
            <div className="space-y-4 py-2 border-t border-b border-white/5">
              {selectedDevice.domain === 'light' && (
                <div className="space-y-3">
                  <h4 className="text-xs font-bold text-slate-400 flex items-center gap-1">
                    <Sliders size={12} /> Light Controls
                  </h4>
                  <div className="space-y-1">
                    <div className="flex justify-between text-xs text-slate-500">
                      <span>Brightness</span>
                      <span>{brightness}%</span>
                    </div>
                    <input
                      type="range"
                      min="1"
                      max="100"
                      value={brightness}
                      onChange={(e) => setBrightness(Number(e.target.value))}
                      onMouseUp={() => callHAService('light', 'turn_on', selectedDevice.entity_id, { brightness: Math.round(brightness * 2.55) })}
                      onTouchEnd={() => callHAService('light', 'turn_on', selectedDevice.entity_id, { brightness: Math.round(brightness * 2.55) })}
                      className="w-full accent-purple-500 bg-slate-800 h-1 rounded-lg cursor-pointer"
                    />
                  </div>
                  <div className="space-y-1.5">
                    <p className="text-xs text-slate-500">Quick Color Temperature Presets</p>
                    <div className="flex gap-2">
                      <button
                        onClick={() => callHAService('light', 'turn_on', selectedDevice.entity_id, { color_temp: 153 })}
                        className="text-[10px] font-semibold px-2 py-1 rounded bg-orange-400/10 border border-orange-400/20 text-orange-400"
                      >
                        Warm (2700K)
                      </button>
                      <button
                        onClick={() => callHAService('light', 'turn_on', selectedDevice.entity_id, { color_temp: 250 })}
                        className="text-[10px] font-semibold px-2 py-1 rounded bg-yellow-400/10 border border-yellow-400/20 text-yellow-400"
                      >
                        Natural (4000K)
                      </button>
                      <button
                        onClick={() => callHAService('light', 'turn_on', selectedDevice.entity_id, { color_temp: 370 })}
                        className="text-[10px] font-semibold px-2 py-1 rounded bg-blue-400/10 border border-blue-400/20 text-blue-400"
                      >
                        Cool (6500K)
                      </button>
                    </div>
                  </div>
                </div>
              )}

              {selectedDevice.domain === 'climate' && (
                <div className="space-y-3">
                  <h4 className="text-xs font-bold text-slate-400 flex items-center gap-1">
                    <Thermometer size={12} /> Climate Thermostat Controls
                  </h4>
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-xs text-slate-500">Target Temperature</p>
                      <p className="text-2xl font-black text-white mt-1">{temperature}°F</p>
                    </div>
                    <div className="flex gap-2">
                      <button
                        onClick={() => {
                          const next = temperature - 1;
                          setTemperature(next);
                          callHAService('climate', 'set_temperature', selectedDevice.entity_id, { temperature: next });
                        }}
                        className="w-10 h-10 rounded-full bg-slate-800 border border-slate-700 flex items-center justify-center font-bold text-white hover:bg-slate-700 transition-colors"
                      >
                        -
                      </button>
                      <button
                        onClick={() => {
                          const next = temperature + 1;
                          setTemperature(next);
                          callHAService('climate', 'set_temperature', selectedDevice.entity_id, { temperature: next });
                        }}
                        className="w-10 h-10 rounded-full bg-slate-800 border border-slate-700 flex items-center justify-center font-bold text-white hover:bg-slate-700 transition-colors"
                      >
                        +
                      </button>
                    </div>
                  </div>
                  <div className="space-y-1">
                    <p className="text-xs text-slate-500">HVAC Operation Mode</p>
                    <div className="flex gap-2">
                      {['cool', 'heat', 'off'].map((mode) => (
                        <button
                          key={mode}
                          onClick={() => callHAService('climate', 'set_hvac_mode', selectedDevice.entity_id, { hvac_mode: mode })}
                          className={`text-xs font-semibold px-3 py-1.5 rounded-lg border uppercase ${
                            selectedDevice.state.toLowerCase() === mode
                              ? 'bg-indigo-600/20 border-indigo-500 text-white'
                              : 'bg-slate-950/20 border-slate-800 text-slate-500 hover:text-slate-300'
                          }`}
                        >
                          {mode}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              )}

              {selectedDevice.domain === 'media_player' && (
                <div className="space-y-3">
                  <h4 className="text-xs font-bold text-slate-400 flex items-center gap-1">
                    <Tv size={12} /> Media Controls
                  </h4>
                  {/* Playback bar */}
                  <div className="flex items-center justify-center gap-4 bg-slate-950/30 border border-white/5 py-3 rounded-xl">
                    <button
                      onClick={() => callHAService('media_player', 'media_previous_track', selectedDevice.entity_id)}
                      className="p-2 hover:bg-white/5 rounded text-slate-400 hover:text-white transition-colors"
                    >
                      <SkipBack size={16} />
                    </button>
                    <button
                      onClick={() => callHAService('media_player', isActive(selectedDevice.state) ? 'media_pause' : 'media_play', selectedDevice.entity_id)}
                      className="p-3 bg-purple-600 hover:bg-purple-500 text-white rounded-full transition-colors flex items-center justify-center shadow-lg"
                    >
                      {isActive(selectedDevice.state) ? <Pause size={18} /> : <Play size={18} />}
                    </button>
                    <button
                      onClick={() => callHAService('media_player', 'media_next_track', selectedDevice.entity_id)}
                      className="p-2 hover:bg-white/5 rounded text-slate-400 hover:text-white transition-colors"
                    >
                      <SkipForward size={16} />
                    </button>
                  </div>
                  {/* Volume Slider */}
                  <div className="space-y-1">
                    <div className="flex justify-between text-xs text-slate-500">
                      <span className="flex items-center gap-1"><Volume2 size={12} /> Volume</span>
                      <span>{volume}%</span>
                    </div>
                    <input
                      type="range"
                      min="1"
                      max="100"
                      value={volume}
                      onChange={(e) => setVolume(Number(e.target.value))}
                      onMouseUp={() => callHAService('media_player', 'set_volume_level', selectedDevice.entity_id, { volume_level: volume / 100 })}
                      onTouchEnd={() => callHAService('media_player', 'set_volume_level', selectedDevice.entity_id, { volume_level: volume / 100 })}
                      className="w-full accent-purple-500 bg-slate-800 h-1 rounded-lg cursor-pointer"
                    />
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
                      className="flex items-center justify-center gap-1 bg-slate-800 hover:bg-slate-700 text-white py-2 rounded-xl text-xs font-bold transition-colors"
                    >
                      <ArrowUp size={14} /> Open
                    </button>
                    <button
                      onClick={() => callHAService('cover', 'stop_cover', selectedDevice.entity_id)}
                      className="flex items-center justify-center gap-1 bg-slate-800 hover:bg-slate-700 text-white py-2 rounded-xl text-xs font-bold transition-colors"
                    >
                      Stop
                    </button>
                    <button
                      onClick={() => callHAService('cover', 'close_cover', selectedDevice.entity_id)}
                      className="flex items-center justify-center gap-1 bg-slate-800 hover:bg-slate-700 text-white py-2 rounded-xl text-xs font-bold transition-colors"
                    >
                      <ArrowDown size={14} /> Close
                    </button>
                  </div>
                </div>
              )}

              {!['light', 'climate', 'media_player', 'cover'].includes(selectedDevice.domain) && (
                <div className="py-2 text-center text-xs text-slate-500 flex items-center justify-center gap-1.5 bg-slate-950/20 rounded-xl p-3">
                  <Info size={14} />
                  <span>No custom settings available for {getDomainLabel(selectedDevice.domain)}. Use standard toggle button.</span>
                </div>
              )}
            </div>

            {/* Admin Device Assignment Panel */}
            {role === 'admin' && (
              <div className="space-y-3">
                <h4 className="text-xs font-bold text-slate-400 flex items-center gap-1.5">
                  <Shield size={12} className="text-indigo-400" /> User Access Control (Admin)
                </h4>
                <p className="text-[10px] text-slate-500">Select which home members have permission to control this device.</p>
                <div className="max-h-36 overflow-y-auto space-y-2 border border-white/5 rounded-xl p-3 bg-slate-950/40">
                  {allUsers.length === 0 ? (
                    <p className="text-xs text-slate-500 text-center py-2">No other users configured</p>
                  ) : (
                    allUsers.map((userProfile) => {
                      const isAssigned = adminAssignments.includes(userProfile.username);
                      return (
                        <div key={userProfile.username} className="flex items-center justify-between">
                          <span className="text-xs text-white font-medium">{userProfile.display_name || userProfile.username}</span>
                          <input
                            type="checkbox"
                            checked={isAssigned || userProfile.is_admin}
                            disabled={userProfile.is_admin || updatingAssignment}
                            onChange={() => handleToggleUserAssignment(userProfile.username)}
                            className="w-4 h-4 rounded text-purple-600 bg-slate-800 border-slate-700 accent-purple-500 disabled:opacity-50"
                          />
                        </div>
                      );
                    })
                  )}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </WidgetCard>
  );
};

export default DeviceControlWidget;
