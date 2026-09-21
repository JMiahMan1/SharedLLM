import { useState, useEffect, useCallback } from 'react';
import { useBackgroundLocation } from '../../hooks/useBackgroundLocation';
import { useHaptics } from '../../hooks/useHaptics';
import { useAuth } from '../../context/AuthContext';
import { api } from '../../services/api';
import toast from 'react-hot-toast';
import {
  MapPin,
  Satellite,
  Zap,
  Car,
  Plus,
  Trash2,
  Gauge,
  Clock,
  Navigation,
  RefreshCw,
  ChevronDown,
  ChevronUp,
  ShieldCheck,
  AlertCircle
} from 'lucide-react';

interface Vehicle {
  id: string;
  name: string;
  mpg: number;
  cost_per_gallon: number;
  fuel_type: string;
}

interface TelemetryData {
  status: string;
  entity_id: string;
  friendly_name: string;
  latitude: number;
  longitude: number;
  accuracy?: number;
  battery?: number;
  is_moving: boolean;
  current_speed_mph: number;
  top_speed_mph: number;
  current_zone?: string | null;
  closest_zone?: string | null;
  closest_zone_distance_miles?: number | null;
  distance_to_home_miles?: number | null;
  dwell_time_seconds: number;
  dwell_time_formatted: string;
  distance_traveled_miles: number;
  frequented_locations: Array<{ name: string; dwell_seconds: number; dwell_formatted: string }>;
  vehicle?: {
    id: string;
    name: string;
    mpg: number;
    cost_per_gallon: number;
    fuel_type: string;
    gallons_used?: number;
    estimated_cost_usd?: number;
  } | null;
  speech: string;
}

