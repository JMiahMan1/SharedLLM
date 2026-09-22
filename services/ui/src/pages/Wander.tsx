import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useAuth } from '../context/AuthContext';
import { useHaptics } from '../hooks/useHaptics';
import { api } from '../services/api';
import type { Trip, TripLocation, TripUpdatePayload, TripLocationsResponse, Workout, RoutePoint, StepsResponse, ActivityTrendsResponse } from '../types/api';
import Modal from '../components/ui/Modal';
import MiniRouteMap from '../components/geo/MiniRouteMap';
import TripLocationsMap from '../components/geo/TripLocationsMap';
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
  Footprints,
  Bike,
  Mountain,
  PawPrint,
  Activity,
  Play,
  Square,
  Flame,
  TrendingUp,
  Share2,
  PersonStanding,
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
  assignedVehicle?: string;
  lastUpdated?: string;
}

type ActivityType =
  | 'driving'
  | 'walking'
  | 'running'
  | 'cycling'
  | 'mountain_biking'
  | 'dirtbiking'
  | 'horseback_riding';

const WORKOUT_OPTIONS: Array<{ type: ActivityType; label: string; icon: typeof Footprints; color: string }> = [
  { type: 'walking', label: 'Walk', icon: Footprints, color: 'text-emerald-400' },
  { type: 'running', label: 'Run', icon: PersonStanding, color: 'text-rose-400' },
  { type: 'cycling', label: 'Bike Ride', icon: Bike, color: 'text-sky-400' },
  { type: 'mountain_biking', label: 'Mountain Bike', icon: Mountain, color: 'text-orange-400' },
  { type: 'dirtbiking', label: 'Dirtbike Ride', icon: Zap, color: 'text-amber-400' },
  { type: 'horseback_riding', label: 'Horseback Ride', icon: PawPrint, color: 'text-purple-400' },
];

const ACTIVITY_LABELS: Record<string, string> = {
  driving: 'Drive',
  walking: 'Walk',
  running: 'Run',
  cycling: 'Bike Ride',
  mountain_biking: 'Mountain Bike',
  dirtbiking: 'Dirtbike Ride',
  horseback_riding: 'Horseback Ride',
};

const ACTIVITY_ICONS: Record<string, typeof Footprints> = {
  driving: Car,
  walking: Footprints,
  running: PersonStanding,
  cycling: Bike,
  mountain_biking: Mountain,
  dirtbiking: Zap,
  horseback_riding: PawPrint,
};

/**
 * Render a trip endpoint: prefer the resolved place name, fall back to
 * coordinates, then to a generic label. The geo service stores these as
 * `latitude`/`longitude`, older records as `lat`/`lon`.
 */
