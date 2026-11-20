import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  Plus, Sun, Droplets, Leaf, X, ChevronRight,
  ShieldAlert, RotateCw, Flower, User, Zap, Settings, Search,
  Scissors, ShoppingCart,
} from 'lucide-react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'react-hot-toast';
import { api } from '../services/api';
import { saveShoppingList, readShoppingList } from '../lib/shoppingListStore';
import { useAuth } from '../context/AuthContext';
import type { GardenPlant, GardenTodayTask, GardenTodaySummary, GardenSpecies, GardenLocation, GardenWeatherSummary, GardenCustomSpeciesIn, HarvestLogIn, HarvestReadyItem } from '../types/api';

/* ========================= Garden Page ========================= */

export default function Garden() {
  const { user } = useAuth();
  const isParent = Boolean(user?.is_admin || user?.family_role === 'mother' || user?.family_role === 'father');
  const [tab, setTab] = useState<'today' | 'plants' | 'planner'>('today');
  const [mineOnly, setMineOnly] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [digestOpen, setDigestOpen] = useState(false);

  const todayQuery = useQuery({
    queryKey: ['garden-today', mineOnly ? 'mine' : 'all'],
    queryFn: () => api.gardenToday(),
    staleTime: 60_000,
  });

  const profileQuery = useQuery({
    queryKey: ['garden-profile'],
    queryFn: () => api.gardenGetProfile(),
    staleTime: 300_000,
  });

  const plantsQuery = useQuery({
    queryKey: ['garden-plants'],
    queryFn: () => api.gardenListPlants(),
    staleTime: 60_000,
  });

  const locationsQuery = useQuery({
    queryKey: ['garden-locations'],
    queryFn: () => api.gardenListLocations(),
    staleTime: 300_000,
  });

  const streaksQuery = useQuery({
    queryKey: ['garden-streaks'],
    queryFn: () => api.gardenStreaks(),
    staleTime: 300_000,
  });

  // Phase 4: harvest
  const harvestReadyQuery = useQuery({
    queryKey: ['garden-harvest-ready'],
    queryFn: () => api.gardenHarvestReady(),
    staleTime: 60_000,
    retry: false,
  });
  const [harvestOpen, setHarvestOpen] = useState<HarvestReadyItem | null>(null);

  const today = todayQuery.data;
  const plants = plantsQuery.data ?? [];
  const locations = locationsQuery.data ?? [];
  const streaks = streaksQuery.data;
  const streak = streaks?.current_streak ?? 0;

  return (
    <div className="flex flex-col h-full w-full bg-slate-950 text-slate-200">
      <div className="flex-1 overflow-y-auto p-4 md:p-6">
        <div className="mb-6 flex flex-col md:flex-row md:items-center md:justify-between gap-4">
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold text-white">Garden</h1>
            {streak > 0 && (
              <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-amber-500/10 border border-amber-500/20">
                <Zap size={12} className="text-amber-400" />
                <span className="text-xs font-semibold text-amber-300">{streak}-day streak</span>
              </div>
            )}
          </div>
          <p className="text-slate-400 text-sm">
            {plants.length} plant{plants.length !== 1 ? 's' : ''} · {today?.due_count ?? 0} due today
          </p>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => setTab('today')}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${
                tab === 'today'
                  ? 'bg-purple-600 text-white shadow-lg shadow-purple-500/20'
                  : 'bg-white/5 text-slate-300 hover:bg-white/10'
              }`}
            >
              <Sun size={16} className="mr-2" /> Today
            </button>
            <div className="flex items-center gap-1 border border-white/10 rounded-lg overflow-hidden">
              <button
                type="button"
                onClick={() => setMineOnly(false)}
                className={`px-3 py-1.5 text-xs font-medium transition-all ${
                  !mineOnly
                    ? 'bg-purple-600/30 text-white'
                    : 'text-slate-300 hover:bg-white/10'
                }`}
              >
                All
              </button>
              <button
                type="button"
                onClick={() => setMineOnly(true)}
                className={`px-3 py-1.5 text-xs font-medium transition-all ${
                  mineOnly
                    ? 'bg-purple-600/30 text-white'
                    : 'text-slate-300 hover:bg-white/10'
                }`}
              >
                Mine
              </button>
            </div>
            <button
              type="button"
              onClick={() => setTab('plants')}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${
                tab === 'plants'
                  ? 'bg-purple-600 text-white shadow-lg shadow-purple-500/20'
                  : 'bg-white/5 text-slate-300 hover:bg-white/10'
              }`}
            >
              <Leaf size={16} className="mr-2" /> Plants
            </button>
            <button
              type="button"
              onClick={() => setTab('planner')}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${
                tab === 'planner'
                  ? 'bg-purple-600 text-white shadow-lg shadow-purple-500/20'
                  : 'bg-white/5 text-slate-300 hover:bg-white/10'
              }`}
            >
              <Scissors size={16} className="mr-2" /> Planner
            </button>
          </div>
        </div>

        {tab === 'today' ? (
          <TodayTab
            today={today}
            locations={locations}
            mineOnly={mineOnly}
            currentUser={user?.username ?? ''}
            weather={today?.weather ?? null}
            harvestReady={harvestReadyQuery.data?.ready}
            onLogHarvest={setHarvestOpen}
          />
        ) : tab === 'plants' ? (
          <PlantsTab plants={plants} locations={locations} mineOnly={mineOnly} setMineOnly={setMineOnly} currentUser={user?.username ?? ''} />
        ) : (
          <PlannerTab locations={locations} />
        )}

        <div className="mt-6 flex justify-end">
          <button
            type="button"
            onClick={() => setAddOpen(true)}
            className="flex items-center gap-2 px-5 py-2.5 bg-purple-600 hover:bg-purple-500 text-white rounded-xl text-sm font-medium transition-colors shadow-lg shadow-purple-500/25"
          >
            <Plus size={16} /> Add Plant
          </button>
          {isParent && (
            <button
              type="button"
              onClick={() => setDigestOpen(true)}
              className="ml-3 flex items-center gap-2 px-5 py-2.5 bg-white/10 hover:bg-white/20 text-slate-300 rounded-xl text-sm font-medium transition-colors border border-white/10"
            >
              <Settings size={16} className="mr-2" /> Digest Settings
            </button>
          )}
        </div>

        <AddPlantModal
          open={addOpen}
          onClose={() => setAddOpen(false)}
          locations={locations}
        />
        {isParent && (
          <DigestSettingsModal
            open={digestOpen}
            onClose={() => setDigestOpen(false)}
            initialDigestTime={profileQuery.data?.daily_digest_time ?? ''}
          />
        )}
        {harvestOpen && (
          <LogHarvestModal
            open={true}
            onClose={() => setHarvestOpen(null)}
            plant={harvestOpen}
          />
        )}
      </div>
    </div>
  );
}

/* ========================= Weather Banner (Phase 3) ========================= */

function WeatherBanner({ weather }: { weather: GardenWeatherSummary | null }) {
  if (!weather) return null;
  const { rain_skipped, frost_alerts, heat_advisory, wind_advisory, summary } = weather;
  const relevant =
    rain_skipped.length > 0 ||
    frost_alerts.length > 0 ||
    heat_advisory !== null ||
    wind_advisory !== null;
  if (!relevant) return null;

  return (
    <div className="mb-4 rounded-xl border border-amber-500/30 bg-amber-500/10 p-4">
      {summary && (
        <p className="text-xs text-amber-300 mb-2">{summary}</p>
      )}
      {rain_skipped.length > 0 && (
        <div className="mb-3">
          <h3 className="text-xs font-semibold text-amber-400 mb-1">Rain skip — watering postponed</h3>
          <p className="text-sm text-amber-200 mb-1">
            {rain_skipped.map((r) => r.nickname).join(", ")} — skipped ({rain_skipped[0]?.rain_in ?? 0} in rain).
            Next care due tomorrow.
          </p>
        </div>
      )}
      {frost_alerts.length > 0 && (
        <div className="mb-3">
          <h3 className="text-xs font-semibold text-amber-400 mb-1">Frost alert — cover or bring inside</h3>
          <p className="text-sm text-amber-200 mb-1">
            {frost_alerts.map((a) => a.advice).join(" ")}
          </p>
        </div>
      )}
      {heat_advisory && (
        <div className="mb-3">
          <h3 className="text-xs font-semibold text-amber-400 mb-1">Heat advisory</h3>
          <p className="text-sm text-amber-200 mb-1">
            High {heat_advisory.high_f}°F forecast — {heat_advisory.advice}
          </p>
        </div>
      )}
      {wind_advisory && (
        <div className="mb-3">
          <h3 className="text-xs font-semibold text-amber-400 mb-1">High wind alert</h3>
          <p className="text-sm text-amber-200 mb-1">
            {wind_advisory.advice} ({wind_advisory.names.join(', ')})
          </p>
        </div>
      )}
    </div>
  );
}

/* ========================= Today Tab ========================= */

function TodayTab({
  today,
  locations,
  mineOnly,
  currentUser,
  weather,
  harvestReady,
  onLogHarvest,
}: {
  today: GardenTodaySummary | undefined;
  locations: GardenLocation[];
  mineOnly: boolean;
  currentUser: string;
  weather: GardenWeatherSummary | null;
  harvestReady: HarvestReadyItem[] | undefined;
  onLogHarvest: (item: HarvestReadyItem) => void;
}) {
  const queryClient = useQueryClient();

  const logCareMutation = useMutation({
    mutationFn: ({ plantId, action }: { plantId: string; action: string }) =>
      api.gardenLogCare(plantId, { action }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['garden-today'] });
      queryClient.invalidateQueries({ queryKey: ['garden-plants'] });
      toast.success('Care logged');
    },
    onError: () => toast.error('Failed to log care'),
  });

  const snoozeMutation = useMutation({
    mutationFn: ({ plantId }: { plantId: string }) => api.gardenSnooze(plantId, { still_damp: true }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['garden-today'] });
      toast.success('Snoozed until tomorrow');
    },
    onError: () => toast.error('Failed to snooze'),
  });

  const waterAllMutation = useMutation({
    mutationFn: (locationId: string) => api.gardenWaterAll(locationId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['garden-today'] });
      queryClient.invalidateQueries({ queryKey: ['garden-plants'] });
      toast.success('Room watered');
    },
    onError: () => toast.error('Failed to water room'),
  });

  if (!today) return <div className="text-slate-400">Loading garden today…</div>;

  const renderTask = (task: GardenTodayTask) => (
    <div
      key={task.plant_id + '-' + task.action}
      className="flex items-center justify-between gap-3 px-4 py-3 rounded-xl bg-white/5 border border-white/10 mb-2"
    >
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1">
          <span className="font-semibold text-white">{task.nickname || task.common_name}</span>
          {task.common_name && task.nickname && (
            <span className="text-xs text-slate-400">{task.common_name}</span>
          )}
        </div>
        <div className="flex items-center gap-2 text-xs text-slate-400 mb-1">
          <Droplets size={12} />
          <span>{task.care_cue}</span>
          {task.location_name && (
            <span className="ml-2">· {task.location_name}</span>
          )}
          {task.toxicity_level && task.toxicity_level !== 'non_toxic' && (
            <span className="ml-2 px-1.5 py-0.5 rounded text-[10px] bg-amber-500/10 text-amber-400 border border-amber-500/20">
              {task.toxicity_level}
            </span>
          )}
          {task.assigned_to && (
            <span className="ml-2 px-1.5 py-0.5 rounded text-[10px] bg-purple-500/10 text-purple-400 border border-purple-500/20">
              {task.assigned_to}
            </span>
          )}
        </div>
        {task.overdue ? (
          <div className="text-xs text-red-400 font-medium mb-1">Overdue · {task.next_due_at}</div>
        ) : (
          <div className="text-xs text-emerald-400">Due {task.next_due_at}</div>
        )}
      </div>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => logCareMutation.mutate({ plantId: task.plant_id, action: task.action })}
          className="px-3 py-1.5 bg-emerald-500/10 text-emerald-400 rounded-lg hover:bg-emerald-500/20 border border-emerald-500/20 text-xs font-medium transition-colors pointer-coarse:min-h-11"
        >
          Done
        </button>
        <button
          type="button"
          onClick={() => snoozeMutation.mutate({ plantId: task.plant_id })}
          className="px-3 py-1.5 bg-amber-500/10 text-amber-400 rounded-lg hover:bg-amber-500/20 border border-amber-500/20 text-xs font-medium transition-colors pointer-coarse:min-h-11"
        >
          Still damp
        </button>
      </div>
    </div>
  );

  const byRoom = today.by_room ?? {};
  const roomEntries = Object.entries(byRoom);

  // Phase 4: Ready to Harvest strip
  const readyItems = harvestReady ?? [];
  const readyNotInCooldown = readyItems.filter((r) => !r.in_cooldown);

  // Apply mineOnly filter to room entries: show plants assigned to current user or unassigned.
  const filteredEntries = mineOnly
    ? roomEntries.map(([roomName, tasks]) => [
        roomName,
        (tasks as GardenTodayTask[]).filter((t) => t.assigned_to === currentUser || !t.assigned_to),
      ] as [string, GardenTodayTask[]])
    : roomEntries.map(([name, tasks]) => [name, tasks as GardenTodayTask[]] as [string, GardenTodayTask[]]);

  return (
    <div className="space-y-6">
      <WeatherBanner weather={weather} />

      {/* Phase 4: Ready to Harvest strip */}
      {readyItems.length > 0 && (
        <section className="mb-6">
          <div className="flex items-center justify-between gap-2 mb-3">
            <div className="flex items-center gap-2">
              <Scissors size={18} className="text-emerald-400" />
              <h2 className="text-sm font-semibold text-white uppercase tracking-wider">
                Ready to Harvest
              </h2>
              <span className={`ml-auto text-xs px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400`}>
                {readyItems.length}
              </span>
            </div>
            {readyNotInCooldown.length > 0 && (
              <button
                type="button"
                onClick={() => onLogHarvest(readyNotInCooldown[0])}
                className="px-3 py-1.5 bg-emerald-500/10 text-emerald-400 rounded-lg hover:bg-emerald-500/20 border border-emerald-500/20 text-xs font-medium transition-colors pointer-coarse:min-h-11"
              >
                <Leaf size={12} /> Log Harvest
              </button>
            )}
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {readyItems.map((item) => (
              <div
                key={item.plant_id}
                className={`p-3 rounded-xl border ${item.in_cooldown ? 'border-amber-500/30 bg-amber-500/5' : 'border-emerald-500/30 bg-emerald-500/5'}`}
              >
                <div className="flex items-start justify-between mb-1">
                  <div className="flex items-center gap-2">
                    <span className="font-semibold text-white">{item.nickname || item.common_name}</span>
                    {item.in_cooldown && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-400 border border-amber-500/30">
                        Cooldown
                      </span>
                    )}
                  </div>
                  <span className={`text-[10px] px-1.5 py-0.5 rounded ${item.in_cooldown ? 'bg-amber-500/20 text-amber-400 border border-amber-500/30' : 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'}`}>
                    {item.category}
                  </span>
                </div>
                <p className="text-xs text-slate-400 mb-2">{item.common_name}</p>
                {item.culinary_aliases.length > 0 && (
                  <p className="text-xs text-slate-500 mb-2">
                    {item.culinary_aliases.slice(0, 3).join(', ')}{item.culinary_aliases.length > 3 ? '…' : ''}
                  </p>
                )}
                {item.in_cooldown && item.cooldown_until && (
                  <p className="text-xs text-amber-300 mb-2">
                    Ready again {new Date(item.cooldown_until).toLocaleDateString()}
                  </p>
                )}
                {!item.in_cooldown && (
                  <button
                    type="button"
                    onClick={() => onLogHarvest(item)}
                    className="w-full px-3 py-1.5 bg-emerald-500/10 text-emerald-400 rounded-lg hover:bg-emerald-500/20 border border-emerald-500/20 text-xs font-medium transition-colors"
                  >
                    Log Harvest
                  </button>
                )}
              </div>
            ))}
          </div>
        </section>
      )}

      {today.due_count === 0 && readyItems.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-slate-400">
          <Flower size={48} className="mb-4 opacity-50" />
          <h3 className="text-lg font-medium text-slate-300">All caught up</h3>
          <p className="text-sm text-slate-500 mt-1">No plants need care today. Add some plants to get started.</p>
        </div>
      ) : (
        <>
          {filteredEntries.map(([roomName, tasks]) => {
            if (tasks.length === 0) return null;
            const room = locations.find((l) => l.name === roomName);
            return (
              <section key={roomName} className="mb-6">
                <div className="flex items-center justify-between gap-2 mb-3">
                  <div className="flex items-center gap-2">
                    <Droplets size={18} className="text-emerald-400" />
                    <h2 className="text-sm font-semibold text-white uppercase tracking-wider">
                      {roomName}
                    </h2>
                    <span className={`ml-auto text-xs px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400`}>
                      {tasks.length}
                    </span>
                  </div>
                  {room?.id && (
                    <button
                      type="button"
                      onClick={() => waterAllMutation.mutate(room.id)}
                      className="px-3 py-1.5 bg-emerald-500/10 text-emerald-400 rounded-lg hover:bg-emerald-500/20 border border-emerald-500/20 text-xs font-medium transition-colors pointer-coarse:min-h-11"
                    >
                      <Droplets size={12} /> Water room
                    </button>
                  )}
                </div>
                <div className="space-y-0">{tasks.map(renderTask)}</div>
              </section>
            );
          })}
        </>
      )}
    </div>
  );
}