const LocationPanel = () => {
  const { user } = useAuth();
  const { trigger } = useHaptics();
  const {
    latitude,
    longitude,
    accuracy,
    speed,
    isTracking,
    error,
    interval,
    startTracking,
    stopTracking
  } = useBackgroundLocation();

  const [showRawDetails, setShowRawDetails] = useState(false);
  const [vehicles, setVehicles] = useState<Vehicle[]>([]);
  const [assignedVehicleId, setAssignedVehicleId] = useState<string | null>(null);
  const [telemetry, setTelemetry] = useState<TelemetryData | null>(null);
  const [loadingTelemetry, setLoadingTelemetry] = useState(false);
  const [showFleetManager, setShowFleetManager] = useState(false);
  const [showAddForm, setShowAddForm] = useState(false);

  // New vehicle form state
  const [newVehicleName, setNewVehicleName] = useState('');
  const [newVehicleMpg, setNewVehicleMpg] = useState('');
  const [newVehicleCost, setNewVehicleCost] = useState('');
  const [newVehicleFuelType, setNewVehicleFuelType] = useState('gasoline');
  const [isSavingVehicle, setIsSavingVehicle] = useState(false);

  // Vehicle lookup state
  const [lookupYear, setLookupYear] = useState('');
  const [lookupMake, setLookupMake] = useState('');
  const [lookupModel, setLookupModel] = useState('');
  const [lookupYears, setLookupYears] = useState<Array<{ text: string; value: string }>>([]);
  const [lookupMakes, setLookupMakes] = useState<Array<{ text: string; value: string }>>([]);
  const [lookupModels, setLookupModels] = useState<Array<{ text: string; value: string }>>([]);
  const [lookupOptions, setLookupOptions] = useState<Array<{ text: string; value: string }>>([]);
  const [lookupOption, setLookupOption] = useState('');
  const [isLoadingLookup, setIsLoadingLookup] = useState(false);
  const [fuelPriceSource, setFuelPriceSource] = useState('');
  const [fuelPriceZip, setFuelPriceZip] = useState('');
  const [isLoadingFuelPrice, setIsLoadingFuelPrice] = useState(false);
  const [lastFetchedPrices, setLastFetchedPrices] = useState<{ regular?: number | null; midgrade?: number | null; premium?: number | null; diesel?: number | null } | null>(null);

  const username = user?.username || 'jeremiah';

  const refreshData = useCallback(async () => {
    try {
      setLoadingTelemetry(true);
      const [vehRes, assignRes, telemRes] = await Promise.allSettled([
        api.getVehicles(),
        api.getAssignedVehicle(username),
        api.getGeoTelemetry(username, 24)
      ]);

      if (vehRes.status === 'fulfilled' && vehRes.value?.vehicles) {
        setVehicles(vehRes.value.vehicles);
      }
      if (assignRes.status === 'fulfilled' && assignRes.value) {
        setAssignedVehicleId(assignRes.value.vehicle_id || null);
      }
      if (telemRes.status === 'fulfilled' && telemRes.value) {
        setTelemetry(telemRes.value);
      }
    } catch (err) {
      console.error('Failed to load location/vehicle data:', err);
    } finally {
      setLoadingTelemetry(false);
    }
  }, [username]);

  useEffect(() => {
    let active = true;
    const init = async () => {
      try {
        const [vehRes, assignRes, telemRes] = await Promise.allSettled([
          api.getVehicles(),
          api.getAssignedVehicle(username),
          api.getGeoTelemetry(username, 24)
        ]);

        if (!active) return;
        if (vehRes.status === 'fulfilled' && vehRes.value?.vehicles) {
          setVehicles(vehRes.value.vehicles);
        }
        if (assignRes.status === 'fulfilled' && assignRes.value) {
          setAssignedVehicleId(assignRes.value.vehicle_id || null);
        }
        if (telemRes.status === 'fulfilled' && telemRes.value) {
          setTelemetry(telemRes.value);
        }
      } catch (err) {
        console.error('Failed to load location/vehicle data:', err);
      }
    };
    void init();
    return () => {
      active = false;
    };
  }, [username]);

  const handleToggleTracking = () => {
    trigger('light');
    if (isTracking) {
      stopTracking();
    } else {
      startTracking();
    }
  };

  const handleSelectVehicle = async (vehicleId: string) => {
    trigger('light');
    const newId = vehicleId === 'none' ? null : vehicleId;
    try {
      await api.assignVehicle(username, newId);
      setAssignedVehicleId(newId);
      toast.success(newId ? 'Designated vehicle updated' : 'No vehicle designated');
      // Refresh telemetry so cost calculations update with newly selected vehicle
      const refreshed = await api.getGeoTelemetry(username, 24);
      setTelemetry(refreshed);
    } catch (err) {
      console.error('Failed to assign vehicle:', err);
      toast.error('Failed to update vehicle designation');
    }
  };

  const handleSaveVehicle = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newVehicleName.trim()) {
      toast.error('Vehicle name is required');
      return;
    }
    const mpgNum = parseFloat(newVehicleMpg);
    if (isNaN(mpgNum) || mpgNum <= 0) {
      toast.error('Please enter a valid MPG (greater than 0)');
      return;
    }
    const costNum = parseFloat(newVehicleCost);
    if (isNaN(costNum) || costNum <= 0) {
      toast.error('Please enter a valid fuel cost (greater than $0)');
      return;
    }

    try {
      setIsSavingVehicle(true);
      const vehicleId = newVehicleName.toLowerCase().replace(/[^a-z0-9]+/g, '_');
      await api.saveVehicle({
        id: vehicleId,
        name: newVehicleName.trim(),
        mpg: mpgNum,
        cost_per_gallon: costNum,
        fuel_type: newVehicleFuelType
      });

      toast.success(`Added ${newVehicleName.trim()}`);
      setNewVehicleName('');
      setNewVehicleMpg('');
      setNewVehicleCost('');
      setNewVehicleFuelType('gasoline');
      setShowAddForm(false);

      // If user had no vehicle assigned, assign this one automatically
      if (!assignedVehicleId) {
        await api.assignVehicle(username, vehicleId);
        setAssignedVehicleId(vehicleId);
      }

      await refreshData();
    } catch (err) {
      console.error('Failed to save vehicle:', err);
      toast.error('Could not save vehicle');
    } finally {
      setIsSavingVehicle(false);
    }
  };

  const handleDeleteVehicle = async (vehicleId: string, vehicleName: string) => {
    trigger('medium');
    try {
      await api.deleteVehicle(vehicleId);
      toast.success(`Removed ${vehicleName}`);
      if (assignedVehicleId === vehicleId) {
        setAssignedVehicleId(null);
      }
      await refreshData();
    } catch (err) {
      console.error('Failed to delete vehicle:', err);
      toast.error('Could not delete vehicle');
    }
  };

  // --- Vehicle Lookup Handlers ---
  const loadLookupYears = async () => {
    try {
      const data = await api.getVehicleLookupYears();
      const items = Array.isArray(data.menuItem) ? data.menuItem : data.menuItem ? [data.menuItem] : [];
      setLookupYears([...items].reverse());
    } catch {
      console.error('Failed to load vehicle years');
    }
  };

  const handleYearChange = async (year: string) => {
    setLookupYear(year);
    setLookupMake('');
    setLookupModel('');
    setLookupOption('');
    setLookupMakes([]);
    setLookupModels([]);
    setLookupOptions([]);
    if (!year) return;
    try {
      setIsLoadingLookup(true);
      const data = await api.getVehicleLookupMakes(parseInt(year));
      const items = Array.isArray(data.menuItem) ? data.menuItem : data.menuItem ? [data.menuItem] : [];
      setLookupMakes(items);
    } catch {
      console.error('Failed to load makes');
    } finally {
      setIsLoadingLookup(false);
    }
  };

  const handleMakeChange = async (make: string) => {
    setLookupMake(make);
    setLookupModel('');
    setLookupOption('');
    setLookupModels([]);
    setLookupOptions([]);
    if (!make || !lookupYear) return;
    try {
      setIsLoadingLookup(true);
      const data = await api.getVehicleLookupModels(parseInt(lookupYear), make);
      const items = Array.isArray(data.menuItem) ? data.menuItem : data.menuItem ? [data.menuItem] : [];
      setLookupModels(items);
    } catch {
      console.error('Failed to load models');
    } finally {
      setIsLoadingLookup(false);
    }
  };

  const handleModelChange = async (model: string) => {
    setLookupModel(model);
    setLookupOption('');
    setLookupOptions([]);
    if (!model || !lookupMake || !lookupYear) return;
    try {
      setIsLoadingLookup(true);
      const data = await api.getVehicleLookupOptions(parseInt(lookupYear), lookupMake, model);
      const raw = data.menuItem;
      const items = Array.isArray(raw) ? raw : raw ? [raw] : [];
      setLookupOptions(items as Array<{ text: string; value: string }>);
      if (items.length === 1) {
        handleOptionChange((items[0] as { text: string; value: string }).value);
      }
    } catch {
      console.error('Failed to load options');
    } finally {
      setIsLoadingLookup(false);
    }
  };

  const handleOptionChange = async (optionId: string) => {
    setLookupOption(optionId);
    if (!optionId) return;
    try {
      setIsLoadingLookup(true);
      const detail = await api.getVehicleLookupDetail(optionId);
      setNewVehicleName(`${detail.year} ${detail.make} ${detail.model}`);
      if (detail.comb08) setNewVehicleMpg(String(detail.comb08));
      const ft = (detail.fuelType1 || '').toLowerCase();
      if (ft.includes('diesel')) setNewVehicleFuelType('diesel');
      else if (ft.includes('electri')) setNewVehicleFuelType('electric');
      else if (ft.includes('hybrid') || ft.includes('e85')) setNewVehicleFuelType('hybrid');
      else setNewVehicleFuelType('gasoline');
    } catch {
      console.error('Failed to load vehicle detail');
    } finally {
      setIsLoadingLookup(false);
    }
  };

  const handleFetchFuelPrice = async () => {
    const loc = fuelPriceZip.trim();
    if (!loc) {
      toast.error('Enter a ZIP code or city to look up fuel prices');
      return;
    }
    try {
      setIsLoadingFuelPrice(true);
      const data = await api.getFuelPrices(loc);
      setLastFetchedPrices(data.prices);
      const fuelMap: Record<string, string> = {
        gasoline: 'regular', diesel: 'diesel', hybrid: 'regular', electric: 'regular',
      };
      const priceKey = fuelMap[newVehicleFuelType] || 'regular';
      const price = data.prices[priceKey as keyof typeof data.prices];
      if (price != null) {
        setNewVehicleCost(price.toFixed(2));
      }
      setFuelPriceSource(data.source || data.location || '');
      toast.success(`Fuel price loaded from ${data.source || 'local average'}`);
    } catch {
      toast.error('Could not fetch fuel prices for that location');
    } finally {
      setIsLoadingFuelPrice(false);
    }
  };

  const currentVehicle = vehicles.find((v) => v.id === assignedVehicleId);
  const liveSpeedMph = typeof speed === 'number' && !isNaN(speed) ? (speed * 2.237).toFixed(1) : '0';
  const isTransit = interval === 'transit' || telemetry?.is_moving;

  return (
    <div className="space-y-4">
      {/* 1. Background GPS Switch */}
      <div className="glass-panel rounded-2xl p-4 space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className={`p-2.5 rounded-xl ${isTracking ? 'bg-emerald-500/20 text-emerald-400' : 'bg-slate-700/40 text-slate-400'}`}>
              <Satellite size={20} />
            </div>
            <div>
              <p className="text-white text-sm font-semibold">Background Location Tracking</p>
              <p className="text-xs text-slate-400">
                {isTracking
                  ? isTransit
                    ? 'Active road tracking (sync every 30s)'
                    : 'Stationary low-power mode (geofence locked)'
                  : 'Location reporting paused'}
              </p>
            </div>
          </div>

          <button
            onClick={handleToggleTracking}
            aria-label="Toggle location tracking"
            className={`w-12 h-7 rounded-full relative transition-colors ${
              isTracking ? 'bg-emerald-500' : 'bg-slate-600'
            }`}
          >
            <div
              className={`absolute top-1 w-5 h-5 rounded-full bg-white shadow-md transition-transform ${
                isTracking ? 'translate-x-6' : 'translate-x-1'
              }`}
            />
          </button>
        </div>

        {isTracking && (
          <div className="flex items-center justify-between pt-1 text-xs text-slate-400 border-t border-white/5">
            <div className="flex items-center gap-1.5">
              <MapPin size={13} className="text-purple-400" />
              <span>
                {typeof latitude === 'number' && typeof longitude === 'number'
                  ? `${latitude.toFixed(4)}, ${longitude.toFixed(4)}`
                  : 'Acquiring GPS...'}
              </span>
              {typeof accuracy === 'number' && <span className="text-slate-500 font-mono">±{accuracy.toFixed(0)}m</span>}
            </div>

            <button
              onClick={() => {
                trigger('light');
                setShowRawDetails(!showRawDetails);
              }}
              className="text-xs text-purple-400 hover:text-purple-300 font-medium"
            >
              {showRawDetails ? 'Hide GPS Debug' : 'GPS Debug'}
            </button>
          </div>
        )}

        {showRawDetails && isTracking && (
          <div className="glass-card p-3 space-y-1.5 text-xs font-mono rounded-xl bg-slate-900/50">
            <div className="flex justify-between">
              <span className="text-slate-400">Latitude</span>
              <span className="text-slate-200">{typeof latitude === 'number' ? latitude.toFixed(6) : '—'}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-400">Longitude</span>
              <span className="text-slate-200">{typeof longitude === 'number' ? longitude.toFixed(6) : '—'}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-400">Speed (Live)</span>
              <span className="text-slate-200">{liveSpeedMph} mph</span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-400">Accuracy</span>
              <span className="text-slate-200">{typeof accuracy === 'number' ? `${accuracy.toFixed(1)}m` : '—'}</span>
            </div>
          </div>
        )}

        {error && (
          <div className="flex items-center gap-2 p-2.5 rounded-xl bg-red-500/10 border border-red-500/20 text-xs text-red-400">
            <AlertCircle size={14} />
            <span>{error}</span>
          </div>
        )}
      </div>

      {/* 2. Live Life360 & Home Assistant Presence */}
      <div className="glass-panel rounded-2xl p-4 space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Navigation size={18} className="text-cyan-400" />
            <h3 className="text-sm font-semibold text-white">Live Presence & Telemetry</h3>
          </div>
          <button
            onClick={() => {
              trigger('light');
              refreshData();
            }}
            disabled={loadingTelemetry}
            className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-white/10 transition-colors"
            title="Refresh location telemetry"
          >
            <RefreshCw size={14} className={loadingTelemetry ? 'animate-spin text-cyan-400' : ''} />
          </button>
        </div>

        {/* Status Badges Grid */}
        <div className="grid grid-cols-2 gap-2.5">
          {/* Current Zone */}
          <div className="glass-card p-3 rounded-xl bg-white/5 border border-white/5">
            <div className="flex items-center gap-2 text-slate-400 text-xs mb-1">
              <MapPin size={13} className="text-purple-400" />
              <span>Current Zone</span>
            </div>
            <p className="text-white text-sm font-semibold truncate">
              {telemetry?.current_zone || (telemetry?.is_moving ? 'In Transit' : 'Away')}
            </p>
            {telemetry?.closest_zone && !telemetry.current_zone && (
              <p className="text-[11px] text-slate-400 truncate mt-0.5">
                Near {telemetry.closest_zone} ({typeof telemetry.closest_zone_distance_miles === 'number' ? telemetry.closest_zone_distance_miles.toFixed(1) : '0.0'} mi)
              </p>
            )}
          </div>

          {/* Dwell / Motion Status */}
          <div className="glass-card p-3 rounded-xl bg-white/5 border border-white/5">
            <div className="flex items-center gap-2 text-slate-400 text-xs mb-1">
              {telemetry?.is_moving ? (
                <Zap size={13} className="text-amber-400" />
              ) : (
                <Clock size={13} className="text-emerald-400" />
              )}
              <span>{telemetry?.is_moving ? 'Motion' : 'Dwell Time'}</span>
            </div>
            <p className="text-white text-sm font-semibold truncate">
              {telemetry?.is_moving
                ? `${typeof telemetry?.current_speed_mph === 'number' ? telemetry.current_speed_mph.toFixed(0) : '0'} mph`
                : telemetry?.dwell_time_formatted || 'Stationary'}
            </p>
            <p className="text-[11px] text-slate-400 truncate mt-0.5">
              {telemetry?.is_moving ? 'Traveling' : `Stationary for ${telemetry?.dwell_time_formatted || '0m'}`}
            </p>
          </div>

          {/* Top Speed Today */}
          <div className="glass-card p-3 rounded-xl bg-white/5 border border-white/5">
            <div className="flex items-center gap-2 text-slate-400 text-xs mb-1">
              <Gauge size={13} className="text-cyan-400" />
              <span>Top Speed Today</span>
            </div>
            <p className="text-white text-sm font-semibold">
              {typeof telemetry?.top_speed_mph === 'number' ? `${telemetry.top_speed_mph.toFixed(1)} mph` : '0.0 mph'}
            </p>
          </div>

          {/* Distance Traveled Today */}
          <div className="glass-card p-3 rounded-xl bg-white/5 border border-white/5">
            <div className="flex items-center gap-2 text-slate-400 text-xs mb-1">
              <Navigation size={13} className="text-indigo-400" />
              <span>Traveled Today</span>
            </div>
            <p className="text-white text-sm font-semibold">
              {typeof telemetry?.distance_traveled_miles === 'number' ? `${telemetry.distance_traveled_miles.toFixed(1)} mi` : '0.0 mi'}
            </p>
          </div>
        </div>

        {/* Frequented Locations */}
        {telemetry?.frequented_locations && telemetry.frequented_locations.length > 0 && (
          <div className="pt-2 border-t border-white/5">
            <p className="text-xs text-slate-400 mb-1.5 font-medium">Frequented Locations Today</p>
            <div className="flex flex-wrap gap-1.5">
              {telemetry.frequented_locations.map((loc, idx) => (
                <span
                  key={idx}
                  className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-white/5 text-[11px] text-slate-300 border border-white/5"
                >
                  <MapPin size={11} className="text-purple-400" />
                  <span className="font-medium">{loc.name}</span>
                  <span className="text-slate-500">({loc.dwell_formatted})</span>
                </span>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* 3. Vehicle Designation & Travel Cost */}
      <div className="glass-panel rounded-2xl p-4 space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Car size={18} className="text-amber-400" />
            <h3 className="text-sm font-semibold text-white">Designated Vehicle</h3>
          </div>
          <button
            onClick={() => {
              trigger('light');
              setShowFleetManager(!showFleetManager);
            }}
            className="flex items-center gap-1 text-xs text-purple-400 hover:text-purple-300 font-medium"
          >
            <span>{showFleetManager ? 'Close Vehicles' : 'Manage Vehicles'}</span>
            {showFleetManager ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          </button>
        </div>

        {/* Selector */}
        <div>
          <label className="block text-xs text-slate-400 mb-1.5 font-medium">Active Traveling Vehicle</label>
          <select
            value={assignedVehicleId || 'none'}
            onChange={(e) => handleSelectVehicle(e.target.value)}
            className="w-full bg-slate-800/80 border border-slate-700/80 text-white rounded-xl px-3 py-2 text-sm focus:outline-none focus:border-purple-500"
          >
            <option value="none">None / Passenger / Walking</option>
            {vehicles.map((v) => (
              <option key={v.id} value={v.id}>
                {v.name} ({v.mpg} MPG · ${typeof v.cost_per_gallon === 'number' ? v.cost_per_gallon.toFixed(2) : '0.00'}/{v.fuel_type === 'electric' ? 'kWh' : 'gal'})
              </option>
            ))}
          </select>
        </div>

        {/* Vehicle Cost & Trip Calculations */}
        {currentVehicle ? (
          <div className="glass-card p-3.5 rounded-xl bg-gradient-to-r from-amber-500/10 to-purple-500/10 border border-amber-500/20 space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <ShieldCheck size={16} className="text-amber-400" />
                <span className="text-xs font-semibold text-white">{currentVehicle.name}</span>
              </div>
              <span className="text-xs text-slate-400 capitalize">
                {currentVehicle.fuel_type} · {currentVehicle.mpg} MPG
              </span>
            </div>

            <div className="grid grid-cols-3 gap-2 pt-1 border-t border-white/10 text-center">
              <div>
                <p className="text-[10px] text-slate-400">Distance</p>
                <p className="text-xs font-bold text-white">
                  {typeof telemetry?.distance_traveled_miles === 'number' ? `${telemetry.distance_traveled_miles.toFixed(1)} mi` : '0.0 mi'}
                </p>
              </div>
              <div>
                <p className="text-[10px] text-slate-400">Fuel Used</p>
                <p className="text-xs font-bold text-white">
                  {typeof telemetry?.vehicle?.gallons_used === 'number'
                    ? `${telemetry.vehicle.gallons_used.toFixed(2)} ${currentVehicle.fuel_type === 'electric' ? 'kWh' : 'gal'}`
                    : '0.00 gal'}
                </p>
              </div>
              <div>
                <p className="text-[10px] text-slate-400">Est. Trip Cost</p>
                <p className="text-xs font-bold text-emerald-400">
                  {typeof telemetry?.vehicle?.estimated_cost_usd === 'number'
                    ? `$${telemetry.vehicle.estimated_cost_usd.toFixed(2)}`
                    : '$0.00'}
                </p>
              </div>
            </div>
          </div>
        ) : (
          <div className="p-3 rounded-xl bg-white/5 border border-white/5 text-xs text-slate-400 space-y-1">
            <p className="font-medium text-slate-300">No vehicle designated</p>
            <p className="text-[11px] leading-relaxed">
              Designate a vehicle to calculate your MPG usage, fuel consumption, and travel costs in real-time as you travel.
            </p>
          </div>
        )}

        {/* 4. Fleet Management Section (Expandable) */}
        {showFleetManager && (
          <div className="pt-3 border-t border-white/10 space-y-3">
            <div className="flex items-center justify-between">
              <h4 className="text-xs font-semibold text-slate-300 uppercase tracking-wider">Your Vehicles</h4>
              <button
                onClick={() => {
                  trigger('light');
                  setShowAddForm(!showAddForm);
                }}
                className="flex items-center gap-1 px-2.5 py-1 rounded-lg bg-purple-600 hover:bg-purple-500 text-white text-xs font-medium transition-colors"
              >
                <Plus size={13} />
                <span>Add Vehicle</span>
              </button>
            </div>

            {/* Add Vehicle Form */}
            {showAddForm && (
              <form onSubmit={handleSaveVehicle} className="glass-card p-3 rounded-xl bg-slate-900/60 border border-purple-500/30 space-y-3">
                <p className="text-xs font-semibold text-white">New Vehicle Profile</p>

                {/* Vehicle Lookup Section */}
                <div className="space-y-2 p-2.5 rounded-lg bg-slate-800/50 border border-slate-700/50">
                  <div className="flex items-center justify-between">
                    <p className="text-[11px] font-medium text-purple-300 flex items-center gap-1">
                      <span>🔍</span> Look Up Vehicle (auto-fills MPG &amp; fuel type)
                    </p>
                    <span className="text-[10px] text-slate-400">Class 2b/3 (F-250/2500) enter below</span>
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <select
                      value={lookupYear}
                      onChange={(e) => handleYearChange(e.target.value)}
                      onFocus={() => lookupYears.length === 0 && loadLookupYears()}
                      className="bg-slate-800 border border-slate-700 text-white rounded-lg px-2 py-1.5 text-xs focus:outline-none focus:border-purple-500"
                    >
                      <option value="">Year...</option>
                      {lookupYears.map((y) => (
                        <option key={y.value} value={y.value}>{y.text}</option>
                      ))}
                    </select>
                    <select
                      value={lookupMake}
                      onChange={(e) => handleMakeChange(e.target.value)}
                      disabled={!lookupYear || lookupMakes.length === 0}
                      className="bg-slate-800 border border-slate-700 text-white rounded-lg px-2 py-1.5 text-xs focus:outline-none focus:border-purple-500 disabled:opacity-40"
                    >
                      <option value="">Make...</option>
                      {lookupMakes.map((m) => (
                        <option key={m.value} value={m.value}>{m.text}</option>
                      ))}
                    </select>
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <select
                      value={lookupModel}
                      onChange={(e) => handleModelChange(e.target.value)}
                      disabled={!lookupMake || lookupModels.length === 0}
                      className="bg-slate-800 border border-slate-700 text-white rounded-lg px-2 py-1.5 text-xs focus:outline-none focus:border-purple-500 disabled:opacity-40"
                    >
                      <option value="">Model...</option>
                      {lookupModels.map((m) => (
                        <option key={m.value} value={m.value}>{m.text}</option>
                      ))}
                    </select>
                    <select
                      value={lookupOption}
                      onChange={(e) => handleOptionChange(e.target.value)}
                      disabled={!lookupModel || lookupOptions.length === 0}
                      className="bg-slate-800 border border-slate-700 text-white rounded-lg px-2 py-1.5 text-xs focus:outline-none focus:border-purple-500 disabled:opacity-40"
                    >
                      <option value="">Trim / Engine...</option>
                      {lookupOptions.map((o) => (
                        <option key={o.value} value={o.value}>{o.text}</option>
                      ))}
                    </select>
                  </div>
                  {isLoadingLookup && (
                    <p className="text-[10px] text-purple-300 animate-pulse">Loading...</p>
                  )}
                </div>

                {/* Manual / Auto-filled Fields */}
                <div>
                  <label className="block text-[11px] text-slate-400 mb-1">Vehicle Name / Model</label>
                  <input
                    type="text"
                    placeholder="e.g. 2022 Ford F-150"
                    value={newVehicleName}
                    onChange={(e) => setNewVehicleName(e.target.value)}
                    className="w-full bg-slate-800 border border-slate-700 text-white rounded-lg px-2.5 py-1.5 text-xs focus:outline-none focus:border-purple-500"
                    required
                  />
                </div>

                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="block text-[11px] text-slate-400 mb-1">Fuel Efficiency (MPG)</label>
                    <input
                      type="number"
                      step="0.1"
                      placeholder="e.g. 18.5"
                      value={newVehicleMpg}
                      onChange={(e) => setNewVehicleMpg(e.target.value)}
                      className="w-full bg-slate-800 border border-slate-700 text-white rounded-lg px-2.5 py-1.5 text-xs focus:outline-none focus:border-purple-500"
                      required
                    />
                  </div>
                  <div>
                    <label className="block text-[11px] text-slate-400 mb-1">Fuel Type</label>
                    <select
                      value={newVehicleFuelType}
                      onChange={(e) => {
                        const ft = e.target.value;
                        setNewVehicleFuelType(ft);
                        if (lastFetchedPrices) {
                          const fuelMap: Record<string, string> = {
                            gasoline: 'regular', diesel: 'diesel', hybrid: 'regular', electric: 'regular',
                          };
                          const key = fuelMap[ft] || 'regular';
                          const p = lastFetchedPrices[key as keyof typeof lastFetchedPrices];
                          if (p != null) {
                            setNewVehicleCost(p.toFixed(2));
                          }
                        }
                      }}
                      className="w-full bg-slate-800 border border-slate-700 text-white rounded-lg px-2.5 py-1.5 text-xs focus:outline-none focus:border-purple-500"
                    >
                      <option value="gasoline">Gasoline</option>
                      <option value="diesel">Diesel</option>
                      <option value="hybrid">Hybrid</option>
                      <option value="electric">Electric</option>
                    </select>
                  </div>
                </div>

                {/* Fuel Price Lookup */}
                <div className="space-y-1.5">
                  <label className="block text-[11px] text-slate-400">
                    Cost per {newVehicleFuelType === 'electric' ? 'kWh' : 'Gallon'} ($)
                  </label>
                  <div className="flex gap-2">
                    <input
                      type="number"
                      step="0.01"
                      placeholder="e.g. 3.49"
                      value={newVehicleCost}
                      onChange={(e) => setNewVehicleCost(e.target.value)}
                      className="flex-1 bg-slate-800 border border-slate-700 text-white rounded-lg px-2.5 py-1.5 text-xs focus:outline-none focus:border-purple-500"
                      required
                    />
                    <div className="flex gap-1">
                      <input
                        type="text"
                        placeholder="ZIP code"
                        value={fuelPriceZip}
                        onChange={(e) => setFuelPriceZip(e.target.value)}
                        className="w-20 bg-slate-800 border border-slate-700 text-white rounded-lg px-2 py-1.5 text-xs focus:outline-none focus:border-purple-500"
                      />
                      <button
                        type="button"
                        onClick={handleFetchFuelPrice}
                        disabled={isLoadingFuelPrice}
                        className="px-2 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-[10px] font-medium whitespace-nowrap disabled:opacity-50"
                      >
                        {isLoadingFuelPrice ? '...' : '⛽ Fetch'}
                      </button>
                    </div>
                  </div>
                  {fuelPriceSource && (
                    <p className="text-[10px] text-emerald-400/70 italic">
                      Auto-filled from {fuelPriceSource}
                    </p>
                  )}
                </div>

                <div className="flex items-center justify-end gap-2 pt-1">
                  <button
                    type="button"
                    onClick={() => setShowAddForm(false)}
                    className="px-3 py-1.5 rounded-lg text-xs text-slate-400 hover:text-white"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    disabled={isSavingVehicle}
                    className="px-3 py-1.5 rounded-lg bg-purple-600 hover:bg-purple-500 text-white text-xs font-semibold disabled:opacity-50"
                  >
                    {isSavingVehicle ? 'Saving...' : 'Save Vehicle'}
                  </button>
                </div>
              </form>
            )}

            {/* List of Vehicles */}
            {vehicles.length === 0 ? (
              <div className="text-center py-4 text-xs text-slate-400">
                No vehicles configured yet. Click "Add Vehicle" above to configure your car or truck.
              </div>
            ) : (
              <div className="space-y-2">
                {vehicles.map((v) => (
                  <div
                    key={v.id}
                    className={`flex items-center justify-between p-2.5 rounded-xl border transition-colors ${
                      assignedVehicleId === v.id
                        ? 'bg-purple-600/10 border-purple-500/30'
                        : 'bg-white/5 border-white/5'
                    }`}
                  >
                    <div className="flex items-center gap-2.5 min-w-0">
                      <div className="p-2 rounded-lg bg-white/5 text-amber-400">
                        <Car size={16} />
                      </div>
                      <div className="min-w-0">
                        <p className="text-xs font-semibold text-white truncate">{v.name}</p>
                        <p className="text-[11px] text-slate-400 truncate">
                          {v.mpg} MPG · ${typeof v.cost_per_gallon === 'number' ? v.cost_per_gallon.toFixed(2) : '0.00'}/{v.fuel_type === 'electric' ? 'kWh' : 'gal'} ·{' '}
                          <span className="capitalize">{v.fuel_type}</span>
                        </p>
                      </div>
                    </div>

                    <div className="flex items-center gap-1.5">
                      {assignedVehicleId === v.id ? (
                        <span className="text-[10px] uppercase font-bold text-purple-400 bg-purple-500/20 px-2 py-0.5 rounded">
                          Active
                        </span>
                      ) : (
                        <button
                          onClick={() => handleSelectVehicle(v.id)}
                          className="text-xs text-slate-400 hover:text-white px-2 py-1 rounded hover:bg-white/10"
                        >
                          Select
                        </button>
                      )}
                      <button
                        onClick={() => handleDeleteVehicle(v.id, v.name)}
                        className="p-1.5 text-slate-500 hover:text-red-400 hover:bg-red-500/10 rounded-lg transition-colors"
                        title="Delete vehicle"
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

export default LocationPanel;
