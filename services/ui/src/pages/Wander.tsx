import { useState, useCallback, useMemo, useRef } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useAuth } from '../context/AuthContext';
import { useHaptics } from '../hooks/useHaptics';
import { api } from '../services/api';
import type { Trip, TripLocation, TripUpdatePayload, TripLocationsResponse, TripLocationSuggestion, RoutePoint, TripsResponse } from '../types/api';
import Modal from '../components/ui/Modal';
import LiveFamilyMap from '../components/geo/LiveFamilyMap';
import TripLocationsMap from '../components/geo/TripLocationsMap';
import RoutePreview from '../components/geo/RoutePreview';
import SensorStatusBanner from '../components/location/SensorStatusBanner';
import { ACTIVITY_ICONS, ACTIVITY_LABELS, formatDuration, formatTimeRange } from '../lib/workoutMeta';
import {
  displayName,
  filterTrips,
  formatDistanceMeters,
  formatTripLocation,
  isTripOwner,
  relativeTime,
  tripStats,
  type TripFilter,
} from '../lib/wanderTrips';
import {
  geoPeopleQueryKey,
  geoZonesQueryKey,
  houseConfigQueryOptions,
  tripsQueryKey,
  tripsQueryOptions,
  vehiclesQueryKey,
} from '../lib/wanderQueries';
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
  Route,
  CheckCircle2,
  Compass,
  Share2,
  Lock,
  Zap,
  Activity,
  MapPinned,
  Loader2,
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
  /** GPS accuracy in metres, when the source reports it. */
  accuracy?: number | null;
  /** Zones the member is currently inside (from HA). */
  inZones?: string[];
}

