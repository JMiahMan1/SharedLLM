import { useState, useEffect, useCallback, useMemo } from 'react';
import { useAuth } from '../context/AuthContext';
import { useHaptics } from '../hooks/useHaptics';
import { api } from '../services/api';
import type { Trip, TripUpdatePayload } from '../types/api';
import Modal from '../components/ui/Modal';
import toast from 'react-hot-toast';
import {
  Users,
  Car,
  Fuel,
  Clock,
  RefreshCw,
  Edit2,
  Gauge,
  Battery,
  DollarSign,
  Calendar as CalendarIcon,
  MapPin,
  Lock,
  Route,
  Zap,
  CheckCircle2,
  Compass,
} from 'lucide-react';

interface VehicleOption {
  id: string;
  name: string;
  mpg: number;
  cost_per_gallon: number;
  fuel_type: string;
}

interface FamilyMemberStatus {
  id: string;
  name: string;
  zone: string;
  isMoving: boolean;
  speedMph: number;
  battery: number | null;
  dwellFormatted?: string;
  assignedVehicle?: string;
  lastUpdated?: string;
}

const Wander = () => {
  const { user } = useAuth();
  const { trigger } = useHaptics();

  const currentUsername = (user?.username || '').toLowerCase();

  const [trips, setTrips] = useState<Trip[]>([]);
  const [vehicles, setVehicles] = useState<VehicleOption[]>([]);
  const [familyMembers, setFamilyMembers] = useState<FamilyMemberStatus[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [filterTab, setFilterTab] = useState<'all' | 'mine'>('all');

  // Edit Trip Modal state
  const [editingTrip, setEditingTrip] = useState<Trip | null>(null);
  const [selectedVehicleId, setSelectedVehicleId] = useState<string>('');
  const [editVehicleName, setEditVehicleName] = useState<string>('');
  const [editFuelType, setEditFuelType] = useState<string>('gasoline');
  const [editMpg, setEditMpg] = useState<number | string>(25.0);
  const [editCostPerGallon, setEditCostPerGallon] = useState<number | string>(3.65);
  const [isSavingTrip, setIsSavingTrip] = useState(false);

  const fetchTripsAndTelemetry = useCallback(async () => {
    try {
      setIsRefreshing(true);
      const [tripsRes, vehRes, peopleRes] = await Promise.allSettled([
        api.getTrips(),
        api.getVehicles(),
        api.getGeoPeople(),
      ]);

      if (tripsRes.status === 'fulfilled' && tripsRes.value?.trips) {
        setTrips(tripsRes.value.trips);
      }

      if (vehRes.status === 'fulfilled' && vehRes.value?.vehicles) {
        setVehicles(vehRes.value.vehicles);
      }

      // Parse Family Members from HA People collection
      const members: FamilyMemberStatus[] = [];
      if (peopleRes.status === 'fulfilled' && peopleRes.value) {
        const geoJson = peopleRes.value as { features?: Array<{ properties?: Record<string, unknown> }> };
        const features = geoJson.features || [];
        for (const feat of features) {
          const props = feat.properties || {};
          const entityId = String(props.entity_id || '');
          if (entityId.startsWith('person.')) {
            const rawName = String(props.friendly_name || entityId.replace('person.', ''));
            const zone = String(props.state || 'Unknown');
            const speed = Number(props.speed || 0);
            const battery = props.battery != null ? Number(props.battery) : null;
            members.push({
              id: entityId,
              name: rawName,
              zone: zone.charAt(0).toUpperCase() + zone.slice(1),
              isMoving: speed > 1.0,
              speedMph: Math.round(speed * 0.621371), // Convert km/h or m/s if reported
              battery,
            });
          }
        }
      }

      // If no HA people entities were returned (or mocked locally), populate standard family members
      if (members.length === 0) {
        members.push(
          {
            id: 'person.jeremiah',
            name: 'Jeremiah',
            zone: 'Home',
            isMoving: false,
            speedMph: 0,
            battery: 88,
            assignedVehicle: '2011 Ford F-250 Super Duty (Diesel)',
          },
          {
            id: 'person.michele',
            name: 'Michele',
            zone: 'Home',
            isMoving: false,
            speedMph: 0,
            battery: 92,
            assignedVehicle: '2012 Chevrolet Equinox (Gasoline)',
          },
          {
            id: 'person.summers',
            name: 'Summers',
            zone: 'Home',
            isMoving: false,
            speedMph: 0,
            battery: 75,
            assignedVehicle: 'Family Pool',
          },
        );
      }

      setFamilyMembers(members);
    } catch (err) {
      console.error('Failed to load trips or family data:', err);
      toast.error('Failed to load trip telemetry');
    } finally {
      setIsLoading(false);
      setIsRefreshing(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    const init = async () => {
      try {
        await fetchTripsAndTelemetry();
      } catch (err) {
        if (active) {
          console.error('Initial telemetry load failed:', err);
        }
      }
    };
    void init();
    return () => {
      active = false;
    };
  }, [fetchTripsAndTelemetry]);

  // Check if current user is owner of a trip
  const isTripOwner = useCallback(
    (trip: Trip): boolean => {
      if (!currentUsername) return false;
      const tripOwner = (trip.user_id || '').split('.').pop()?.toLowerCase() || '';
      return tripOwner === currentUsername || currentUsername === 'admin';
    },
    [currentUsername]
  );

  // Filter trips
  const displayedTrips = useMemo(() => {
    if (filterTab === 'mine') {
      return trips.filter((t) => {
        const owner = (t.user_id || '').split('.').pop()?.toLowerCase() || '';
        return owner === currentUsername;
      });
    }
    return trips;
  }, [trips, filterTab, currentUsername]);

  // Aggregate stats
  const stats = useMemo(() => {
    let totalMiles = 0;
    let totalCost = 0;
    let totalFuel = 0;
    let sharedCount = 0;

    for (const t of displayedTrips) {
      totalMiles += Number(t.distance_miles || 0);
      totalCost += Number(t.trip_cost_usd || 0);
      totalFuel += Number(t.fuel_used_gal || 0);
      if (t.is_shared) sharedCount += 1;
    }

    return {
      miles: totalMiles.toFixed(1),
      cost: totalCost.toFixed(2),
      fuel: totalFuel.toFixed(1),
      count: displayedTrips.length,
      sharedCount,
    };
  }, [displayedTrips]);

  // Open Edit Modal
  const handleOpenEdit = (trip: Trip) => {
    trigger('light');
    if (!isTripOwner(trip)) {
      toast.error(`Only ${trip.user_name || 'the trip owner'} can edit this trip.`);
      return;
    }

    setEditingTrip(trip);
    setSelectedVehicleId(trip.vehicle_id || '');
    setEditVehicleName(trip.vehicle_name || '');
    setEditFuelType(trip.fuel_type || 'gasoline');
    setEditMpg(trip.mpg || 25.0);
    setEditCostPerGallon(trip.cost_per_gallon || 3.65);
  };

  // When user selects a vehicle from the dropdown, populate fields
  const handleVehicleSelect = (vehId: string) => {
    setSelectedVehicleId(vehId);
    const found = vehicles.find((v) => v.id === vehId);
    if (found) {
      setEditVehicleName(found.name);
      setEditFuelType(found.fuel_type || 'gasoline');
      setEditMpg(found.mpg);
      setEditCostPerGallon(found.cost_per_gallon);
    }
  };

  // Save Trip updates
  const handleSaveTrip = async () => {
    if (!editingTrip) return;
    trigger('medium');
    setIsSavingTrip(true);
    try {
      const payload: TripUpdatePayload = {
        vehicle_id: selectedVehicleId || undefined,
        vehicle_name: editVehicleName.trim() || undefined,
        fuel_type: editFuelType,
        mpg: Number(editMpg) > 0 ? Number(editMpg) : 25.0,
        cost_per_gallon: Number(editCostPerGallon) >= 0 ? Number(editCostPerGallon) : 3.65,
      };

      const updated = await api.updateTrip(editingTrip.id, payload);
      toast.success('Trip vehicle & fuel details updated!');

      // Update in local state
      setTrips((prev) => prev.map((t) => (t.id === updated.id ? { ...t, ...updated } : t)));
      setEditingTrip(null);
    } catch (err: unknown) {
      const errorMsg =
        err && typeof err === 'object' && 'response' in err
          ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail
          : 'Failed to update trip.';
      toast.error(errorMsg || 'Failed to update trip.');
    } finally {
      setIsSavingTrip(false);
    }
  };

  // Seed sample trips for testing
  const handleSeedTrips = async () => {
    trigger('medium');
    try {
      setIsRefreshing(true);
      await api.seedTrips();
      toast.success('Sample trips generated!');
      await fetchTripsAndTelemetry();
    } catch {
      toast.error('Failed to seed trips.');
    } finally {
      setIsRefreshing(false);
    }
  };

  // Format timestamp helper
  const formatTimeRange = (start: number, end: number) => {
    const sDate = new Date(start * 1000);
    const eDate = new Date(end * 1000);
    const sameDay = sDate.toDateString() === eDate.toDateString();

    const timeStr = sDate.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    const endTimeStr = eDate.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    const dateStr = sDate.toLocaleDateString([], { month: 'short', day: 'numeric' });

    if (sameDay) {
      return `${dateStr}, ${timeStr} – ${endTimeStr}`;
    }
    return `${dateStr} ${timeStr} – ${eDate.toLocaleDateString([], { month: 'short', day: 'numeric' })} ${endTimeStr}`;
  };

  const formatDuration = (seconds: number) => {
    const mins = Math.round(seconds / 60);
    if (mins < 60) return `${mins}m`;
    const hrs = Math.floor(mins / 60);
    const remMins = mins % 60;
    return `${hrs}h ${remMins}m`;
  };

  // Recalculated values in edit modal preview
  const previewFuelUsed = useMemo(() => {
    if (!editingTrip) return 0;
    const mpgNum = Number(editMpg);
    if (!mpgNum || mpgNum <= 0) return 0;
    return Number((editingTrip.distance_miles / mpgNum).toFixed(2));
  }, [editingTrip, editMpg]);

  const previewCost = useMemo(() => {
    const costNum = Number(editCostPerGallon);
    if (!costNum || costNum < 0) return 0;
    return Number((previewFuelUsed * costNum).toFixed(2));
  }, [previewFuelUsed, editCostPerGallon]);

  return (
    <div className="space-y-6 max-w-7xl mx-auto pb-12">
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 glass-panel p-6 rounded-2xl border border-white/10 shadow-xl">
        <div className="space-y-1">
          <div className="flex items-center gap-3">
            <div className="p-2.5 rounded-xl bg-purple-500/20 text-purple-400 border border-purple-500/30">
              <Compass size={26} />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-2xl sm:text-3xl font-bold bg-gradient-to-r from-purple-300 via-pink-300 to-indigo-300 bg-clip-text text-transparent">
                  Wander
                </h1>
                <span className="text-[10px] uppercase font-bold tracking-wider px-2 py-0.5 rounded-full bg-purple-500/20 text-purple-300 border border-purple-500/30">
                  Cherith Telemetry
                </span>
              </div>
              <p className="text-xs sm:text-sm text-slate-400">
                Automated trip recording (&gt;10 MPH), shared rides, and fuel telemetry — Inspired by Elijah&apos;s journey in the wilderness (1 Kings 17:3–6).
              </p>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={fetchTripsAndTelemetry}
            disabled={isRefreshing}
            className="glass-button flex items-center gap-2 px-3 py-2 text-xs font-semibold text-slate-300 hover:text-white rounded-xl"
            title="Refresh Trips & Telemetry"
          >
            <RefreshCw size={14} className={isRefreshing ? 'animate-spin text-purple-400' : ''} />
            <span>Refresh</span>
          </button>
          <button
            onClick={handleSeedTrips}
            disabled={isRefreshing}
            className="glass-button flex items-center gap-2 px-3 py-2 text-xs font-semibold text-purple-300 hover:text-purple-200 border-purple-500/30 rounded-xl"
            title="Seed Initial Sample Trips"
          >
            <Sparkles size={14} className="text-purple-400" />
            <span>Sample Data</span>
          </button>
        </div>
      </div>

      {/* Live Family Circle Members Section */}
      <div className="space-y-3">
        <div className="flex items-center justify-between px-1">
          <h2 className="text-sm font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-2">
            <Users size={16} className="text-purple-400" />
            Family Presence
          </h2>
          <span className="text-xs text-slate-500">Live HA / GPS status</span>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {familyMembers.map((member) => (
            <div
              key={member.id}
              className="glass-card p-4 rounded-2xl border border-white/5 hover:border-purple-500/30 transition-all flex flex-col justify-between gap-3 shadow-lg"
            >
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-full bg-gradient-to-tr from-purple-600 to-indigo-600 flex items-center justify-center font-bold text-white shadow-md">
                    {member.name.charAt(0).toUpperCase()}
                  </div>
                  <div>
                    <h3 className="font-semibold text-slate-100 flex items-center gap-2">
                      {member.name}
                      {member.name.toLowerCase() === currentUsername && (
                        <span className="text-[10px] uppercase font-bold px-1.5 py-0.5 rounded bg-purple-500/20 text-purple-300 border border-purple-500/30">
                          You
                        </span>
                      )}
                    </h3>
                    <div className="flex items-center gap-1.5 text-xs text-slate-400">
                      <MapPin size={12} className="text-purple-400" />
                      <span>{member.zone}</span>
                    </div>
                  </div>
                </div>

                {member.battery !== null && (
                  <div className="flex items-center gap-1 text-xs text-slate-400 bg-slate-900/60 px-2 py-1 rounded-lg border border-white/5">
                    <Battery size={13} className={member.battery < 20 ? 'text-rose-400' : 'text-emerald-400'} />
                    <span>{member.battery}%</span>
                  </div>
                )}
              </div>

              <div className="flex items-center justify-between text-xs pt-2 border-t border-white/5 text-slate-400">
                <div className="flex items-center gap-1.5">
                  <Gauge size={13} className={member.isMoving ? 'text-emerald-400 animate-pulse' : 'text-slate-500'} />
                  <span>
                    {member.isMoving ? (
                      <span className="text-emerald-400 font-semibold">{member.speedMph} mph (In Motion)</span>
                    ) : (
                      'Stationary'
                    )}
                  </span>
                </div>
                {member.assignedVehicle && (
                  <div className="flex items-center gap-1 text-[11px] text-slate-400 truncate max-w-[180px]">
                    <Car size={12} className="text-indigo-400 shrink-0" />
                    <span className="truncate">{member.assignedVehicle}</span>
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Aggregate Statistics Ribbon */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div className="glass-panel p-4 rounded-xl border border-white/5 flex flex-col">
          <span className="text-[11px] text-slate-400 uppercase tracking-wider font-semibold">Total Trips</span>
          <span className="text-xl sm:text-2xl font-bold text-white mt-1">{stats.count}</span>
          <span className="text-[10px] text-slate-500 mt-0.5">{stats.sharedCount} shared rides</span>
        </div>

        <div className="glass-panel p-4 rounded-xl border border-white/5 flex flex-col">
          <span className="text-[11px] text-slate-400 uppercase tracking-wider font-semibold">Total Distance</span>
          <span className="text-xl sm:text-2xl font-bold text-indigo-400 mt-1">{stats.miles} mi</span>
          <span className="text-[10px] text-slate-500 mt-0.5">Immutable GPS distance</span>
        </div>

        <div className="glass-panel p-4 rounded-xl border border-white/5 flex flex-col">
          <span className="text-[11px] text-slate-400 uppercase tracking-wider font-semibold">Fuel Consumed</span>
          <span className="text-xl sm:text-2xl font-bold text-purple-400 mt-1">{stats.fuel} gal</span>
          <span className="text-[10px] text-slate-500 mt-0.5">Based on vehicle MPG</span>
        </div>

        <div className="glass-panel p-4 rounded-xl border border-white/5 flex flex-col">
          <span className="text-[11px] text-slate-400 uppercase tracking-wider font-semibold">Estimated Cost</span>
          <span className="text-xl sm:text-2xl font-bold text-emerald-400 mt-1">${stats.cost}</span>
          <span className="text-[10px] text-slate-500 mt-0.5">Gas / Diesel fuel price</span>
        </div>
      </div>

      {/* Trips Section Header with Filter Tabs */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pt-2">
        <div>
          <h2 className="text-lg font-bold text-slate-100 flex items-center gap-2">
            <Route size={20} className="text-purple-400" />
            Recorded Trips
          </h2>
          <p className="text-xs text-slate-400">
            Recorded automatically when vehicle speed exceeds 10 MPH. Vehicle & fuel editable by trip owner.
          </p>
        </div>

        {/* Tab Filters */}
        <div className="flex items-center p-1 bg-slate-900/80 rounded-xl border border-white/10 self-start sm:self-auto">
          <button
            onClick={() => {
              trigger('light');
              setFilterTab('all');
            }}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
              filterTab === 'all'
                ? 'bg-purple-600 text-white shadow-md'
                : 'text-slate-400 hover:text-white'
            }`}
          >
            All Wander Trips
          </button>
          <button
            onClick={() => {
              trigger('light');
              setFilterTab('mine');
            }}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
              filterTab === 'mine'
                ? 'bg-purple-600 text-white shadow-md'
                : 'text-slate-400 hover:text-white'
            }`}
          >
            My Trips ({currentUsername || 'Me'})
          </button>
        </div>
      </div>

      {/* Trips List */}
      {isLoading ? (
        <div className="glass-panel p-12 text-center rounded-2xl border border-white/5">
          <RefreshCw size={28} className="animate-spin text-purple-400 mx-auto mb-3" />
          <p className="text-sm text-slate-400">Loading trips and telemetry...</p>
        </div>
      ) : displayedTrips.length === 0 ? (
        <div className="glass-panel p-12 text-center rounded-2xl border border-white/5 space-y-3">
          <div className="p-3 bg-purple-500/10 text-purple-400 rounded-full w-fit mx-auto">
            <Car size={32} />
          </div>
          <h3 className="text-base font-semibold text-slate-200">No recorded trips found</h3>
          <p className="text-xs text-slate-400 max-w-md mx-auto">
            Trips are logged automatically whenever speed exceeds 10 MPH. You can also generate initial sample trips to inspect the shared trip features.
          </p>
          <button
            onClick={handleSeedTrips}
            className="glass-button px-4 py-2 text-xs font-medium text-purple-300 hover:text-white rounded-xl border-purple-500/30"
          >
            Generate Sample Trips
          </button>
        </div>
      ) : (
        <div className="space-y-4">
          {displayedTrips.map((trip) => {
            const canEdit = isTripOwner(trip);
            const isShared = trip.is_shared;

            return (
              <div
                key={trip.id}
                className={`glass-card p-5 rounded-2xl border transition-all relative overflow-hidden shadow-lg ${
                  isShared
                    ? 'border-purple-500/40 bg-gradient-to-r from-purple-950/20 via-slate-900/40 to-indigo-950/20'
                    : 'border-white/5 hover:border-white/10'
                }`}
              >
                {/* Top Badge Row */}
                <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
                  <div className="flex items-center gap-2">
                    {/* User Badge */}
                    <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-slate-800/80 text-xs font-semibold text-slate-200 border border-white/5">
                      <span className="w-2 h-2 rounded-full bg-purple-400" />
                      <span>{trip.user_name || trip.user_id}</span>
                    </div>

                    {/* Shared Ride Badge */}
                    {isShared && (
                      <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-purple-500/20 text-purple-300 border border-purple-500/40 text-xs font-semibold animate-pulse">
                        <Users size={12} />
                        <span>Shared Trip with {trip.shared_with?.join(', ') || 'Family'}</span>
                      </div>
                    )}

                    {/* Status Badge */}
                    {trip.status === 'in_progress' ? (
                      <span className="px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider bg-amber-500/20 text-amber-300 border border-amber-500/30 flex items-center gap-1">
                        <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-ping" />
                        In Progress
                      </span>
                    ) : (
                      <span className="px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 flex items-center gap-1">
                        <CheckCircle2 size={10} />
                        Completed
                      </span>
                    )}
                  </div>

                  {/* Date and Duration */}
                  <div className="flex items-center gap-2 text-xs text-slate-400">
                    <CalendarIcon size={13} className="text-slate-500" />
                    <span>{formatTimeRange(trip.start_time, trip.end_time)}</span>
                    <span className="text-slate-600">•</span>
                    <Clock size={13} className="text-slate-500" />
                    <span className="font-semibold text-slate-300">{formatDuration(trip.duration_seconds)}</span>
                  </div>
                </div>

                {/* Route Visualization */}
                <div className="grid grid-cols-1 md:grid-cols-12 gap-4 items-center bg-black/20 p-3.5 rounded-xl border border-white/5">
                  {/* Origin & Destination */}
                  <div className="md:col-span-6 flex items-center gap-3">
                    <div className="flex flex-col items-center">
                      <div className="w-3 h-3 rounded-full bg-emerald-400 shadow-sm shadow-emerald-400/50" />
                      <div className="w-0.5 h-6 bg-slate-700 my-0.5" />
                      <div className="w-3 h-3 rounded-full bg-rose-400 shadow-sm shadow-rose-400/50" />
                    </div>

                    <div className="flex-1 space-y-2">
                      <div>
                        <span className="text-[10px] text-slate-500 uppercase font-semibold">Start</span>
                        <p className="text-xs font-medium text-slate-200 truncate">
                          {trip.start_location?.zone ||
                            (trip.start_location?.lat
                              ? `${trip.start_location.lat.toFixed(4)}, ${trip.start_location.lon.toFixed(4)}`
                              : 'Starting Point')}
                        </p>
                      </div>
                      <div>
                        <span className="text-[10px] text-slate-500 uppercase font-semibold">Destination</span>
                        <p className="text-xs font-medium text-slate-200 truncate">
                          {trip.end_location?.zone ||
                            (trip.end_location?.lat
                              ? `${trip.end_location.lat.toFixed(4)}, ${trip.end_location.lon.toFixed(4)}`
                              : 'Destination')}
                        </p>
                      </div>
                    </div>
                  </div>

                  {/* Telemetry Metrics */}
                  <div className="md:col-span-6 grid grid-cols-3 gap-2 text-center pt-2 md:pt-0 border-t md:border-t-0 md:border-l border-white/5 md:pl-4">
                    <div className="flex flex-col items-center justify-center p-2 rounded-lg bg-white/[0.02]">
                      <span className="text-[10px] text-slate-400 flex items-center gap-1">
                        <Lock size={10} className="text-slate-500" title="GPS Distance is locked and immutable" />
                        Distance
                      </span>
                      <span className="text-sm font-bold text-white mt-0.5">{trip.distance_miles} mi</span>
                    </div>

                    <div className="flex flex-col items-center justify-center p-2 rounded-lg bg-white/[0.02]">
                      <span className="text-[10px] text-slate-400 flex items-center gap-1">
                        <Zap size={10} className="text-amber-400" />
                        Top Speed
                      </span>
                      <span className="text-sm font-bold text-amber-300 mt-0.5">{trip.top_speed_mph} mph</span>
                    </div>

                    <div className="flex flex-col items-center justify-center p-2 rounded-lg bg-white/[0.02]">
                      <span className="text-[10px] text-slate-400 flex items-center gap-1">
                        <DollarSign size={10} className="text-emerald-400" />
                        Trip Cost
                      </span>
                      <span className="text-sm font-bold text-emerald-400 mt-0.5">${trip.trip_cost_usd}</span>
                    </div>
                  </div>
                </div>

                {/* Vehicle & Fuel Details Row */}
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mt-3 pt-3 border-t border-white/5 text-xs">
                  <div className="flex flex-wrap items-center gap-3 text-slate-300">
                    {/* Vehicle Name */}
                    <div className="flex items-center gap-1.5 font-medium">
                      <Car size={14} className="text-purple-400 shrink-0" />
                      <span>{trip.vehicle_name || 'Assigned Vehicle'}</span>
                    </div>

                    {/* Fuel Type Badge */}
                    <span
                      className={`text-[10px] uppercase font-bold px-2 py-0.5 rounded border ${
                        (trip.fuel_type || '').toLowerCase() === 'diesel'
                          ? 'bg-amber-500/20 text-amber-300 border-amber-500/30'
                          : 'bg-indigo-500/20 text-indigo-300 border-indigo-500/30'
                      }`}
                    >
                      {trip.fuel_type || 'Gasoline'}
                    </span>

                    {/* MPG & Gas Price */}
                    <div className="flex items-center gap-2 text-slate-400">
                      <span className="flex items-center gap-1">
                        <Gauge size={12} className="text-slate-500" />
                        <span>{trip.mpg} MPG</span>
                      </span>
                      <span>•</span>
                      <span className="flex items-center gap-1">
                        <Fuel size={12} className="text-slate-500" />
                        <span>${trip.cost_per_gallon}/gal</span>
                      </span>
                      <span>•</span>
                      <span>{trip.fuel_used_gal} gal used</span>
                    </div>
                  </div>

                  {/* Edit Button (Only visible/enabled for owner) */}
                  <div>
                    {canEdit ? (
                      <button
                        onClick={() => handleOpenEdit(trip)}
                        className="glass-button flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-purple-300 hover:text-white border-purple-500/30 rounded-lg transition-all"
                      >
                        <Edit2 size={12} />
                        <span>Edit Vehicle & Fuel</span>
                      </button>
                    ) : (
                      <div
                        className="flex items-center gap-1.5 text-[11px] text-slate-500 px-2 py-1 bg-white/[0.02] rounded-lg border border-white/5 cursor-not-allowed"
                        title={`Only ${trip.user_name || 'the trip owner'} can edit this trip`}
                      >
                        <Lock size={11} className="text-slate-500" />
                        <span>Owner locked</span>
                      </div>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Edit Vehicle & Fuel Modal */}
      <Modal
        isOpen={Boolean(editingTrip)}
        onClose={() => setEditingTrip(null)}
        title="Edit Trip Vehicle & Fuel Info"
        size="lg"
      >
        {editingTrip && (
          <div className="space-y-5">
            {/* Explanatory Notice */}
            <div className="p-3.5 rounded-xl bg-purple-500/10 border border-purple-500/20 text-xs text-purple-200 space-y-1">
              <div className="flex items-center gap-2 font-semibold">
                <Lock size={14} className="text-purple-400" />
                <span>GPS Telemetry Locked</span>
              </div>
              <p className="text-slate-400">
                Route locations and distance ({editingTrip.distance_miles} miles) are verified telemetry and cannot be altered. You can change the vehicle, MPG, and fuel price to recalculate fuel usage and costs.
              </p>
            </div>

            {/* Quick Vehicle Selector */}
            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-1.5">
                <Car size={14} className="text-purple-400" />
                Choose Saved Vehicle
              </label>
              <select
                value={selectedVehicleId}
                onChange={(e) => handleVehicleSelect(e.target.value)}
                className="w-full bg-slate-900/90 border border-white/10 rounded-xl px-3.5 py-2.5 text-sm text-white focus:outline-none focus:border-purple-500"
              >
                <option value="">Custom Vehicle Override</option>
                {vehicles.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name} ({v.mpg} MPG, {v.fuel_type || 'gasoline'}, ${v.cost_per_gallon}/gal)
                  </option>
                ))}
              </select>
            </div>

            {/* Vehicle Name input */}
            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-slate-300 uppercase tracking-wider">
                Vehicle Name
              </label>
              <input
                type="text"
                value={editVehicleName}
                onChange={(e) => setEditVehicleName(e.target.value)}
                placeholder="e.g. 2011 Ford F-250 Super Duty"
                className="w-full bg-slate-900/90 border border-white/10 rounded-xl px-3.5 py-2 text-sm text-white focus:outline-none focus:border-purple-500"
              />
            </div>

            {/* Fuel Type & MPG Grid */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              {/* Fuel Type */}
              <div className="space-y-1.5">
                <label className="text-xs font-semibold text-slate-300 uppercase tracking-wider">
                  Fuel Type
                </label>
                <select
                  value={editFuelType}
                  onChange={(e) => setEditFuelType(e.target.value)}
                  className="w-full bg-slate-900/90 border border-white/10 rounded-xl px-3.5 py-2 text-sm text-white focus:outline-none focus:border-purple-500"
                >
                  <option value="gasoline">Gasoline</option>
                  <option value="diesel">Diesel</option>
                  <option value="premium">Premium Gas</option>
                  <option value="e85">E85 / Flex Fuel</option>
                </select>
              </div>

              {/* MPG */}
              <div className="space-y-1.5">
                <label className="text-xs font-semibold text-slate-300 uppercase tracking-wider">
                  Vehicle MPG
                </label>
                <input
                  type="number"
                  step="0.1"
                  min="1"
                  value={editMpg}
                  onChange={(e) => setEditMpg(e.target.value)}
                  className="w-full bg-slate-900/90 border border-white/10 rounded-xl px-3.5 py-2 text-sm text-white focus:outline-none focus:border-purple-500"
                />
              </div>

              {/* Price per Gallon */}
              <div className="space-y-1.5">
                <label className="text-xs font-semibold text-slate-300 uppercase tracking-wider">
                  Fuel Price ($/gal)
                </label>
                <input
                  type="number"
                  step="0.01"
                  min="0"
                  value={editCostPerGallon}
                  onChange={(e) => setEditCostPerGallon(e.target.value)}
                  className="w-full bg-slate-900/90 border border-white/10 rounded-xl px-3.5 py-2 text-sm text-white focus:outline-none focus:border-purple-500"
                />
              </div>
            </div>

            {/* Live Recalculation Preview */}
            <div className="p-4 rounded-xl bg-slate-900/80 border border-white/10 grid grid-cols-3 gap-3 text-center">
              <div>
                <span className="text-[10px] text-slate-400 uppercase font-semibold">Distance</span>
                <p className="text-base font-bold text-white mt-0.5">{editingTrip.distance_miles} mi</p>
              </div>
              <div>
                <span className="text-[10px] text-slate-400 uppercase font-semibold">New Fuel Used</span>
                <p className="text-base font-bold text-purple-300 mt-0.5">{previewFuelUsed} gal</p>
              </div>
              <div>
                <span className="text-[10px] text-slate-400 uppercase font-semibold">Recalculated Cost</span>
                <p className="text-base font-bold text-emerald-400 mt-0.5">${previewCost}</p>
              </div>
            </div>

            {/* Modal Actions */}
            <div className="flex items-center justify-end gap-3 pt-3 border-t border-white/10">
              <button
                type="button"
                onClick={() => setEditingTrip(null)}
                disabled={isSavingTrip}
                className="px-4 py-2 rounded-xl text-xs font-semibold text-slate-400 hover:text-white transition-all"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleSaveTrip}
                disabled={isSavingTrip}
                className="glass-button px-5 py-2 rounded-xl text-xs font-bold text-white bg-purple-600 hover:bg-purple-500 transition-all flex items-center gap-2 shadow-lg shadow-purple-600/30"
              >
                {isSavingTrip ? (
                  <>
                    <RefreshCw size={14} className="animate-spin" />
                    <span>Saving...</span>
                  </>
                ) : (
                  <>
                    <CheckCircle2 size={14} />
                    <span>Save Changes</span>
                  </>
                )}
              </button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
};

export default Wander;