/* ========================= Plants Tab ========================= */

function PlantsTab({ plants, locations, mineOnly, setMineOnly, currentUser }: { plants: GardenPlant[]; locations: GardenLocation[]; mineOnly: boolean; setMineOnly: (v: boolean) => void; currentUser: string }) {
  const [filter, setFilter] = useState<'all' | 'houseplant' | 'herb' | 'outdoor'>('all');

  const locationMap = new Map(locations.map((l) => [l.id, l.name]));

  const FILTERS: Array<'all' | 'houseplant' | 'herb' | 'outdoor'> = ['all', 'houseplant', 'herb', 'outdoor'];

  let filtered = plants.filter((p) => {
    if (mineOnly && p.assigned_to !== currentUser && p.assigned_to != null && p.assigned_to !== '') return false;
    if (filter === 'outdoor') return ['vegetable', 'fruit', 'flower'].includes(p.category);
    if (filter !== 'all' && p.category !== filter) return false;
    return true;
  });

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap gap-2">
        {FILTERS.map((f) => (
          <button
            key={f}
            type="button"
            onClick={() => setFilter(f)}
            className={`px-3 py-1.5 rounded-full text-sm font-medium transition-all ${
              filter === f
                ? 'bg-purple-600 text-white'
                : 'bg-white/5 text-slate-300 hover:bg-white/10'
            }`}
          >
            {f.charAt(0).toUpperCase() + f.slice(1)}
          </button>
        ))}
        <button
          type="button"
          onClick={() => setMineOnly(!mineOnly)}
          className={`px-3 py-1.5 rounded-full text-sm font-medium transition-all ${
            mineOnly
              ? 'bg-purple-600 text-white'
              : 'bg-white/5 text-slate-300 hover:bg-white/10'
          }`}
        >
          <User size={14} className="mr-1.5" />
          {mineOnly ? 'Mine' : 'All'}
        </button>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {filtered.length === 0 ? (
          <div className="col-span-3 flex flex-col items-center justify-center py-16 text-slate-400">
            <Flower size={48} className="mb-4 opacity-50" />
            <h3 className="text-lg font-medium text-slate-300">No plants yet</h3>
            <p className="text-sm text-slate-500 mt-1">Add your first plant to start tracking care.</p>
          </div>
        ) : (
          filtered.map((plant) => (
            <PlantCard key={plant.id} plant={plant} locationName={locationMap.get(plant.location_id || '')} />
          ))
        )}
      </div>
    </div>
  );
}