const Wander = () => {
  const { user } = useAuth();
  const { trigger } = useHaptics();
  const queryClient = useQueryClient();

  const currentUsername = (user?.username || '').toLowerCase();

  const [focusMember, setFocusMember] = useState<string | null>(null);
  const [filterTab, setFilterTab] = useState<TripFilter>('all');

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
  // Editable place names + nearby-place picker
  const [editStartName, setEditStartName] = useState<string>('');
  const [editEndName, setEditEndName] = useState<string>('');
  const [editStartAddress, setEditStartAddress] = useState<string>('');
  const [editEndAddress, setEditEndAddress] = useState<string>('');
  const [suggestTarget, setSuggestTarget] = useState<'start' | 'end' | null>(null);
  const [suggestLoading, setSuggestLoading] = useState(false);
  const [suggestions, setSuggestions] = useState<TripLocationSuggestion[]>([]);

  // Share Trip Modal state
  const [sharingTrip, setSharingTrip] = useState<Trip | null>(null);
  const [shareSelection, setShareSelection] = useState<string[]>([]);
  const [isSavingShare, setIsSavingShare] = useState(false);

  // One query per source instead of a 15s hand-rolled interval. Trips now come
  // back scoped to the caller (geo applies per-user opt-in consent), so the
  // page and the LiveFamilyMap read the same numbers from one cache.
  // Keys come from lib/wanderQueries so this page and the dashboard's Family
  // Presence card share one cache entry instead of drifting apart.
  const tripsQuery = useQuery({
    queryKey: tripsQueryKey(),
    queryFn: () => api.getTrips(),
    ...tripsQueryOptions,
  });
  const vehiclesQuery = useQuery({
    queryKey: vehiclesQueryKey(),
    queryFn: () => api.getVehicles(),
    ...houseConfigQueryOptions,
  });
  const peopleQuery = useQuery({
    queryKey: geoPeopleQueryKey(),
    queryFn: () => api.getGeoPeople(),
    ...houseConfigQueryOptions,
  });
  const zonesQuery = useQuery({
    queryKey: geoZonesQueryKey(),
    queryFn: () => api.getGeoZones(),
    ...houseConfigQueryOptions,
  });

  const trips = useMemo(() => tripsQuery.data?.trips ?? [], [tripsQuery.data]);
  const vehicles = useMemo(
    () => (vehiclesQuery.data?.vehicles ?? []) as VehicleOption[],
    [vehiclesQuery.data],
  );
  const isLoading = tripsQuery.isLoading;
  // Only a background refetch advertises itself; the old code set the spinner
  // inside the polling path, so Refresh appeared to spin every 15 seconds.
  const isRefreshing = tripsQuery.isFetching && !tripsQuery.isLoading;

  /** HA zones become "Places" on the live map (Life360-style geofences). */
  const places = useMemo(() => {
    const geoJson = zonesQuery.data as
      | {
          features?: Array<{
            properties?: Record<string, unknown>;
            geometry?: { coordinates?: [number, number] };
          }>;
        }
      | undefined;
    return (geoJson?.features ?? [])
      .map((feature) => {
        const props = feature.properties || {};
        const coords = feature.geometry?.coordinates;
        const lat = Number(props.latitude ?? coords?.[1]);
        const lon = Number(props.longitude ?? coords?.[0]);
        const radius = Number(props.radius ?? 100);
        if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
        return {
          id: String(props.entity_id || props.friendly_name || `${lat},${lon}`),
          name: String(props.friendly_name || 'Place'),
          lat,
          lon,
          radius: Number.isFinite(radius) && radius > 0 ? radius : 100,
        };
      })
      .filter(
        (z): z is { id: string; name: string; lat: number; lon: number; radius: number } =>
          z !== null,
      );
  }, [zonesQuery.data]);

  /** Parse Family Members from the HA People collection - real entities only. */
  const familyMembers = useMemo(() => {
    const geoJson = peopleQuery.data as
      | { features?: Array<{ properties?: Record<string, unknown> }> }
      | undefined;
    const members: FamilyMemberStatus[] = [];
    for (const feat of geoJson?.features ?? []) {
      const props = feat.properties || {};
      const entityId = String(props.entity_id || '');
      if (!entityId.startsWith('person.')) continue;
      const rawName = String(props.friendly_name || entityId.replace('person.', ''));
      const zone = String(props.state || 'Unknown');
      const speedRaw = Number(props.speed || 0);
      const battery = props.battery != null ? Number(props.battery) : null;
      const accuracy = props.gps_accuracy != null ? Number(props.gps_accuracy) : null;
      const lastUpdated = props.last_updated != null ? String(props.last_updated) : undefined;
      const inZones = Array.isArray(props.in_zones) ? (props.in_zones as string[]) : [];
      members.push({
        id: entityId,
        name: rawName,
        zone: zone.charAt(0).toUpperCase() + zone.slice(1),
        isMoving: speedRaw > 1.0,
        speedMph: Math.round(speedRaw * 2.23694), // m/s -> mph
        battery: Number.isFinite(battery as number) ? battery : null,
        accuracy: Number.isFinite(accuracy as number) ? accuracy : null,
        lastUpdated,
        inZones,
      });
    }
    return members;
  }, [peopleQuery.data]);

  const refreshAll = useCallback(() => {
    trigger('light');
    void Promise.all([
      tripsQuery.refetch(),
      vehiclesQuery.refetch(),
      peopleQuery.refetch(),
      zonesQuery.refetch(),
    ]);
  }, [tripsQuery, vehiclesQuery, peopleQuery, zonesQuery, trigger]);

  const loadError = tripsQuery.isError || peopleQuery.isError || zonesQuery.isError;

  // Lazy route loading for trip cards
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

  // Filtering and stats come from lib/wanderTrips, where they are unit-tested.
  const displayedTrips = useMemo(
    () => filterTrips(trips, filterTab, currentUsername),
    [trips, filterTab, currentUsername],
  );

  const stats = useMemo(() => tripStats(displayedTrips), [displayedTrips]);

  // Open Edit Modal
  const handleOpenEdit = (trip: Trip) => {
    trigger('light');
    if (!isTripOwner(trip, currentUsername)) {
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
    setEditStartName(trip.start_location?.name || '');
    setEditEndName(trip.end_location?.name || '');
    setEditStartAddress(trip.start_location?.address || '');
    setEditEndAddress(trip.end_location?.address || '');
    setSuggestTarget(null);
    setSuggestions([]);
  };

  // Coordinates for a trip endpoint (tolerates legacy lat/lon spelling)
  const tripLocationCoords = (loc?: TripLocation): { lat: number; lon: number } | null => {
    const lat = loc?.latitude ?? loc?.lat;
    const lon = loc?.longitude ?? loc?.lon;
    return typeof lat === 'number' && typeof lon === 'number' ? { lat, lon } : null;
  };

  // Load nearby store/restaurant/zone names for a trip endpoint
  const handleOpenSuggestions = async (target: 'start' | 'end') => {
    if (!editingTrip) return;
    const loc = target === 'start' ? editingTrip.start_location : editingTrip.end_location;
    const coords = tripLocationCoords(loc);
    if (!coords) {
      toast.error('No GPS coordinates recorded for this location.');
      return;
    }
    trigger('light');
    setSuggestTarget(target);
    setSuggestLoading(true);
    setSuggestions([]);
    try {
      const res = await api.getLocationSuggestions(coords.lat, coords.lon);
      setSuggestions(res.candidates || []);
    } catch {
      toast.error('Could not load nearby places.');
      setSuggestions([]);
    } finally {
      setSuggestLoading(false);
    }
  };

  // Apply a picked place: display name changes, address stays stored
  const applySuggestion = (s: TripLocationSuggestion) => {
    if (!suggestTarget) return;
    trigger('light');
    if (suggestTarget === 'start') {
      setEditStartName(s.name);
      if (s.address) setEditStartAddress(s.address);
    } else {
      setEditEndName(s.name);
      if (s.address) setEditEndAddress(s.address);
    }
    setSuggestTarget(null);
    setSuggestions([]);
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
        start_name: editStartName.trim() || undefined,
        end_name: editEndName.trim() || undefined,
        ...(editStartAddress.trim() ? { start_address: editStartAddress.trim() } : {}),
        ...(editEndAddress.trim() ? { end_address: editEndAddress.trim() } : {}),
      };

      const updated = await api.updateTrip(editingTrip.id, payload);
      toast.success('Trip updated!');

      // Update in local state
      queryClient.setQueryData<TripsResponse>(['geo-trips'], (prev) =>
        prev
          ? { ...prev, trips: prev.trips.map((t) => (t.id === updated.id ? { ...t, ...updated } : t)) }
          : prev,
      );
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

  // Share trip with manually-chosen riders
  const handleOpenShare = (trip: Trip) => {
    trigger('light');
    if (!isTripOwner(trip, currentUsername)) {
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
      queryClient.setQueryData<TripsResponse>(['geo-trips'], (prev) =>
        prev
          ? { ...prev, trips: prev.trips.map((t) => (t.id === updated.id ? { ...t, ...updated } : t)) }
          : prev,
      );
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

  return (
    <div className="space-y-6 max-w-7xl mx-auto pb-12">
      {/* A phone that stopped reporting must never look like "nobody moved". */}
      <SensorStatusBanner />

      {/* A failed fetch must not render as "no trips" — prefer a visible
          warning over a silent empty state. */}
      {loadError && (
        <div
          role="alert"
          data-testid="wander-load-error"
          className="bg-red-500/20 border border-red-500/30 rounded-xl p-3 text-red-400 text-sm flex items-center justify-between gap-3"
        >
          <span>Could not load trips and family locations.</span>
          <button
            onClick={refreshAll}
            className="underline text-xs shrink-0 min-h-11 pointer-coarse:min-h-11"
          >
            Retry
          </button>
        </div>
      )}

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
                Family presence, places, and trips — Inspired by Elijah&apos;s journey in the wilderness (1 Kings 17:3–6). Steps and workouts live on Health.
              </p>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={refreshAll}
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

        {/* Live GPS map renders whenever anyone is sharing, independent of HA presence */}
        <LiveFamilyMap
          height={320}
          focusUserId={familyMembers.find((m) => m.id === focusMember)?.name.toLowerCase() ?? null}
          zones={places}
        />

        {familyMembers.length === 0 ? (
          <div className="glass-panel p-6 rounded-2xl border border-white/5 text-center">
            <p className="text-sm text-slate-400">No family presence entities reporting yet.</p>
            <p className="text-xs text-slate-500 mt-1">Location tracking starts automatically once family members share their GPS from the mobile app.</p>
          </div>
        ) : (
          <>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {familyMembers.map((member) => (
              <div
                key={member.id}
                role="button"
                tabIndex={0}
                onClick={() => setFocusMember(member.id)}
                onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') setFocusMember(member.id); }}
                title="Show on the live map"
                className={`glass-card p-4 rounded-2xl border transition-all flex flex-col justify-between gap-3 shadow-lg cursor-pointer ${
                  focusMember === member.id ? 'border-purple-400/70 ring-1 ring-purple-400/40' : 'border-white/5 hover:border-purple-500/30'
                }`}
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
                        {member.accuracy != null && (
                          <span className="text-[10px] text-slate-500" title="GPS accuracy">
                            ±{Math.round(member.accuracy)} m
                          </span>
                        )}
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

                {(member.inZones?.length ?? 0) > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {member.inZones!.slice(0, 3).map((zone) => (
                      <span
                        key={zone}
                        className="text-[10px] px-1.5 py-0.5 rounded-full bg-indigo-500/15 text-indigo-300 border border-indigo-500/25"
                      >
                        {zone.replace(/^zone\./, '').replace(/_/g, ' ')}
                      </span>
                    ))}
                  </div>
                )}

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
                  {member.lastUpdated && (
                    <span className="text-[10px] text-slate-500" title={new Date(member.lastUpdated).toLocaleString()}>
                      {relativeTime(member.lastUpdated)}
                    </span>
                  )}
                </div>
              </div>
            ))}
            </div>
          </>
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
            aria-pressed={filterTab === 'all'}
            className={`px-3 py-2 rounded-lg text-xs font-medium transition-all min-h-11 pointer-coarse:min-h-11 ${
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
            aria-pressed={filterTab === 'mine'}
            className={`px-3 py-2 rounded-lg text-xs font-medium transition-all min-h-11 pointer-coarse:min-h-11 ${
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
      ) : displayedTrips.length === 0 && !loadError ? (
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
            const canEdit = isTripOwner(trip, currentUsername);
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
                      <span>{displayName(trip)}</span>
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
                  hidden={Boolean(tripLocations)}
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
                <span>GPS Verified Trip</span>
              </div>
              <p className="text-slate-400">
                Distance ({editingTrip.distance_miles} miles) comes from recorded GPS and can&apos;t be altered. Place names can be edited, or picked from nearby stores, restaurants, and zones — the street address stays stored with the coordinates.
              </p>
            </div>

            {/* Route Locations: editable display names + nearby place picker */}
            <div className="space-y-4">
              <label className="text-xs font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-1.5">
                <MapPinned size={14} className="text-purple-400" />
                Route Locations
              </label>
              {[
                { target: 'start' as const, label: 'Starting Place', value: editStartName, setValue: setEditStartName, address: editStartAddress },
                { target: 'end' as const, label: 'Destination', value: editEndName, setValue: setEditEndName, address: editEndAddress },
              ].map((field) => (
                <div key={field.target} className="space-y-1.5 p-3 rounded-xl bg-slate-900/50 border border-white/5">
                  <p className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">{field.label}</p>
                  <input
                    type="text"
                    value={field.value}
                    onChange={(e) => field.setValue(e.target.value)}
                    placeholder={field.target === 'start' ? 'Starting place' : 'Destination'}
                    aria-label={field.label}
                    className="w-full bg-slate-900/90 border border-white/10 rounded-xl px-3.5 py-2 text-sm text-white focus:outline-none focus:border-purple-500"
                  />
                  {field.address && (
                    <p className="text-[11px] text-slate-500 truncate" title={field.address}>
                      {field.address}
                    </p>
                  )}
                  <button
                    type="button"
                    onClick={() => void handleOpenSuggestions(field.target)}
                    disabled={suggestLoading && suggestTarget === field.target}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs bg-slate-800/80 border border-white/10 text-slate-200 hover:border-purple-500/50 hover:text-white transition-colors disabled:opacity-50"
                  >
                    {suggestLoading && suggestTarget === field.target ? (
                      <Loader2 size={13} className="animate-spin" />
                    ) : (
                      <MapPinned size={13} />
                    )}
                    Pick nearby place
                  </button>
                  {suggestTarget === field.target && (
                    <div className="flex flex-wrap gap-2 pt-1" data-testid={`suggestions-${field.target}`}>
                      {suggestLoading && <p className="text-[11px] text-slate-500">Looking around…</p>}
                      {!suggestLoading && suggestions.length === 0 && (
                        <p className="text-[11px] text-slate-500">
                          No named places nearby — keep the address or type a name.
                        </p>
                      )}
                      {suggestions.map((s) => (
                        <button
                          key={`${s.kind}-${s.name}`}
                          type="button"
                          onClick={() => applySuggestion(s)}
                          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs bg-slate-800/80 border border-white/10 text-slate-200 hover:border-purple-500/50 hover:text-white transition-colors max-w-full"
                        >
                          <span className="truncate">{s.name}</span>
                          <span className="text-slate-500 shrink-0">
                            · {s.kind}
                            {formatDistanceMeters(s.distance_m)}
                          </span>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              ))}
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

export default Wander;