const formatTripLocation = (loc: TripLocation | undefined, fallback: string): string => {
  if (loc?.name) return loc.name;
  const lat = loc?.latitude ?? loc?.lat;
  const lon = loc?.longitude ?? loc?.lon;
  if (typeof lat === 'number' && typeof lon === 'number') {
    return `${lat.toFixed(4)}, ${lon.toFixed(4)}`;
  }
  return loc?.zone || fallback;
};

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

  // Steps (hardware pedometer) & trends
  const [steps, setSteps] = useState<StepsResponse | null>(null);
  const [trends, setTrends] = useState<ActivityTrendsResponse | null>(null);
  const [trendsLoading, setTrendsLoading] = useState(false);
  const [trendsAnalysis, setTrendsAnalysis] = useState<string | null>(null);

  // Workouts
  const [workouts, setWorkouts] = useState<Workout[]>([]);
  const [activeWorkout, setActiveWorkout] = useState<Workout | null>(null);

  // Route previews (fetched lazily per card)
  const [routePoints, setRoutePoints] = useState<Record<string, RoutePoint[]>>({});
  const routeCache = useRef<Record<string, RoutePoint[]>>({});

  // Trip locations modal (clickable start/destination)
  const [tripLocations, setTripLocations] = useState<TripLocationsResponse | null>(null);
  const [tripLocationsPath, setTripLocationsPath] = useState<RoutePoint[]>([]);
  const [tripLocationsTitle, setTripLocationsTitle] = useState('');
  // Edit Trip Modal state
  const [editingTrip, setEditingTrip] = useState<Trip | null>(null);
  const [selectedVehicleId, setSelectedVehicleId] = useState<string>('');
  const [editVehicleName, setEditVehicleName] = useState<string>('');
  const [editFuelType, setEditFuelType] = useState<string>('gasoline');
  const [editMpg, setEditMpg] = useState<number | string>(25.0);
  const [editCostPerGallon, setEditCostPerGallon] = useState<number | string>(3.65);
  const [editActivityType, setEditActivityType] = useState<string>('driving');
  const [editNotes, setEditNotes] = useState<string>('');
  const [isSavingTrip, setIsSavingTrip] = useState(false);

  // Share Trip Modal state
  const [sharingTrip, setSharingTrip] = useState<Trip | null>(null);
  const [shareSelection, setShareSelection] = useState<string[]>([]);
  const [isSavingShare, setIsSavingShare] = useState(false);

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

      // Parse Family Members from HA People collection — real entities only.
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
            const speedRaw = Number(props.speed || 0);
            const battery = props.battery != null ? Number(props.battery) : null;
            members.push({
              id: entityId,
              name: rawName,
              zone: zone.charAt(0).toUpperCase() + zone.slice(1),
              isMoving: speedRaw > 1.0,
              speedMph: Math.round(speedRaw * 2.23694), // m/s -> mph
              battery,
            });
          }
        }
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

  const fetchStepsAndWorkouts = useCallback(async () => {
    try {
      const stepsRes = await api.getDailySteps(currentUsername || undefined, 7);
      setSteps(stepsRes);
    } catch {
      // Steps require pedometer hardware; absence is not an error state
      setSteps(null);
    }
    try {
      const workoutsRes = await api.getWorkouts(undefined, 10);
      setWorkouts(workoutsRes.workouts || []);
    } catch {
      setWorkouts([]);
    }
  }, [currentUsername]);

  const fetchTrends = useCallback(
    async (refresh = false) => {
      setTrendsLoading(true);
      try {
        const res = await api.getActivityTrends(currentUsername || undefined, 7, refresh);
        setTrends(res);
        setTrendsAnalysis(res.analysis_available ? res.analysis : null);
      } catch {
        setTrends(null);
        setTrendsAnalysis(null);
      } finally {
        setTrendsLoading(false);
      }
    },
    [currentUsername]
  );

  useEffect(() => {
    let active = true;
    const init = async () => {
      try {
        await fetchTripsAndTelemetry();
        if (!active) return;
        await fetchStepsAndWorkouts();
        if (!active) return;
        await fetchTrends();
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
  }, [fetchTripsAndTelemetry, fetchStepsAndWorkouts, fetchTrends]);

  // Live polling: trips and step counts should appear as events happen,
  // not only when the user remembers to hit Refresh.
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState !== 'visible') return;
      void fetchTripsAndTelemetry();
      void fetchStepsAndWorkouts();
    }, 15000);
    return () => window.clearInterval(timer);
  }, [fetchTripsAndTelemetry, fetchStepsAndWorkouts]);

  // Also refresh the moment the user comes back to the tab
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible') {
        void fetchTripsAndTelemetry();
        void fetchStepsAndWorkouts();
      }
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [fetchTripsAndTelemetry, fetchStepsAndWorkouts]);

  // Workout control handlers
  const [nowSeconds, setNowSeconds] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const timer = setInterval(() => setNowSeconds(Date.now() / 1000), 30000);
    return () => clearInterval(timer);
  }, []);

  const handleStartWorkout = async (activityType: ActivityType) => {
    trigger('medium');
    try {
      const res = await api.startWorkout(activityType, currentUsername || undefined);
      if (res.status === 'already_active') {
        toast.error('You already have a workout in progress. Stop it first.');
        // Refresh to surface the active workout
        const list = await api.getWorkouts(currentUsername || undefined, 10);
        const active = list.workouts.find((w) => w.status === 'active');
        setActiveWorkout(active || null);
        return;
      }
      setActiveWorkout(res.workout);
      toast.success(`${ACTIVITY_LABELS[activityType] || activityType} started — breadcrumbs recording`);
    } catch (err: unknown) {
      const errorMsg =
        err && typeof err === 'object' && 'response' in err
          ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail
          : undefined;
      toast.error(errorMsg || 'Failed to start workout.');
    }
  };

  const handleStopWorkout = async () => {
    if (!activeWorkout) return;
    trigger('medium');
    try {
      const res = await api.stopWorkout({ user_id: currentUsername || undefined });
      toast.success('Workout saved');
      setActiveWorkout(null);
      // Prepend the finished workout
      setWorkouts((prev) => [res.workout, ...prev].slice(0, 10));
      await fetchStepsAndWorkouts();
      await fetchTrends(true);
    } catch (err: unknown) {
      const errorMsg =
        err && typeof err === 'object' && 'response' in err
          ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail
          : undefined;
      toast.error(errorMsg || 'Failed to stop workout.');
    }
  };

  // Lazy route loading for trip/workout cards
  const loadRoute = useCallback(async (key: string, loader: () => Promise<RoutePoint[]>) => {
    if (routeCache.current[key]) {
      setRoutePoints((prev) => ({ ...prev, [key]: routeCache.current[key] }));
      return;
    }
    try {
      const points = await loader();
      routeCache.current[key] = points;
      setRoutePoints((prev) => ({ ...prev, [key]: points }));
    } catch {
      // Route unavailable (e.g. no breadcrumbs) — card renders without map
    }
  }, []);

  // Open the trip-locations map modal for a given trip
  const openTripLocations = useCallback(async (trip: Trip) => {
    setTripLocationsTitle(`${trip.user_name || 'Trip'} — Route Map`);
    // Seed from the trip card so the modal opens immediately even if the API is slow/fails.
    const seedStart = trip.start_location;
    const seedEnd = trip.end_location;
    setTripLocations({
      trip_id: trip.id,
      start: {
        name: seedStart?.name || 'Starting Point',
        lat: seedStart?.latitude ?? seedStart?.lat ?? null,
        lon: seedStart?.longitude ?? seedStart?.lon ?? null,
        source: 'stored',
      },
      end: {
        name: seedEnd?.name || 'Destination',
        lat: seedEnd?.latitude ?? seedEnd?.lat ?? null,
        lon: seedEnd?.longitude ?? seedEnd?.lon ?? null,
        source: 'stored',
      },
    });
    setTripLocationsPath(routeCache.current[`${trip.id}:route`] || []);
    try {
      const [locs, route] = await Promise.allSettled([
        api.getTripLocations(trip.id),
        api.getTripRoute(trip.id),
      ]);
      if (locs.status === 'fulfilled') {
        setTripLocations(locs.value);
      }
      if (route.status === 'fulfilled' && route.value.points.length > 0) {
        setTripLocationsPath(route.value.points);
        routeCache.current[`${trip.id}:route`] = route.value.points;
      } else if (locs.status === 'fulfilled') {
        const s = locs.value.start;
        const e = locs.value.end;
        if (s.lat != null && e.lat != null && s.lon != null && e.lon != null) {
          setTripLocationsPath([
            { t: 0, lat: s.lat, lon: s.lon, spd: 0 },
            { t: 1, lat: e.lat, lon: e.lon, spd: 0 },
          ]);
        }
      }
    } catch {
      // Modal already open with card-seeded coords
    }
  }, []);

  // Check if current user is owner of a trip
  const isTripOwner = useCallback(    (trip: Trip): boolean => {
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

  // Aggregate stats — only driving trips contribute fuel/cost
  const stats = useMemo(() => {
    let totalMiles = 0;
    let totalCost = 0;
    let totalFuel = 0;
    let sharedCount = 0;

    for (const t of displayedTrips) {
      totalMiles += Number(t.distance_miles || 0);
      if ((t.activity_type || 'driving') === 'driving') {
        totalCost += Number(t.trip_cost_usd || 0);
        totalFuel += Number(t.fuel_used_gal || 0);
      }
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
    setEditActivityType(trip.activity_type || 'driving');
    setEditNotes(trip.notes || '');
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

  // Save Trip updates (vehicle, fuel, activity type, notes)
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
        activity_type: editActivityType,
        notes: editNotes.trim() || undefined,
      };

      const updated = await api.updateTrip(editingTrip.id, payload);
      toast.success('Trip updated!');

      // Update in local state
      setTrips((prev) => prev.map((t) => (t.id === updated.id ? { ...t, ...updated } : t)));
      setEditingTrip(null);
      await fetchTrends(true);
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

  // Share trip with manually-chosen riders
  const handleOpenShare = (trip: Trip) => {
    trigger('light');
    if (!isTripOwner(trip)) {
      toast.error(`Only ${trip.user_name || 'the trip owner'} can share this trip.`);
      return;
    }
    setSharingTrip(trip);
    setShareSelection((trip.shared_with || []).map((r) => r.user_id));
  };

  const handleSaveShare = async () => {
    if (!sharingTrip) return;
    trigger('medium');
    setIsSavingShare(true);
    try {
      const updated = await api.shareTrip(sharingTrip.id, shareSelection);
      setTrips((prev) => prev.map((t) => (t.id === updated.id ? { ...t, ...updated } : t)));
      toast.success(shareSelection.length > 0 ? 'Trip shared!' : 'Sharing removed');
      setSharingTrip(null);
    } catch (err: unknown) {
      const errorMsg =
        err && typeof err === 'object' && 'response' in err
          ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail
          : 'Failed to share trip.';
      toast.error(errorMsg || 'Failed to share trip.');
    } finally {
      setIsSavingShare(false);
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

  const formatActivityLabel = (trip: Trip) => ACTIVITY_LABELS[trip.activity_type || 'driving'] || 'Drive';

  // Recalculated values in edit modal preview
  const previewFuelUsed = useMemo(() => {
    if (!editingTrip) return 0;
    if (editActivityType !== 'driving') return 0;
    const mpgNum = Number(editMpg);
    if (!mpgNum || mpgNum <= 0) return 0;
    return Number((editingTrip.distance_miles / mpgNum).toFixed(2));
  }, [editingTrip, editMpg, editActivityType]);

  const previewCost = useMemo(() => {
    if (editActivityType !== 'driving') return 0;
    const costNum = Number(editCostPerGallon);
    if (!costNum || costNum < 0) return 0;
    return Number((previewFuelUsed * costNum).toFixed(2));
  }, [previewFuelUsed, editCostPerGallon, editActivityType]);

  // Steps ring progress
  const stepsGoal = steps?.goal || 10000;
  const stepsToday = steps?.today || 0;
  const stepsPct = Math.min(100, Math.round((stepsToday / stepsGoal) * 100));

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
                Family presence, trips, workouts, and step tracking — Inspired by Elijah&apos;s journey in the wilderness (1 Kings 17:3–6).
              </p>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => {
              void (async () => {
                await fetchTripsAndTelemetry();
                await fetchStepsAndWorkouts();
              })();
            }}
            disabled={isRefreshing}
            className="glass-button flex items-center gap-2 px-3 py-2 text-xs font-semibold text-slate-300 hover:text-white rounded-xl"
            title="Refresh Trips & Telemetry"
          >
            <RefreshCw size={14} className={isRefreshing ? 'animate-spin text-purple-400' : ''} />
            <span>Refresh</span>
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

        {familyMembers.length === 0 ? (
          <div className="glass-panel p-6 rounded-2xl border border-white/5 text-center">
            <p className="text-sm text-slate-400">No family presence entities reporting yet.</p>
            <p className="text-xs text-slate-500 mt-1">Location tracking starts automatically once family members share their GPS from the mobile app.</p>
          </div>
        ) : (
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
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Steps Ring + Trends Row */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Daily Steps Ring (hardware pedometer) */}
        <div className="glass-panel p-5 rounded-2xl border border-white/5 flex items-center gap-5">
          <div className="relative w-24 h-24 shrink-0">
            <svg viewBox="0 0 100 100" className="w-24 h-24 -rotate-90">
              <circle cx="50" cy="50" r="42" fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="10" />
              <circle
                cx="50"
                cy="50"
                r="42"
                fill="none"
                stroke={stepsPct >= 100 ? '#10b981' : '#a78bfa'}
                strokeWidth="10"
                strokeLinecap="round"
                strokeDasharray={`${(stepsPct / 100) * 2 * Math.PI * 42} ${2 * Math.PI * 42}`}
                className="transition-all duration-700"
              />
            </svg>
            <div className="absolute inset-0 flex flex-col items-center justify-center">
              <Footprints size={18} className="text-purple-400 mb-0.5" />
              <span className="text-sm font-bold text-white">{stepsPct}%</span>
            </div>
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-1.5 text-[11px] text-slate-400 uppercase tracking-wider font-semibold">
              <Flame size={12} className="text-orange-400" />
              Steps Today
            </div>
            {steps && steps.today > 0 ? (
              <>
                <p className="text-2xl font-bold text-white mt-0.5">{stepsToday.toLocaleString()}</p>
                <p className="text-[11px] text-slate-500">
                  of {stepsGoal.toLocaleString()} goal
                  {stepsPct >= 100 ? ' — goal reached! 🎉' : ''}
                </p>
                {Object.keys(steps.daily_steps || {}).length > 0 && (
                  <div className="flex items-end gap-1 mt-2 h-8">
                    {Object.entries(steps.daily_steps)
                      .slice(-7)
                      .map(([day, count]) => {
                        const maxVal = Math.max(...Object.values(steps.daily_steps), 1);
                        const h = Math.max(4, Math.round((count / maxVal) * 100));
                        return (
                          <div
                            key={day}
                            className="w-3.5 rounded-t bg-purple-500/60 hover:bg-purple-400 transition-all"
                            style={{ height: `${h}%` }}
                            title={`${day}: ${count.toLocaleString()} steps`}
                          />
                        );
                      })}
                  </div>
                )}
              </>
            ) : (
              <p className="text-sm text-slate-500 mt-1">
                {steps
                  ? 'No pedometer reading yet today — open the app on your phone to sync.'
                  : 'No pedometer data. Steps sync from your phone\u2019s hardware step counter when the app is open.'}
              </p>
            )}
          </div>
        </div>

        {/* Trends + LLM Analysis */}
        <div className="lg:col-span-2 glass-panel p-5 rounded-2xl border border-white/5">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-1.5 text-[11px] text-slate-400 uppercase tracking-wider font-semibold">
              <TrendingUp size={13} className="text-indigo-400" />
              7-Day Activity Trends
            </div>
            <button
              onClick={() => void fetchTrends(true)}
              disabled={trendsLoading}
              className="glass-button flex items-center gap-1.5 px-2.5 py-1 text-[11px] font-semibold text-slate-300 hover:text-white rounded-lg"
              title="Re-analyze with fresh data"
            >
              <RefreshCw size={11} className={trendsLoading ? 'animate-spin text-indigo-400' : ''} />
              Re-analyze
            </button>
          </div>

          {trendsLoading && !trends ? (
            <div className="py-6 text-center text-sm text-slate-500">Loading activity trends...</div>
          ) : trends ? (
            <div className="space-y-3">
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <div className="p-2.5 rounded-xl bg-white/[0.03] border border-white/5">
                  <span className="text-[10px] text-slate-400 uppercase font-semibold">Steps Avg/Day</span>
                  <p className="text-base font-bold text-purple-300 mt-0.5">
                    {(trends.steps_avg ?? trends.steps_average) != null
                      ? (trends.steps_avg ?? trends.steps_average)!.toLocaleString()
                      : '—'}
                  </p>
                </div>
                <div className="p-2.5 rounded-xl bg-white/[0.03] border border-white/5">
                  <span className="text-[10px] text-slate-400 uppercase font-semibold">Workouts</span>
                  <p className="text-base font-bold text-emerald-300 mt-0.5">{trends.workout_count}</p>
                </div>
                <div className="p-2.5 rounded-xl bg-white/[0.03] border border-white/5">
                  <span className="text-[10px] text-slate-400 uppercase font-semibold">Workout Miles</span>
                  <p className="text-base font-bold text-sky-300 mt-0.5">{(trends.workout_distance_miles || 0).toFixed(1)}</p>
                </div>
                <div className="p-2.5 rounded-xl bg-white/[0.03] border border-white/5">
                  <span className="text-[10px] text-slate-400 uppercase font-semibold">Drive Cost</span>
                  <p className="text-base font-bold text-amber-300 mt-0.5">${(trends.drive_cost_usd || 0).toFixed(2)}</p>
                </div>
              </div>

              {trendsAnalysis ? (
                <div className="p-3 rounded-xl bg-indigo-500/10 border border-indigo-500/20">
                  <div className="flex items-center gap-1.5 text-[11px] font-semibold text-indigo-300 uppercase tracking-wider mb-1">
                    <SparklesBadge />
                    AI Insight
                  </div>
                  <p className="text-xs text-slate-300 leading-relaxed whitespace-pre-wrap">{trendsAnalysis}</p>
                </div>
              ) : (
                <div className="p-3 rounded-xl bg-white/[0.02] border border-white/5 text-[11px] text-slate-500">
                  {trendsLoading
                    ? 'Analyzing your activity with AI...'
                    : 'AI analysis unavailable right now — stats above are live from your recorded data.'}
                </div>
              )}
            </div>
          ) : (
            <div className="py-6 text-center text-sm text-slate-500">
              No activity data yet. Start a workout or take a drive to build your trends.
            </div>
          )}
        </div>
      </div>

      {/* Workouts Section */}
      <div className="space-y-3">
        <div className="flex items-center justify-between px-1">
          <h2 className="text-sm font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-2">
            <Activity size={16} className="text-emerald-400" />
            Workouts
          </h2>
          <span className="text-xs text-slate-500">Track walks, runs, rides — steps auto-counted</span>
        </div>

        {/* Start / Stop Controls */}
        <div className="glass-panel p-4 rounded-2xl border border-white/5">
          {activeWorkout ? (
            <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
              <div className="flex items-center gap-3">
                <span className="relative flex h-3 w-3">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                  <span className="relative inline-flex rounded-full h-3 w-3 bg-emerald-500" />
                </span>
                <div>
                  <p className="text-sm font-semibold text-white flex items-center gap-2">
                    {(() => {
                      const Icon = ACTIVITY_ICONS[activeWorkout.activity_type] || Footprints;
                      return <Icon size={16} className="text-emerald-400" />;
                    })()}
                    {ACTIVITY_LABELS[activeWorkout.activity_type] || activeWorkout.activity_type} in progress
                  </p>
                  <p className="text-[11px] text-slate-400">
                    Started {new Date(activeWorkout.start_time * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}
                    {' · '}
                    elapsed {formatDuration(Math.max(0, nowSeconds - activeWorkout.start_time))}
                  </p>
                </div>
              </div>
              <button
                onClick={handleStopWorkout}
                className="flex items-center gap-2 px-4 py-2 rounded-xl bg-rose-600/90 hover:bg-rose-500 text-white text-xs font-bold transition-all shadow-lg shadow-rose-600/30"
              >
                <Square size={13} />
                Stop & Save
              </button>
            </div>
          ) : (
            <div>
              <p className="text-[11px] text-slate-400 mb-2.5 flex items-center gap-1.5">
                <Play size={11} className="text-emerald-400" />
                Start tracking an activity — distance and speed come from GPS breadcrumbs.
              </p>
              <div className="flex flex-wrap gap-2">
                {WORKOUT_OPTIONS.map((opt) => {
                  const Icon = opt.icon;
                  return (
                    <button
                      key={opt.type}
                      onClick={() => handleStartWorkout(opt.type)}
                      className={`flex items-center gap-1.5 px-3 py-2 rounded-xl bg-white/[0.04] hover:bg-white/[0.08] border border-white/10 hover:border-emerald-500/40 text-xs font-semibold text-slate-200 transition-all ${opt.color}`}
                    >
                      <Icon size={14} />
                      {opt.label}
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </div>

        {/* Workout History */}
        {workouts.length > 0 ? (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {workouts.map((workout) => {
              const Icon = ACTIVITY_ICONS[workout.activity_type] || Footprints;
              return (
                <div key={workout.id} className="glass-card p-4 rounded-2xl border border-white/5 shadow-lg">
                  <div className="flex items-start justify-between gap-2 mb-2">
                    <div className="flex items-center gap-2.5">
                      <div className="p-2 rounded-lg bg-emerald-500/10 border border-emerald-500/20">
                        <Icon size={16} className="text-emerald-400" />
                      </div>
                      <div>
                        <p className="text-sm font-semibold text-slate-100">
                          {ACTIVITY_LABELS[workout.activity_type] || workout.activity_type}
                        </p>
                        <p className="text-[11px] text-slate-500 flex items-center gap-1">
                          <CalendarIcon size={10} />
                          {formatTimeRange(workout.start_time, workout.end_time || workout.start_time)}
                        </p>
                      </div>
                    </div>
                    {workout.steps != null && (
                      <div className="flex items-center gap-1 px-2 py-1 rounded-lg bg-purple-500/10 border border-purple-500/20 text-[11px] font-semibold text-purple-300">
                        <Footprints size={11} />
                        {workout.steps.toLocaleString()}
                        {workout.steps_source === 'gps_estimate' && (
                          <span className="text-[9px] text-slate-500 font-normal">est</span>
                        )}
                      </div>
                    )}
                  </div>

                  <div className="grid grid-cols-3 gap-2 text-center pt-1">
                    <div className="p-1.5 rounded-lg bg-white/[0.02]">
                      <span className="text-[9px] text-slate-500 uppercase">Distance</span>
                      <p className="text-xs font-bold text-white">{(workout.distance_miles || 0).toFixed(2)} mi</p>
                    </div>
                    <div className="p-1.5 rounded-lg bg-white/[0.02]">
                      <span className="text-[9px] text-slate-500 uppercase">Duration</span>
                      <p className="text-xs font-bold text-white">{formatDuration(workout.duration_seconds || 0)}</p>
                    </div>
                    <div className="p-1.5 rounded-lg bg-white/[0.02]">
                      <span className="text-[9px] text-slate-500 uppercase">Avg Pace</span>
                      <p className="text-xs font-bold text-white">
                        {workout.avg_speed_mph != null ? `${workout.avg_speed_mph} mph` : '—'}
                      </p>
                    </div>
                  </div>

                  {workout.notes && <p className="text-[11px] text-slate-400 mt-2 italic">{workout.notes}</p>}

                  <RoutePreview
                    id={workout.id}
                    completed={workout.status === 'completed'}
                    points={routePoints[workout.id]}
                    loadRoute={loadRoute}
                    fetcher={() => api.getWorkoutRoute(workout.id).then((r) => r.points)}
                  />
                </div>
              );
            })}
          </div>
        ) : (
          !activeWorkout && (
            <div className="glass-panel p-6 rounded-2xl border border-white/5 text-center">
              <p className="text-sm text-slate-400">No workouts recorded yet.</p>
              <p className="text-xs text-slate-500 mt-1">Start one above — walks and runs also estimate steps from GPS when your phone&apos;s pedometer isn&apos;t available.</p>
            </div>
          )
        )}
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
          <span className="text-[10px] text-slate-500 mt-0.5">Driving trips only</span>
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
            Driving trips log automatically over 10 MPH. Workouts are manual. Vehicle, fuel, and activity editable by trip owner.
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
            Trips are logged automatically whenever GPS speed exceeds 10 MPH — with real telemetry, there&apos;s nothing to seed or simulate.
          </p>
        </div>
      ) : (
        <div className="space-y-4">
          {displayedTrips.map((trip) => {
            const canEdit = isTripOwner(trip);
            const isShared = trip.is_shared;
            const ActivityIcon = ACTIVITY_ICONS[trip.activity_type || 'driving'] || Car;
            const isDrive = (trip.activity_type || 'driving') === 'driving';

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

                    {/* Activity Type Badge */}
                    <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-white/[0.04] text-xs font-semibold text-slate-300 border border-white/10">
                      <ActivityIcon size={12} className="text-purple-400" />
                      <span>{formatActivityLabel(trip)}</span>
                    </div>

                    {/* Shared Ride Badge — renders rider names properly (no [object Object]) */}
                    {isShared && trip.shared_with && trip.shared_with.length > 0 && (
                      <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-purple-500/20 text-purple-300 border border-purple-500/40 text-xs font-semibold">
                        <Users size={12} />
                        <span>
                          Shared with{' '}
                          {trip.shared_with
                            .map((r) => (typeof r === 'string' ? r : r.user_name || r.user_id))
                            .join(', ')}
                        </span>
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

                    <div
                      className="flex-1 space-y-2 cursor-pointer hover:bg-white/5 rounded px-1 -mx-1 transition-colors"
                      onClick={() => openTripLocations(trip)}
                    >
                      <div>
                        <span className="text-[10px] text-slate-500 uppercase font-semibold">Start</span>
                        <p className="text-xs font-medium text-slate-200 truncate">
                          {formatTripLocation(trip.start_location, 'Starting Point')}
                        </p>
                      </div>
                      <div>
                        <span className="text-[10px] text-slate-500 uppercase font-semibold">Destination</span>
                        <p className="text-xs font-medium text-slate-200 truncate">
                          {formatTripLocation(trip.end_location, 'Destination')}
                        </p>
                      </div>
                    </div>
                  </div>

                  {/* Telemetry Metrics */}
                  <div className="md:col-span-6 grid grid-cols-3 gap-2 text-center pt-2 md:pt-0 border-t md:border-t-0 md:border-l border-white/5 md:pl-4">
                    <div className="flex flex-col items-center justify-center p-2 rounded-lg bg-white/[0.02]">
                      <span className="text-[10px] text-slate-400 flex items-center gap-1">
                        <span title="GPS Distance is locked and immutable" className="inline-flex">
                          <Lock size={10} className="text-slate-500" aria-label="GPS Distance is locked and immutable" />
                        </span>
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
                        {isDrive ? <DollarSign size={10} className="text-emerald-400" /> : <Activity size={10} className="text-purple-400" />}
                        {isDrive ? 'Trip Cost' : 'Activity'}
                      </span>
                      {isDrive ? (
                        <span className="text-sm font-bold text-emerald-400 mt-0.5">${trip.trip_cost_usd}</span>
                      ) : (
                        <span className="text-sm font-bold text-purple-300 mt-0.5">{formatActivityLabel(trip)}</span>
                      )}
                    </div>
                  </div>
                </div>

                {/* Vehicle & Fuel Details Row (driving only) */}
                {isDrive && (
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

                    {/* Action Buttons (Only for owner) */}
                    <div className="flex items-center gap-2">
                      {canEdit ? (
                        <>
                          <button
                            onClick={() => handleOpenShare(trip)}
                            className="glass-button flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-indigo-300 hover:text-white border-indigo-500/30 rounded-lg transition-all"
                          >
                            <Share2 size={12} />
                            <span>Share</span>
                          </button>
                          <button
                            onClick={() => handleOpenEdit(trip)}
                            className="glass-button flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-purple-300 hover:text-white border-purple-500/30 rounded-lg transition-all"
                          >
                            <Edit2 size={12} />
                            <span>Edit Vehicle & Fuel</span>
                          </button>
                        </>
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
                )}

                {/* Non-drive action row */}
                {!isDrive && (
                  <div className="flex items-center justify-end gap-2 mt-3 pt-3 border-t border-white/5 text-xs">
                    {canEdit ? (
                      <>
                        <button
                          onClick={() => handleOpenShare(trip)}
                          className="glass-button flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-indigo-300 hover:text-white border-indigo-500/30 rounded-lg transition-all"
                        >
                          <Share2 size={12} />
                          <span>Share</span>
                        </button>
                        <button
                          onClick={() => handleOpenEdit(trip)}
                          className="glass-button flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-purple-300 hover:text-white border-purple-500/30 rounded-lg transition-all"
                        >
                          <Edit2 size={12} />
                          <span>Edit Activity</span>
                        </button>
                      </>
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
                )}

                {trip.notes && (
                  <p className="text-[11px] text-slate-400 mt-2 italic flex items-center gap-1.5">
                    <Activity size={11} className="text-slate-500" />
                    {trip.notes}
                  </p>
                )}

                <RoutePreview
                  id={trip.id}
                  completed={trip.status === 'completed'}
                  points={routePoints[trip.id]}
                  loadRoute={loadRoute}
                  fetcher={() => api.getTripRoute(trip.id).then((r) => r.points)}
                  onOpenMap={() => openTripLocations(trip)}
                />
              </div>
            );
          })}
        </div>
      )}

      {/* Edit Trip Modal */}
      <Modal
        isOpen={Boolean(editingTrip)}
        onClose={() => setEditingTrip(null)}
        title="Edit Trip Details"
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
                Route locations and distance ({editingTrip.distance_miles} miles) are verified telemetry and cannot be altered. You can change the activity, vehicle, MPG, and fuel price to recalculate fuel usage and costs.
              </p>
            </div>

            {/* Activity Type */}
            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-1.5">
                <Activity size={14} className="text-purple-400" />
                Activity Type
              </label>
              <select
                value={editActivityType}
                onChange={(e) => setEditActivityType(e.target.value)}
                className="w-full bg-slate-900/90 border border-white/10 rounded-xl px-3.5 py-2.5 text-sm text-white focus:outline-none focus:border-purple-500"
              >
                {Object.entries(ACTIVITY_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
              {editActivityType !== 'driving' && (
                <p className="text-[11px] text-slate-500">Non-driving activities don&apos;t consume fuel — cost fields will be zeroed.</p>
              )}
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

            {/* Notes */}
            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-slate-300 uppercase tracking-wider">
                Notes
              </label>
              <input
                type="text"
                value={editNotes}
                onChange={(e) => setEditNotes(e.target.value)}
                placeholder="e.g. Evening trail ride"
                className="w-full bg-slate-900/90 border border-white/10 rounded-xl px-3.5 py-2 text-sm text-white focus:outline-none focus:border-purple-500"
              />
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

      {/* Share Trip Modal */}
      <Modal
        isOpen={Boolean(sharingTrip)}
        onClose={() => setSharingTrip(null)}
        title="Share Trip"
        size="md"
      >
        {sharingTrip && (
          <div className="space-y-5">
            <p className="text-xs text-slate-400">
              Choose which family members rode along on this trip. This trip will appear in their history too.
            </p>

            {familyMembers.length === 0 ? (
              <p className="text-xs text-slate-500">No family members available to share with.</p>
            ) : (
              <div className="space-y-2">
                {familyMembers
                  .filter((m) => m.id.replace('person.', '').toLowerCase() !== currentUsername)
                  .map((member) => {
                    const riderId = member.id.replace('person.', '').toLowerCase();
                    const selected = shareSelection.includes(riderId);
                    return (
                      <label
                        key={member.id}
                        className={`flex items-center gap-3 p-3 rounded-xl border cursor-pointer transition-all ${
                          selected
                            ? 'bg-purple-500/10 border-purple-500/40'
                            : 'bg-white/[0.02] border-white/5 hover:border-white/15'
                        }`}
                      >
                        <input
                          type="checkbox"
                          checked={selected}
                          onChange={(e) => {
                            setShareSelection((prev) =>
                              e.target.checked ? [...prev, riderId] : prev.filter((id) => id !== riderId)
                            );
                          }}
                          className="w-4 h-4 accent-purple-500"
                        />
                        <div className="w-8 h-8 rounded-full bg-gradient-to-tr from-purple-600 to-indigo-600 flex items-center justify-center text-xs font-bold text-white">
                          {member.name.charAt(0).toUpperCase()}
                        </div>
                        <div>
                          <p className="text-sm font-semibold text-slate-200">{member.name}</p>
                          <p className="text-[10px] text-slate-500">{member.zone}</p>
                        </div>
                      </label>
                    );
                  })}
              </div>
            )}

            {/* Modal Actions */}
            <div className="flex items-center justify-end gap-3 pt-3 border-t border-white/10">
              <button
                type="button"
                onClick={() => setSharingTrip(null)}
                disabled={isSavingShare}
                className="px-4 py-2 rounded-xl text-xs font-semibold text-slate-400 hover:text-white transition-all"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleSaveShare}
                disabled={isSavingShare}
                className="glass-button px-5 py-2 rounded-xl text-xs font-bold text-white bg-indigo-600 hover:bg-indigo-500 transition-all flex items-center gap-2 shadow-lg shadow-indigo-600/30"
              >
                {isSavingShare ? (
                  <>
                    <RefreshCw size={14} className="animate-spin" />
                    <span>Saving...</span>
                  </>
                ) : (
                  <>
                    <Users size={14} />
                    <span>Save Sharing</span>
                  </>
                )}
              </button>
            </div>
          </div>
        )}
      </Modal>

      {/* Trip Locations Map Modal */}
      <Modal
        isOpen={Boolean(tripLocations)}
        onClose={() => { setTripLocations(null); setTripLocationsPath([]); }}
        title={tripLocationsTitle || 'Trip Map'}
        size="lg"
      >
        {tripLocations && (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3 text-xs">
              <div className="bg-black/30 rounded-lg p-3">
                <div className="flex items-center gap-2 text-emerald-400 mb-1">
                  <div className="w-2 h-2 rounded-full bg-emerald-400" />
                  <span className="font-semibold uppercase text-[10px]">{tripLocations.start.name}</span>
                </div>
                {tripLocations.start.lat != null && tripLocations.start.lon != null ? (
                  <p className="text-slate-400">{tripLocations.start.lat.toFixed(4)}, {tripLocations.start.lon.toFixed(4)}</p>
                ) : (
                  <p className="text-slate-400">unknown</p>
                )}
              </div>
              <div className="bg-black/30 rounded-lg p-3">
                <div className="flex items-center gap-2 text-rose-400 mb-1">
                  <div className="w-2 h-2 rounded-full bg-rose-400" />
                  <span className="font-semibold uppercase text-[10px]">{tripLocations.end.name}</span>
                </div>
                {tripLocations.end.lat != null && tripLocations.end.lon != null ? (
                  <p className="text-slate-400">{tripLocations.end.lat.toFixed(4)}, {tripLocations.end.lon.toFixed(4)}</p>
                ) : (
                  <p className="text-slate-400">unknown</p>
                )}
              </div>
            </div>
            <TripLocationsMap
              start={tripLocations.start}
              end={tripLocations.end}
              path={tripLocationsPath}
            />
          </div>
        )}
      </Modal>
    </div>
  );
};

// Small sparkle icon for AI insight header
const SparklesBadge = () => (
  <span className="inline-flex items-center justify-center w-4 h-4 rounded bg-indigo-500/30 border border-indigo-400/40">
    <TrendingUp size={10} className="text-indigo-300" />
  </span>
);

interface RoutePreviewProps {
  id: string;
  completed: boolean;
  points: RoutePoint[] | undefined;
  loadRoute: (key: string, loader: () => Promise<RoutePoint[]>) => void;
  fetcher: () => Promise<RoutePoint[]>;
  /** Opens the full Route Map modal when the thumbnail is clicked. */
  onOpenMap?: () => void;
}

/** Lazily loads and renders an OSM mini-map for a trip or workout. */
const RoutePreview = ({ id, completed, points, loadRoute, fetcher, onOpenMap }: RoutePreviewProps) => {
  useEffect(() => {
    if (!points && completed) {
      loadRoute(id, fetcher);
    }
  }, [points, completed, id, loadRoute, fetcher]);

  if (!points || points.length === 0) return null;
  return (
    <MiniRouteMap
      points={points}
      height={120}
      className="rounded-xl border border-white/10 overflow-hidden mt-3"
      onClick={onOpenMap}
      title={onOpenMap ? 'Open route map' : undefined}
    />
  );
};

export default Wander;