function PlantCard({ plant, locationName }: { plant: GardenPlant; locationName: string | undefined }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const assignMutation = useMutation({
    mutationFn: (body: { assigned_to: string | null; skylight_sync_enabled: boolean }) =>
      api.gardenAssignPlant(plant.id, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['garden-plants'] });
      queryClient.invalidateQueries({ queryKey: ['garden-today'] });
      toast.success('Assignment updated');
    },
    onError: () => toast.error('Failed to update assignment'),
  });

  const getStatusColor = () => {
    switch (plant.status) {
      case 'active': return 'text-emerald-400';
      case 'dormant': return 'text-amber-400';
      case 'archived': return 'text-slate-500';
      default: return 'text-slate-400';
    }
  };

  return (
    <div
      onClick={(e) => {
        // Don't navigate if the click came from the Skylight checkbox.
        if (!(e.target as HTMLElement).closest('input[type="checkbox"]')) {
          navigate(`/garden/plants/${plant.id}`);
        }
      }}
      className="group bg-white/5 border border-white/10 rounded-xl p-4 hover:border-purple-500/50 hover:bg-white/10 transition-colors cursor-pointer pointer-coarse:min-h-[140px]"
    >
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-2">
          <Flower size={18} className="text-slate-400 shrink-0" />
          <h3 className="font-semibold text-white truncate max-w-[200px]">{plant.nickname || plant.species_ref}</h3>
        </div>
        <span className={`text-[10px] px-2 py-0.5 rounded ${getStatusColor()} border`}>
          {plant.status}
        </span>
      </div>
      <p className="text-xs text-slate-400 mb-3 truncate">{plant.species?.common_name || plant.species_ref}</p>
      <div className="flex items-center justify-between text-xs text-slate-500 mb-3">
        <span>{locationName || 'Unplaced'}</span>
        <span>{plant.category}</span>
      </div>
      {plant.assigned_to && (
        <div className="flex items-center gap-2 text-xs text-slate-400 mb-3">
          <User size={12} />
          <span>Assigned: {plant.assigned_to}</span>
        </div>
      )}
      <div className="flex items-center gap-2 text-xs mt-3 pt-3 border-t border-white/5">
        <label className="text-slate-500 flex items-center gap-1 cursor-pointer">
          <input
            type="checkbox"
            checked={plant.skylight_sync_enabled}
            onChange={(e) => {
              e.stopPropagation();
              assignMutation.mutate({
                assigned_to: plant.assigned_to ?? null,
                skylight_sync_enabled: e.target.checked,
              });
            }}
            className="rounded bg-slate-800 border-slate-600 text-purple-600 focus:ring-purple-500"
          />
          <span>Skylight sync</span>
        </label>
      </div>
      <div className="flex items-center gap-2 pt-3 border-t border-white/5">
        <Droplets size={14} className="text-slate-400" />
        <span className="text-sm text-slate-300">Next care due</span>
      </div>
    </div>
  );
}

/* ========================= Planner Tab (Phase 5) ========================= */

function PlannerTab({
  locations,
}: {
  locations: GardenLocation[];
}) {
  const queryClient = useQueryClient();

  // Zone detection
  const zoneQuery = useQuery({
    queryKey: ['garden-zone'],
    queryFn: () => api.gardenDetectZone(),
    staleTime: 300_000,
    retry: false,
  });
  const zone = zoneQuery.data;

  // Pantry goals
  const pantryQuery = useQuery({
    queryKey: ['garden-pantry-goals'],
    queryFn: () => api.gardenListPantryGoals(),
    staleTime: 300_000,
    retry: false,
  });
  const pantryGoals = pantryQuery.data?.goals ?? [];

  // Plans
  const plansQuery = useQuery({
    queryKey: ['garden-plans'],
    queryFn: () => api.gardenListPlans(),
    staleTime: 300_000,
    retry: false,
  });
  const plans = plansQuery.data?.plans ?? [];

  const [newGoalItem, setNewGoalItem] = useState('');
  const [newGoalQty, setNewGoalQty] = useState('');
  const [activePlan, setActivePlan] = useState<string | null>(plans.length ? plans[0].id : null);

  const addPantryGoalMutation = useMutation({
    mutationFn: async (body: { item: string; target_qty: number; unit: string; method: string }) => {
      return api.gardenCreatePantryGoal(body);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['garden-pantry-goals'] });
      setNewGoalItem('');
      setNewGoalQty('');
      toast.success('Pantry goal added');
    },
    onError: () => toast.error('Failed to add pantry goal'),
  });

  const detectZoneMutation = useMutation({
    mutationFn: () => api.gardenDetectZone(),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['garden-zone'] });
      toast.success(`Zone detected: ${data.zone}`);
    },
    onError: () => toast.error('Zone detection unavailable'),
  });

  const bedLocations = locations.filter((l) =>
    ['raised_bed', 'in_ground', 'container', 'greenhouse'].includes(l.kind),
  );

  return (
    <div className="space-y-6">
      {/* Zone & Frost Header */}
      <div className="bg-white/5 border border-white/10 rounded-2xl p-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-white uppercase tracking-wider">
            Household Climate
          </h3>
          <button
            type="button"
            onClick={() => detectZoneMutation.mutate()}
            disabled={detectZoneMutation.isPending}
            className="px-3 py-1.5 bg-white/5 text-slate-300 hover:bg-white/10 border border-white/10 rounded-lg text-xs transition-colors pointer-coarse:min-h-11"
          >
            {detectZoneMutation.isPending ? 'Detecting…' : 'Auto-detect Zone'}
          </button>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
          <div className="bg-white/5 rounded-lg p-2">
            <div className="text-slate-400">USDA Zone</div>
            <div className="text-white font-bold">
              {zone?.zone ?? '—'}{zone?.zone_source === 'auto' ? ' (auto)' : ''}
            </div>
          </div>
          <div className="bg-white/5 rounded-lg p-2">
            <div className="text-slate-400">Last Spring Frost</div>
            <div className="text-white font-bold">
              {zone?.last_spring_frost_mmdd ?? '—'}
            </div>
          </div>
          <div className="bg-white/5 rounded-lg p-2">
            <div className="text-slate-400">First Fall Frost</div>
            <div className="text-white font-bold">
              {zone?.first_fall_frost_mmdd ?? '—'}
            </div>
          </div>
          <div className="bg-white/5 rounded-lg p-2">
            <div className="text-slate-400">Season Length</div>
            <div className="text-white font-bold">
              {zone?.season_length_days ?? '—'} days
            </div>
          </div>
        </div>
        {zone?.message && (
          <p className="text-xs text-slate-400 mt-2">{zone.message}</p>
        )}
      </div>

      {/* Pantry Goals */}
      <div className="bg-white/5 border border-white/10 rounded-2xl p-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-white uppercase tracking-wider">
            Pantry Goals
          </h3>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-4 gap-3 mb-4">
          <label className="flex flex-col gap-1">
            <span className="text-[10px] text-slate-400">Item</span>
            <input
              type="text"
              value={newGoalItem}
              onChange={(e) => setNewGoalItem(e.target.value)}
              placeholder="e.g. Marinara sauce"
              className="px-3 py-2 bg-white/5 border border-white/10 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-purple-500 text-sm"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[10px] text-slate-400">Qty</span>
            <input
              type="number"
              value={newGoalQty}
              onChange={(e) => setNewGoalQty(e.target.value)}
              placeholder="Qty"
              className="px-3 py-2 bg-white/5 border border-white/10 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-purple-500 text-sm w-20"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[10px] text-slate-400">Unit</span>
            <select
              value="quarts"
              onChange={() => {}}
              className="px-3 py-2 bg-white/5 border border-white/10 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-purple-500 text-sm"
            >
              <option>quarts</option>
              <option>pints</option>
              <option>lbs</option>
            </select>
          </label>
          <button
            type="button"
            onClick={() => {
              if (!newGoalItem || !newGoalQty) return;
              addPantryGoalMutation.mutate({
                item: newGoalItem,
                target_qty: parseFloat(newGoalQty),
                unit: 'quarts',
                method: 'water_bath_canning',
              });
            }}
            disabled={addPantryGoalMutation.isPending || !newGoalItem || !newGoalQty}
            className="px-4 py-2 rounded-lg bg-purple-600 text-white hover:bg-purple-500 disabled:opacity-50 text-sm pointer-coarse:min-h-11"
          >
            {addPantryGoalMutation.isPending ? 'Adding…' : 'Add Goal'}
          </button>
        </div>
        <div className="space-y-2">
          {pantryGoals.length === 0 ? (
            <p className="text-xs text-slate-500 italic">No pantry goals yet.</p>
          ) : (
            pantryGoals.map((g) => (
              <div
                key={g.id}
                className="flex items-center justify-between px-3 py-2 bg-white/5 rounded-lg border border-white/5"
              >
                <div>
                  <div className="text-sm text-white font-medium">{g.item}</div>
                  <div className="text-xs text-slate-400">
                    {g.target_qty} {g.unit} via {g.method} ({g.season_year})
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => api.gardenDeletePantryGoal(g.id).then(() => {
                    queryClient.invalidateQueries({ queryKey: ['garden-pantry-goals'] });
                    toast.success('Goal removed');
                  })}
                  className="text-xs text-red-400 hover:text-red-300 underline"
                >
                  Remove
                </button>
              </div>
            ))
          )}
        </div>
      </div>

      {/* Plans */}
      <div className="bg-white/5 border border-white/10 rounded-2xl p-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-white uppercase tracking-wider">
            Seasonal Plans
          </h3>
        </div>
        {plans.length === 0 ? (
          <p className="text-xs text-slate-500 italic">No seasonal plans yet.</p>
        ) : (
          <div className="space-y-2">
            {plans.map((p) => (
              <div
                key={p.id}
                className={`flex items-center justify-between px-3 py-2 rounded-lg border border-white/5 ${
                  activePlan === p.id ? 'bg-emerald-500/10 border-emerald-500/20' : 'bg-white/5'
                }`}
                onClick={() => setActivePlan(p.id)}
              >
                <div>
                  <div className="text-sm text-white font-medium">{p.name}</div>
                  <div className="text-xs text-slate-400">
                    {p.season} {p.year} · {p.mode} · {p.family_size} family
                  </div>
                </div>
                <span className={`text-xs px-1.5 py-0.5 rounded ${
                  p.status === 'active' ? 'bg-emerald-500/20 text-emerald-400' : 'bg-slate-600/20 text-slate-400'
                }`}>
                  {p.status}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Bed Map (simplified 2D view) */}
      {bedLocations.length > 0 && (
        <div className="bg-white/5 border border-white/10 rounded-2xl p-4">
          <h3 className="text-sm font-semibold text-white uppercase tracking-wider mb-3">
            Yard Map
          </h3>
          <div className="flex flex-col sm:flex-row gap-4">
            {bedLocations.map((bed) => (
              <div
                key={bed.id}
                className="flex flex-col items-center gap-2"
              >
                <div className="relative w-32 h-24 border-2 border-white/20 rounded-lg bg-white/5 overflow-hidden">
                  <div className="absolute top-2 left-2 text-[10px] text-white/70">
                    {bed.name}
                  </div>
                  <div className="absolute bottom-2 right-2 text-[10px] text-white/70">
                    {`${bed.width_ft ?? 4}' × ${bed.length_ft ?? 8}'`}
                  </div>
                  {/* Simple grid cells */}
                  <div className="absolute inset-0 grid grid-cols-4 grid-rows-8 gap-0.5 p-1">
                    {[...Array(32)].map((_, i) => (
                      <div
                        key={i}
                        className="w-full h-full bg-emerald-500/10 border border-white/10 rounded"
                      />
                    ))}
                  </div>
                </div>
                <div className="text-xs text-slate-400 text-center">
                  {bed.kind.replace('_', ' ')}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/* ========================= Add Supplies to Shopping List (Phase 4) ========================= */

/** Garden/preservation supplies to offer adding to the shopping list. */
const GARDEN_SUPPLIES = [
  'Potting mix',
  'Seeds',
  'Compost',
  'Fertilizer',
  'Mulch',
  'Plant labels',
  'Watering can',
  'Pruning shears',
];

async function addSuppliesToShoppingList(plant: GardenPlant) {
  const species = plant.species;
  const supplies = [...GARDEN_SUPPLIES];
  if (plant.category === 'herb' && species?.herb_preservation_tips) {
    supplies.push('Herb drying supplies');
  }
  if (['vegetable', 'fruit'].includes(plant.category)) {
    supplies.push('Canning jars', 'Freezer bags');
  }

  try {
    const existing = await readShoppingList();
    let content = '';
    if (existing.exists && existing.text) {
      content = existing.text;
    }
    const lines = content.split('\n');
    const already = new Set(lines.map(l => l.replace(/^-\s*\[[ xX]\]\s*/, '').trim()).filter(Boolean));
    const toAdd = supplies.filter(s => !already.has(s));
    if (toAdd.length === 0) {
      toast.success('All supplies already on the list');
      return;
    }
    const newLines = [...lines.filter(l => l.trim()), ...toAdd.map(s => `- [ ] ${s}`), ''];
    await saveShoppingList(newLines.join('\n'), existing.exists);
    toast.success(`Added ${toAdd.length} supply(ies) to shopping list`);
  } catch {
    toast.error('Could not add supplies to shopping list');
  }
}

/* ========================= Plant Detail ========================= */

export function PlantDetail() {
  const { id } = useParams();
  const { user } = useAuth();
  const isParent = Boolean(user?.is_admin || user?.family_role === 'mother' || user?.family_role === 'father');
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [harvestOpen, setHarvestOpen] = useState(false);

  const plantQuery = useQuery({
    queryKey: ['garden-plant', id],
    queryFn: () => {
      return api.gardenListPlants().then((plants) => plants.find((p) => p.id === id));
    },
    enabled: !!id,
  });

  const plant = plantQuery.data;

  const logsQuery = useQuery({
    queryKey: ['garden-plant-logs', id],
    queryFn: () => api.gardenGetPlantLogs(id!),
    enabled: !!id,
  });

  const logs = logsQuery.data?.logs ?? [];

  // Phase 4: harvest log for this plant.
  const harvestsQuery = useQuery({
    queryKey: ['garden-harvests', id],
    queryFn: () => api.gardenListHarvests({ plant_id: id }),
    enabled: !!id,
  });
  const harvests = harvestsQuery.data?.harvests ?? [];

  if (!plant) return <div className="p-4 text-slate-400">Plant not found</div>;

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">{plant.nickname || plant.species_ref}</h1>
          <p className="text-slate-400 text-sm">{plant.species?.common_name || plant.species_ref}</p>
        </div>
        <button
          type="button"
          onClick={() => navigate('/garden')}
          className="px-4 py-2 rounded-lg bg-white/5 text-slate-300 hover:bg-white/10 border border-white/10"
        >
          <X size={16} className="mr-2" /> Back
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        <div className="bg-white/5 border border-white/10 rounded-xl p-4">
          <h3 className="text-sm font-semibold text-slate-300 mb-3 uppercase tracking-wider">Care Sheet</h3>
          <div className="space-y-3 text-sm text-slate-300">
            <div className="flex justify-between">
              <span>Watering:</span>
              <span className="text-white">{plant.species?.watering_method || '—'}</span>
            </div>
            <div className="flex justify-between">
              <span>Dryness cue:</span>
              <span className="text-white">{plant.species?.dryness_cue || '—'}</span>
            </div>
            <div className="flex justify-between">
              <span>Water quality:</span>
              <span className="text-white">{plant.species?.water_quality || '—'}</span>
            </div>
            <div className="flex justify-between">
              <span>Light:</span>
              <span className="text-white">{plant.species?.light_level || '—'}</span>
            </div>
            <div className="flex justify-between">
              <span>Misting:</span>
              <span className="text-white">{plant.species?.misting_rule || '—'}</span>
            </div>
            {plant.category === 'herb' && plant.species?.pinching_bolting_notes && (
              <div className="flex justify-between">
                <span>Pinching / Bolting:</span>
                <span className="text-emerald-400">{plant.species.pinching_bolting_notes}</span>
              </div>
            )}
            {plant.category === 'herb' && plant.species?.herb_preservation_tips && (
              <div className="flex justify-between">
                <span>Preservation:</span>
                <span className="text-amber-400">{plant.species.herb_preservation_tips}</span>
              </div>
            )}
          </div>
        </div>

        <div className="bg-white/5 border border-white/10 rounded-xl p-4">
          <h3 className="text-sm font-semibold text-slate-300 mb-3 uppercase tracking-wider">Toxicity</h3>
          <div className="flex items-center gap-2">
            <ShieldAlert size={16} />
            <span className="text-sm text-slate-300">
              {plant.species?.toxicity_level || 'Unknown'}{plant.species?.toxicity_notes ? ` — ${plant.species.toxicity_notes}` : ''}
            </span>
          </div>
          <div className="mt-3">
            <h4 className="text-xs font-semibold text-slate-500 mb-1">Common pests</h4>
            <p className="text-sm text-slate-300">{plant.species?.common_pests || '—'}</p>
          </div>
        </div>

        <div className="bg-white/5 border border-white/10 rounded-xl p-4">
          <h3 className="text-sm font-semibold text-slate-300 mb-3 uppercase tracking-wider">Pot & Placement</h3>
          <div className="space-y-3 text-sm text-slate-300">
            <div className="flex justify-between">
              <span>Pot:</span>
              <span className="text-white">{plant.pot_size_in} in {plant.pot_material || ''}</span>
            </div>
            <div className="flex justify-between">
              <span>Drainage:</span>
              <span className={plant.has_drainage ? 'text-emerald-400' : 'text-red-400'}>
                {plant.has_drainage ? 'Yes' : 'No — water sparingly'}
              </span>
            </div>
            <div className="flex justify-between">
              <span>Window:</span>
              <span className="text-white">{plant.window_direction || '—'}</span>
            </div>
            <div className="flex justify-between">
              <span>Distance:</span>
              <span className="text-white">{plant.distance_from_window != null ? plant.distance_from_window + ' ft' : '—'}</span>
            </div>
          </div>
        </div>
      </div>

      {isParent && (
        <div className="bg-white/5 border border-white/10 rounded-xl p-4">
          <h3 className="text-sm font-semibold text-slate-300 mb-3 uppercase tracking-wider">Assignment</h3>
          <div className="space-y-3">
            <div className="flex items-center gap-3">
              <label htmlFor="garden-assigned-to" className="text-xs text-slate-400 w-24">Assigned to</label>
              <select
                id="garden-assigned-to"
                value={plant.assigned_to || ''}
                onChange={(e) => {
                  api.gardenAssignPlant(plant.id, {
                    assigned_to: e.target.value || null,
                    skylight_sync_enabled: plant.skylight_sync_enabled,
                  }).then(() => {
                    queryClient.invalidateQueries({ queryKey: ['garden-plants'] });
                    queryClient.invalidateQueries({ queryKey: ['garden-today'] });
                    toast.success('Assignment updated');
                  }).catch(() => toast.error('Failed to update assignment'));
                }}
                className="flex-1 px-3 py-2 bg-white/5 border border-white/10 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-purple-500"
              >
                <option value="">Unassigned</option>
                <option value="mother">Mother</option>
                <option value="father">Father</option>
                <option value="adult_child">Adult Child</option>
                <option value="teen">Teen</option>
                <option value="child">Child</option>
              </select>
            </div>
            <div className="flex items-center gap-2">
              <label className="text-xs text-slate-400 cursor-pointer">
                <input
                  type="checkbox"
                  checked={plant.skylight_sync_enabled}
                  onChange={(e) => {
                    e.stopPropagation();
                    api.gardenAssignPlant(plant.id, {
                      assigned_to: plant.assigned_to ?? null,
                      skylight_sync_enabled: e.target.checked,
                    }).then(() => {
                      queryClient.invalidateQueries({ queryKey: ['garden-plants'] });
                      toast.success('Skylight sync updated');
                    }).catch(() => toast.error('Failed to update Skylight sync'));
                  }}
                  className="rounded bg-slate-800 border-slate-600 text-purple-600 focus:ring-purple-500"
                />
                <span>Opt-in Skylight chore mirror</span>
              </label>
            </div>
          </div>
        </div>
      )}

      <div className="bg-white/5 border border-white/10 rounded-xl p-4">
        <h3 className="text-sm font-semibold text-slate-300 mb-3 uppercase tracking-wider">Quick Care</h3>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {['water', 'fertilize', 'mist', 'rotate'].map((action) => (
            <button
              key={action}
              type="button"
              onClick={() => {
                api.gardenLogCare(plant.id, { action }).then(() => {
                  queryClient.invalidateQueries({ queryKey: ['garden-today'] });
                  queryClient.invalidateQueries({ queryKey: ['garden-plants'] });
                  queryClient.invalidateQueries({ queryKey: ['garden-plant-logs', id] });
                  toast.success(`${action} logged`);
                }).catch(() => toast.error('Failed to log care'));
              }}
              className="px-4 py-2 rounded-lg bg-white/5 text-slate-300 hover:bg-white/10 border border-white/10 text-sm transition-colors pointer-coarse:min-h-11"
            >
              <RotateCw size={14} className="mr-2" />
              {action.charAt(0).toUpperCase() + action.slice(1)}
            </button>
          ))}
          {['herb', 'vegetable', 'fruit'].includes(plant.category) && (
            <button
              type="button"
              onClick={() => setHarvestOpen(true)}
              className="flex items-center justify-between px-4 py-2 rounded-lg bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20 border border-emerald-500/20 text-sm transition-colors pointer-coarse:min-h-11"
            >
              <Scissors size={14} className="mr-2" />
              Log Harvest
              {plant.regrowth_cooldown_days && plant.last_harvested_at && (
                <span className="text-[10px] text-emerald-200 ml-2">
                  {plant.regrowth_cooldown_days}d cooldown
                </span>
              )}
            </button>
          )}
          <button
            type="button"
            onClick={() => addSuppliesToShoppingList(plant)}
            className="flex items-center justify-between px-4 py-2 rounded-lg bg-amber-500/10 text-amber-400 hover:bg-amber-500/20 border border-amber-500/20 text-sm transition-colors pointer-coarse:min-h-11"
          >
            <ShoppingCart size={14} className="mr-2" />
            Add Supplies to Shopping List
          </button>
        </div>
      </div>

      <div className="bg-white/5 border border-white/10 rounded-xl p-4">
        <h3 className="text-sm font-semibold text-slate-300 mb-3 uppercase tracking-wider">Recent Harvests</h3>
        <div className="h-48 overflow-y-auto space-y-2">
          {harvests.length === 0 ? (
            <p className="text-xs text-slate-500 italic">No harvests yet</p>
          ) : (
            harvests.map((h) => (
              <div key={h.id} className="flex items-center gap-2 text-xs text-slate-300 pb-1 border-b border-white/5">
                <span className="text-slate-500">{new Date(h.harvested_at).toLocaleDateString()}</span>
                <span className="text-white">{h.quantity} {h.unit}</span>
                <span className="ml-2 px-1 py-0.5 rounded text-[10px] bg-amber-500/10 text-amber-400 border border-amber-500/20">
                  {h.disposition}
                </span>
                {h.notes && <span className="ml-2 text-slate-400">— {h.notes}</span>}
              </div>
            ))
          )}
        </div>
      </div>

      <div className="bg-white/5 border border-white/10 rounded-xl p-4">
        <h3 className="text-sm font-semibold text-slate-300 mb-3 uppercase tracking-wider">Care Log</h3>
        <div className="h-64 overflow-y-auto space-y-2">
          {logs.length === 0 ? (
            <p className="text-xs text-slate-500 italic">No care logs yet</p>
          ) : (
            logs.map((log) => (
              <div key={log.id} className="flex items-center gap-2 text-xs text-slate-300 pb-1 border-b border-white/5">
                <span className="text-slate-500">{new Date(log.created_at).toLocaleDateString()}</span>
                <span className="text-white">{log.action}</span>
                {log.performed_by && <span className="ml-2 text-slate-500">by {log.performed_by}</span>}
                {log.notes && <span className="ml-2 text-slate-400">— {log.notes}</span>}
              </div>
            ))
          )}
        </div>
      </div>
      {harvestOpen && (
        <HarvestModal
          open={harvestOpen}
          onClose={() => setHarvestOpen(false)}
          plant={plant}
        />
      )}
    </div>
  );
}

/* ========================= Log Harvest Modal (Phase 4) ========================= */

function HarvestModal({
  open,
  onClose,
  plant,
}: {
  open: boolean;
  onClose: () => void;
  plant: GardenPlant;
}) {
  const [quantity, setQuantity] = useState('');
  const [unit, setUnit] = useState('count');
  const [disposition, setDisposition] = useState('fresh');
  const [notes, setNotes] = useState('');

  const saveMutation = useMutation({
    mutationFn: async () => {
      await api.gardenLogHarvest(plant.id!, {
        plant_id: plant.id!,
        quantity: parseFloat(quantity) || 1,
        unit,
        disposition,
        notes: notes || undefined,
      });
    },
    onSuccess: () => {
      onClose();
      setQuantity('');
      setUnit('count');
      setDisposition('fresh');
      setNotes('');
      toast.success('Harvest logged');
    },
    onError: () => toast.error('Failed to log harvest'),
  });

  const units = ['count', 'bunch', 'leaf', 'oz', 'lb', 'pint', 'quart', 'piece'];
  const dispositions = ['fresh', 'canned', 'frozen', 'dried', 'fermented', 'shared'];

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-white/10 border border-white/20 rounded-2xl p-6 w-full max-w-md" onClick={(e) => e.stopPropagation()}>
        <div className="flex justify-between items-center mb-6">
          <h2 className="text-xl font-bold text-white">Log Harvest</h2>
          <button type="button" onClick={onClose} className="text-slate-400 hover:text-white">
            <X size={20} />
          </button>
        </div>

        <div className="space-y-4">
          <div>
            <label htmlFor="detail-harvest-qty" className="block text-xs text-slate-400 mb-1">Quantity</label>
            <input
              id="detail-harvest-qty"
              type="number"
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
              placeholder="e.g. 2"
              className="w-full px-3 py-2 bg-white/5 border border-white/10 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-purple-500"
            />
          </div>
          <div>
            <label htmlFor="detail-harvest-unit" className="block text-xs text-slate-400 mb-1">Unit</label>
            <select
              id="detail-harvest-unit"
              value={unit}
              onChange={(e) => setUnit(e.target.value)}
              className="w-full px-3 py-2 bg-white/5 border border-white/10 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-purple-500"
            >
              {units.map((u) => (
                <option key={u} value={u}>{u}</option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="detail-harvest-disposition" className="block text-xs text-slate-400 mb-1">Disposition</label>
            <select
              id="detail-harvest-disposition"
              value={disposition}
              onChange={(e) => setDisposition(e.target.value)}
              className="w-full px-3 py-2 bg-white/5 border border-white/10 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-purple-500"
            >
              {dispositions.map((d) => (
                <option key={d} value={d}>{d}</option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="detail-harvest-notes" className="block text-xs text-slate-400 mb-1">Notes (optional)</label>
            <input
              id="detail-harvest-notes"
              type="text"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="e.g. first basil of the season"
              className="w-full px-3 py-2 bg-white/5 border border-white/10 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-purple-500"
            />
          </div>
          <p className="text-xs text-slate-500">
            Species: <span className="text-white">{plant.species?.common_name || plant.species_ref}</span>
          </p>

          <div className="mt-4 flex gap-3">
            <button
              type="button"
              onClick={onClose}
              className="flex-1 px-4 py-2.5 rounded-lg bg-white/5 text-slate-300 hover:bg-white/10 border border-white/10 disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => saveMutation.mutate()}
              disabled={!quantity || parseFloat(quantity) <= 0}
              className="flex-1 px-4 py-2.5 rounded-lg bg-emerald-500 text-white hover:bg-emerald-400 disabled:opacity-50"
            >
              {saveMutation.isPending ? 'Logging…' : 'Log Harvest'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ========================= Digest Settings Modal (parent+ only) ========================= */

function DigestSettingsModal({
  open,
  onClose,
  initialDigestTime,
}: {
  open: boolean;
  onClose: () => void;
  initialDigestTime: string;
}) {
  const [saving, setSaving] = useState(false);
  const [digestTime, setDigestTime] = useState(initialDigestTime);

  useEffect(() => {
    setDigestTime(initialDigestTime);
  }, [initialDigestTime]);

  const saveMutation = useMutation({
    mutationFn: async (time: string) => {
      await api.gardenUpdateProfile({ daily_digest_time: time });
    },
    onSuccess: () => {
      setSaving(false);
      toast.success('Digest time saved');
    },
    onError: () => {
      setSaving(false);
      toast.error('Failed to save digest time');
    },
  });

  const testMutation = useMutation({
    mutationFn: async () => {
      await api.gardenNoticesTest({});
    },
    onSuccess: () => {
      setSaving(false);
      toast.success('Test digest sent');
    },
    onError: () => {
      setSaving(false);
      toast.error('Failed to send test digest');
    },
  });

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-white/10 border border-white/20 rounded-2xl p-6 w-full max-w-md" onClick={(e) => e.stopPropagation()}>
        <div className="flex justify-between items-center mb-6">
          <h2 className="text-xl font-bold text-white">Digest Settings</h2>
          <button type="button" onClick={onClose} className="text-slate-400 hover:text-white">
            <X size={20} />
          </button>
        </div>

        <div className="space-y-4">
          <div>
            <label htmlFor="garden-digest-time" className="block text-xs text-slate-400 mb-1">Morning digest time</label>
            <input
              id="garden-digest-time"
              type="time"
              value={digestTime}
              onChange={(e) => setDigestTime(e.target.value)}
              className="w-full px-3 py-2 bg-white/5 border border-white/10 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-purple-500"
            />
          </div>

          <div className="pt-3 border-t border-white/10">
            <button
              type="button"
              onClick={() => {
                setSaving(true);
                testMutation.mutate();
              }}
              disabled={saving}
              className="w-full px-4 py-2.5 rounded-lg bg-purple-600 text-white hover:bg-purple-500 disabled:opacity-50"
            >
              {saving ? 'Sending…' : 'Send test digest'}
            </button>
          </div>

          <div className="mt-4 flex gap-3">
            <button
              type="button"
              onClick={onClose}
              className="flex-1 px-4 py-2.5 rounded-lg bg-white/5 text-slate-300 hover:bg-white/10 border border-white/10 disabled:opacity-50"
              disabled={saving}
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => {
                setSaving(true);
                saveMutation.mutate(digestTime);
              }}
              disabled={saving || !digestTime}
              className="flex-1 px-4 py-2.5 rounded-lg bg-purple-600 text-white hover:bg-purple-500 disabled:opacity-50"
            >
              {saving ? 'Saving…' : 'Save'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ========================= Log Harvest Modal (Phase 4) ========================= */

function LogHarvestModal({
  open,
  onClose,
  plant,
}: {
  open: boolean;
  onClose: () => void;
  plant: HarvestReadyItem | null;
}) {
  const queryClient = useQueryClient();
  const [quantity, setQuantity] = useState('1');
  const [unit, setUnit] = useState('bunch');
  const [disposition, setDisposition] = useState('fresh');
  const [notes, setNotes] = useState('');

  const units = ['bunch', 'lb', 'oz', 'pint', 'quart', 'piece', 'count'];
  const dispositions = ['fresh', 'canned', 'frozen', 'dried', 'fermented', 'shared'];

  const logHarvestMutation = useMutation({
    mutationFn: async ({ plantId, body }: { plantId: string; body: HarvestLogIn }) => {
      return api.gardenLogHarvest(plantId, body);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['garden-today'] });
      queryClient.invalidateQueries({ queryKey: ['garden-plants'] });
      queryClient.invalidateQueries({ queryKey: ['garden-harvest-ready'] });
      queryClient.invalidateQueries({ queryKey: ['garden-streaks'] });
      onClose();
      setQuantity('1');
      setNotes('');
      toast.success('Harvest logged');
    },
    onError: () => toast.error('Failed to log harvest'),
  });

  if (!open || !plant) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-white/10 border border-white/20 rounded-2xl p-6 w-full max-w-md" onClick={(e) => e.stopPropagation()}>
        <div className="flex justify-between items-center mb-6">
          <h2 className="text-xl font-bold text-white">Log Harvest</h2>
          <button type="button" onClick={onClose} className="text-slate-400 hover:text-white">
            <X size={20} />
          </button>
        </div>

        <div className="space-y-4">
          <div>
            <span className="block text-xs text-slate-400 mb-1">Plant</span>
            <p className="text-sm text-white font-medium">{plant.nickname || plant.common_name}</p>
            <p className="text-xs text-slate-500">{plant.category} · {plant.species_ref}</p>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label htmlFor="ready-harvest-qty" className="block text-xs text-slate-400 mb-1">Quantity</label>
              <input
                id="ready-harvest-qty"
                type="number"
                value={quantity}
                onChange={(e) => setQuantity(e.target.value)}
                className="w-full px-3 py-2 bg-white/5 border border-white/10 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-purple-500"
              />
            </div>
            <div>
              <label htmlFor="ready-harvest-unit" className="block text-xs text-slate-400 mb-1">Unit</label>
              <select
                id="ready-harvest-unit"
                value={unit}
                onChange={(e) => setUnit(e.target.value)}
                className="w-full px-3 py-2 bg-white/5 border border-white/10 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-purple-500"
              >
                {units.map((u) => (
                  <option key={u} value={u}>
                    {u.charAt(0).toUpperCase() + u.slice(1)}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div>
            <label htmlFor="ready-harvest-disposition" className="block text-xs text-slate-400 mb-1">Disposition</label>
            <select
              id="ready-harvest-disposition"
              value={disposition}
              onChange={(e) => setDisposition(e.target.value)}
              className="w-full px-3 py-2 bg-white/5 border border-white/10 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-purple-500"
            >
              {dispositions.map((d) => (
                <option key={d} value={d}>
                  {d.charAt(0).toUpperCase() + d.slice(1)}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label htmlFor="ready-harvest-notes" className="block text-xs text-slate-400 mb-1">Notes (optional)</label>
            <textarea
              id="ready-harvest-notes"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={3}
              className="w-full px-3 py-2 bg-white/5 border border-white/10 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-purple-500"
              placeholder="e.g. Used for pesto, shared with neighbor…"
            />
          </div>

          {plant.herb_preservation_tips && (
            <div className="pt-3 border-t border-white/10">
              <p className="text-xs text-amber-300 mb-1">Herb preservation tip:</p>
              <p className="text-xs text-slate-300">{plant.herb_preservation_tips}</p>
            </div>
          )}

          <div className="mt-4 flex gap-3">
            <button
              type="button"
              onClick={onClose}
              className="flex-1 px-4 py-2.5 rounded-lg bg-white/5 text-slate-300 hover:bg-white/10 border border-white/10 disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => {
                logHarvestMutation.mutate({
                  plantId: plant.plant_id,
                  body: {
                    plant_id: plant.plant_id,
                    quantity: parseFloat(quantity) || 1,
                    unit,
                    disposition,
                    notes: notes || undefined,
                  },
                });
              }}
              className="flex-1 px-4 py-2.5 rounded-lg bg-purple-600 text-white hover:bg-purple-500 disabled:opacity-50"
            >
              Log Harvest
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ========================= Add Plant Modal (fast-add) ========================= */

function AddPlantModal({
  open,
  onClose,
  locations,
}: {
  open: boolean;
  onClose: () => void;
  locations: GardenLocation[];
}) {
  const queryClient = useQueryClient();
  const [query, setQuery] = useState('');
  const [speciesResults, setSpeciesResults] = useState<GardenSpecies[]>([]);
  const [selectedSpecies, setSelectedSpecies] = useState<GardenSpecies | null>(null);
  const [nickname, setNickname] = useState('');
  const [locationId, setLocationId] = useState<string>('');
  const [showNewRoom, setShowNewRoom] = useState(false);
  const [newRoomName, setNewRoomName] = useState('');
  const [showTuning, setShowTuning] = useState(false);
  const [potSize, setPotSize] = useState<string>('');
  const [potMaterial, setPotMaterial] = useState<string>('');
  const [windowDirection, setWindowDirection] = useState<string>('');
  const [distanceFromWindow, setDistanceFromWindow] = useState<string>('');
  const [creating, setCreating] = useState(false);
  // Phase 3: unknown species lookup flow
  const [lookupMode, setLookupMode] = useState(false);
  const [lookupDraft, setLookupDraft] = useState<GardenSpecies | null>(null);
  const [lookupWarnings, setLookupWarnings] = useState<string[]>([]);
  const [lookupError, setLookupError] = useState<string | null>(null);

  const starterSpeciesQuery = useQuery({
    queryKey: ['garden-starter-species'],
    queryFn: async () => {
      if (typeof api.gardenListSpecies !== 'function') return [];
      const resp = await api.gardenListSpecies({ category: 'houseplant', limit: 15 });
      return resp?.results ?? [];
    },
    enabled: open,
    staleTime: 5 * 60_000,
  });

  const searchMutation = useMutation({
    mutationFn: async (q: string) => {
      const resp = await api.gardenSearch({ q, limit: 10 });
      return resp.results;
    },
    onSuccess: (results) => setSpeciesResults(results),
    onError: () => setSpeciesResults([]),
  });

  const lookupMutation = useMutation({
    mutationFn: async (q: string) => {
      setLookupMode(true);
      setLookupDraft(null);
      setLookupWarnings([]);
      setLookupError(null);
      const resp = await api.gardenSpeciesLookup({ name: q });
      setLookupWarnings(resp.warnings);
      setLookupDraft(resp.species);
      return resp;
    },
    onError: () => {
      setLookupMode(false);
      setLookupDraft(null);
      setLookupError('Jarvis could not look up that plant right now. You can still add it as a custom plant below.');
      toast.error('Species lookup failed');
    },
  });

  const startManualCustomDraft = (rawName: string) => {
    const clean = rawName.trim();
    if (!clean) return;
    const slug = clean
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '_')
      .replace(/^_+|_+$/g, '')
      .slice(0, 60) || 'custom_plant';
    setLookupError(null);
    setLookupMode(true);
    setLookupWarnings(['Custom entry with default 7-day watering cadence — you can tune it after adding.']);
    setLookupDraft({
      key: `custom_${slug}`,
      category: 'houseplant',
      common_name: clean,
      scientific_name: null,
      family: null,
      light_level: 'bright_indirect',
      watering_interval_days: 7,
      winter_multiplier: 1.3,
      watering_method: 'top_drain',
      dryness_cue: 'Top inch of soil feels dry',
      water_quality: null,
      humidity_band: 'medium',
      misting_rule: null,
      soil_mix: 'Standard indoor potting mix',
      temp_min_f: 60,
      temp_max_f: 80,
      cold_draft_sensitive: 0,
      toxicity_level: 'unknown',
      toxicity_notes: null,
      common_pests: null,
      symptoms: null,
      grooming_propagation: null,
      fertilizing: null,
      repot_interval_months: null,
      description: 'Custom plant added manually.',
      preservation_methods: null,
      usda_acidity_class: null,
      frost_tolerance: 'tender',
      usda_zone_min: null,
      usda_zone_max: null,
      days_to_maturity: null,
      tall_trellis: null,
      culinary_aliases: null,
      pinching_bolting_notes: null,
      regrowth_cadence: null,
      herb_preservation_tips: null,
    });
  };

  const saveCustomMutation = useMutation({
    mutationFn: async () => {
      if (!lookupDraft) throw new Error('No draft to save');
      const body: GardenCustomSpeciesIn = {
        key: lookupDraft.key,
        category: lookupDraft.category,
        common_name: lookupDraft.common_name,
        scientific_name: lookupDraft.scientific_name ?? undefined,
        family: lookupDraft.family ?? undefined,
        description: lookupDraft.description ?? undefined,
        watering_interval_days: lookupDraft.watering_interval_days ?? undefined,
        winter_multiplier: lookupDraft.winter_multiplier ?? undefined,
        watering_method: lookupDraft.watering_method ?? undefined,
        dryness_cue: lookupDraft.dryness_cue ?? undefined,
        water_quality: lookupDraft.water_quality ?? undefined,
        light_level: lookupDraft.light_level ?? undefined,
        temp_min_f: lookupDraft.temp_min_f ?? undefined,
        temp_max_f: lookupDraft.temp_max_f ?? undefined,
        cold_draft_sensitive: Boolean(lookupDraft.cold_draft_sensitive) ?? undefined,
        humidity_band: lookupDraft.humidity_band ?? undefined,
        misting_rule: lookupDraft.misting_rule ?? undefined,
        soil_mix: lookupDraft.soil_mix ?? undefined,
        fertilizing: lookupDraft.fertilizing ?? undefined,
        repot_interval_months: lookupDraft.repot_interval_months ?? undefined,
        grooming_propagation: lookupDraft.grooming_propagation ?? undefined,
        common_pests: lookupDraft.common_pests ?? undefined,
        symptoms: lookupDraft.symptoms ?? undefined,
        frost_tolerance: lookupDraft.frost_tolerance ?? undefined,
        usda_zone_min: lookupDraft.usda_zone_min ?? undefined,
        usda_zone_max: lookupDraft.usda_zone_max ?? undefined,
        days_to_maturity: lookupDraft.days_to_maturity ?? undefined,
        tall_trellis: lookupDraft.tall_trellis ?? undefined,
        toxicity_level: lookupDraft.toxicity_level ?? undefined,
        toxicity_notes: lookupDraft.toxicity_notes ?? undefined,
        preservation_methods: lookupDraft.preservation_methods ?? undefined,
        usda_acidity_class: lookupDraft.usda_acidity_class ?? undefined,
        source: 'wiki_ai_draft',
      };
      const resp = await api.gardenSaveCustomSpecies(body);
      setSelectedSpecies({ ...lookupDraft, key: resp.key } as GardenSpecies);
      setLookupMode(false);
      setLookupDraft(null);
      toast.success('Species saved');
      return resp;
    },
    onError: () => {
      toast.error('Failed to save custom species');
    },
  });

  const createMutation = useMutation({
    mutationFn: async () => {
      if (!selectedSpecies) throw new Error('No species selected');
      const body: Partial<GardenPlant> = {
        category: selectedSpecies.category || 'houseplant',
        species_ref: selectedSpecies.key,
        location_id: locationId || undefined,
        nickname: nickname || undefined,
      };
      if (potSize) body.pot_size_in = parseFloat(potSize);
      if (potMaterial) body.pot_material = potMaterial;
      if (windowDirection) body.window_direction = windowDirection;
      if (distanceFromWindow) body.distance_from_window = parseFloat(distanceFromWindow);
      const resp = await api.gardenCreatePlant(body);
      return resp;
    },
    onSuccess: () => {
      setCreating(false);
      onClose();
      queryClient.invalidateQueries({ queryKey: ['garden-plants'] });
      queryClient.invalidateQueries({ queryKey: ['garden-today'] });
      queryClient.invalidateQueries({ queryKey: ['garden-locations'] });
      toast.success('Plant added');
      setQuery('');
      setSpeciesResults([]);
      setSelectedSpecies(null);
      setNickname('');
      setLocationId('');
      setShowNewRoom(false);
      setShowTuning(false);
      setPotSize('');
      setPotMaterial('');
      setWindowDirection('');
      setDistanceFromWindow('');
      setNewRoomName('');
      setLookupMode(false);
      setLookupDraft(null);
      setLookupWarnings([]);
      setLookupError(null);
    },
    onError: () => {
      setCreating(false);
      toast.error('Failed to add plant');
    },
  });

  const handleSearch = (e: React.ChangeEvent<HTMLInputElement>) => {
    const q = e.target.value;
    setQuery(q);
    setLookupError(null);
    if (q.trim().length >= 2) {
      searchMutation.mutate(q.trim());
    } else {
      setSpeciesResults([]);
    }
  };

  const visibleSpecies =
    query.trim().length >= 2 ? speciesResults : (starterSpeciesQuery.data ?? []);

  const suggestName = () => {
    if (selectedSpecies?.common_name) {
      const base = selectedSpecies.common_name.split(' ')[0];
      setNickname(`Sir ${base}`);
    }
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-white/10 border border-white/20 rounded-2xl p-6 w-full max-w-md max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <div className="flex justify-between items-center mb-6">
          <h2 className="text-xl font-bold text-white">Add Plant</h2>
          <button type="button" onClick={onClose} className="text-slate-400 hover:text-white">
            <X size={20} />
          </button>
        </div>

        <div className="space-y-4">
          <div>
            <label htmlFor="garden-add-species" className="block text-xs text-slate-400 mb-1">Search species</label>
            <input
              id="garden-add-species"
              type="text"
              value={query}
              onChange={handleSearch}
              placeholder="e.g. Monstera, Basil, Snake Plant"
              className="w-full px-3 py-2 bg-white/5 border border-white/10 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-purple-500"
            />
          </div>

          {searchMutation.isPending && query.trim().length >= 2 && (
            <p className="text-xs text-slate-400">Searching catalog…</p>
          )}

          {visibleSpecies.length > 0 && (
            <div className="max-h-48 overflow-y-auto space-y-1">
              {query.trim().length < 2 && (
                <p className="text-[11px] uppercase tracking-wider text-slate-400 mb-1">
                  Popular species
                </p>
              )}
              {visibleSpecies.map((s) => (
                <button
                  key={s.key}
                  type="button"
                  onClick={() => setSelectedSpecies(s)}
                  className={`w-full text-left px-3 py-2 rounded-lg text-sm transition-colors ${
                    selectedSpecies?.key === s.key
                      ? 'bg-purple-600 text-white'
                      : 'bg-white/5 text-slate-300 hover:bg-white/10'
                  }`}
                >
                  <div className="font-medium text-white">{s.common_name}</div>
                  <div className="text-xs text-slate-400">{s.scientific_name || ''}</div>
                </button>
              ))}
            </div>
          )}

          {lookupMutation.isPending && (
            <p className="text-xs text-purple-300">Looking up “{query.trim()}”…</p>
          )}

          {lookupError && (
            <p className="text-xs text-amber-300">{lookupError}</p>
          )}

          {query.trim().length >= 2 && !searchMutation.isPending && speciesResults.length === 0 && !lookupMode && !lookupDraft && (
            <div className="space-y-2">
              <button
                type="button"
                onClick={() => lookupMutation.mutate(query.trim())}
                disabled={creating || lookupMutation.isPending}
                className="w-full flex items-center text-left px-3 py-2 rounded-lg text-sm text-purple-400 border border-purple-500/30 hover:bg-purple-500/10 hover:text-purple-300 transition-colors"
              >
                <Search size={14} className="mr-2 shrink-0" />
                Ask Jarvis to look up “{query.trim()}”
              </button>
              <button
                type="button"
                onClick={() => startManualCustomDraft(query)}
                disabled={creating}
                className="w-full text-left px-3 py-2 rounded-lg text-xs text-slate-300 border border-white/10 hover:bg-white/10 transition-colors"
              >
                Or add “{query.trim()}” as a custom plant
              </button>
            </div>
          )}

          {lookupMode && lookupDraft && (
            <div className="space-y-3 p-3 bg-white/5 rounded-xl border border-white/10">
              <h3 className="text-xs font-semibold text-amber-400 uppercase tracking-wider mb-1">
                Review drafted species
              </h3>
              <p className="text-xs text-slate-400 mb-2">
                {lookupDraft.common_name} — {lookupDraft.scientific_name || ''}
              </p>
              {lookupWarnings.length > 0 && (
                <p className="text-xs text-amber-300 mb-2">
                  {lookupWarnings.join('; ')}
                </p>
              )}
              <button
                type="button"
                onClick={() => saveCustomMutation.mutate()}
                disabled={creating || saveCustomMutation.isPending}
                className="w-full px-3 py-2 rounded-lg bg-purple-600 text-white hover:bg-purple-500 disabled:opacity-50 text-sm transition-colors"
              >
                {saveCustomMutation.isPending ? 'Saving…' : 'Save & Select Species'}
              </button>
              <button
                type="button"
                onClick={() => {
                  setLookupMode(false);
                  setLookupDraft(null);
                  setLookupWarnings([]);
                }}
                className="w-full text-xs text-slate-500 hover:text-slate-300 underline"
              >
                Cancel
              </button>
            </div>
          )}

          {selectedSpecies && (
            <div className="space-y-4">
              <div className="p-3 bg-white/5 rounded-xl border border-white/10">
                <div className="flex items-center gap-3">
                  <div className="text-slate-400">{selectedSpecies.common_name}</div>
                  <span className="text-xs text-slate-500">{selectedSpecies.scientific_name}</span>
                </div>
                <p className="text-xs text-slate-400 mt-1">{selectedSpecies.description || ''}</p>
              </div>

              <div>
                <label htmlFor="garden-add-nickname" className="block text-xs text-slate-400 mb-1">Personal name (optional)</label>
                <input
                  id="garden-add-nickname"
                  type="text"
                  value={nickname}
                  onChange={(e) => setNickname(e.target.value)}
                  placeholder="e.g. Monty"
                  className="w-full px-3 py-2 bg-white/5 border border-white/10 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-purple-500"
                />
                <button
                  type="button"
                  onClick={suggestName}
                  className="mt-2 text-xs text-purple-400 hover:text-purple-300 underline"
                >
                  Suggest a name
                </button>
              </div>

              <div>
                <label htmlFor="garden-add-room" className="block text-xs text-slate-400 mb-1">Room</label>
                <div className="flex gap-2">
                  <select
                    id="garden-add-room"
                    value={locationId}
                    onChange={(e) => setLocationId(e.target.value)}
                    className="flex-1 px-3 py-2 bg-white/5 border border-white/10 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-purple-500"
                  >
                    <option value="">Choose a room</option>
                    {locations.map((l) => (
                      <option key={l.id} value={l.id}>
                        {l.name}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    onClick={() => setShowNewRoom((prev) => !prev)}
                    className="px-2 py-2 bg-white/5 border border-white/10 rounded-lg text-purple-400 hover:bg-white/10 hover:text-purple-300 focus:outline-none focus:ring-2 focus:ring-purple-500"
                    title="Add a new room"
                  >
                    +
                  </button>
                </div>
                {showNewRoom && (
                  <div className="mt-2 flex gap-2">
                    <input
                      type="text"
                      value={newRoomName}
                      onChange={(e) => setNewRoomName(e.target.value)}
                      placeholder="Room name"
                      aria-label="New room name"
                      className="flex-1 px-3 py-2 bg-white/5 border border-white/10 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-purple-500"
                    />
                    <button
                      type="button"
                      onClick={async () => {
                        if (!newRoomName.trim()) return;
                        setCreating(true);
                        try {
                          const resp = await api.gardenCreateLocation({
                            name: newRoomName.trim(),
                            kind: 'indoor_room',
                          });
                          setNewRoomName('');
                          setShowNewRoom(false);
                          setLocationId(resp.id);
                          queryClient.invalidateQueries({ queryKey: ['garden-locations'] });
                          toast.success('Room added');
                        } catch {
                          toast.error('Failed to add room');
                        } finally {
                          setCreating(false);
                        }
                      }}
                      disabled={creating || !newRoomName.trim()}
                      className="px-3 py-2 bg-purple-600 text-white rounded-lg hover:bg-purple-500 disabled:opacity-50"
                    >
                      Add
                    </button>
                  </div>
                )}
              </div>

              <button
                type="button"
                onClick={() => setShowTuning(!showTuning)}
                className="w-full text-xs text-purple-400 hover:text-purple-300 underline flex items-center justify-between"
              >
                <span>Pot & window spot (optional)</span>
                <ChevronRight size={14} />
              </button>

              {showTuning && (
                <div className="space-y-3 pt-3 border-t border-white/10">
                  <div>
                    <label htmlFor="garden-add-pot-size" className="block text-xs text-slate-400 mb-1">Pot size (in)</label>
                    <input
                      id="garden-add-pot-size"
                      type="number"
                      value={potSize}
                      onChange={(e) => setPotSize(e.target.value)}
                      className="w-full px-3 py-2 bg-white/5 border border-white/10 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-purple-500"
                    />
                  </div>
                  <div>
                    <label htmlFor="garden-add-pot-material" className="block text-xs text-slate-400 mb-1">Pot material</label>
                    <select
                      id="garden-add-pot-material"
                      value={potMaterial}
                      onChange={(e) => setPotMaterial(e.target.value)}
                      className="w-full px-3 py-2 bg-white/5 border border-white/10 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-purple-500"
                    >
                      <option value="">Standard</option>
                      <option value="terracotta">Terracotta</option>
                      <option value="plastic">Plastic</option>
                      <option value="ceramic">Ceramic</option>
                      <option value="fabric">Fabric</option>
                    </select>
                  </div>
                  <div>
                    <label htmlFor="garden-add-window-direction" className="block text-xs text-slate-400 mb-1">Window direction</label>
                    <select
                      id="garden-add-window-direction"
                      value={windowDirection}
                      onChange={(e) => setWindowDirection(e.target.value)}
                      className="w-full px-3 py-2 bg-white/5 border border-white/10 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-purple-500"
                    >
                      <option value="">Any</option>
                      <option value="n">North</option>
                      <option value="s">South</option>
                      <option value="e">East</option>
                      <option value="w">West</option>
                    </select>
                  </div>
                  <div>
                    <label htmlFor="garden-add-window-distance" className="block text-xs text-slate-400 mb-1">Distance from window (ft)</label>
                    <input
                      id="garden-add-window-distance"
                      type="number"
                      value={distanceFromWindow}
                      onChange={(e) => setDistanceFromWindow(e.target.value)}
                      className="w-full px-3 py-2 bg-white/5 border border-white/10 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-purple-500"
                    />
                  </div>
                </div>
              )}
            </div>
          )}

          {!selectedSpecies && (
            <p className="text-xs text-slate-400">
              Select a species above to enable Add Plant.
            </p>
          )}

          <div className="mt-6 flex gap-3">
            <button
              type="button"
              onClick={onClose}
              disabled={creating}
              className="flex-1 px-4 py-2.5 rounded-lg bg-white/5 text-slate-300 hover:bg-white/10 border border-white/10 disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => {
                if (!selectedSpecies) return;
                setCreating(true);
                createMutation.mutate();
              }}
              disabled={creating || !selectedSpecies}
              className="flex-1 px-4 py-2.5 rounded-lg bg-purple-600 text-white hover:bg-purple-500 disabled:opacity-50"
            >
              {creating ? 'Adding…' : 'Add Plant'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
