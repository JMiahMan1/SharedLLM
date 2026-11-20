import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  Plus, Sun, Droplets, Leaf, X, ChevronRight,
  ShieldAlert, RotateCw, Flower,
} from 'lucide-react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'react-hot-toast';
import { api } from '../services/api';
import type { GardenPlant, GardenTodayTask, GardenTodaySummary, GardenSpecies, GardenLocation } from '../types/api';
/* ========================= Garden Page ========================= */

export default function Garden() {
  const [tab, setTab] = useState<'today' | 'plants'>('today');
  const [addOpen, setAddOpen] = useState(false);

  const todayQuery = useQuery({
    queryKey: ['garden-today'],
    queryFn: () => api.gardenToday(),
    staleTime: 60_000,
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

  const today = todayQuery.data;
  const plants = plantsQuery.data ?? [];
  const locations = locationsQuery.data ?? [];

  return (
    <div className="flex flex-col h-full w-full bg-slate-950 text-slate-200">
      <div className="flex-1 overflow-y-auto p-4 md:p-6">
        <div className="mb-6 flex flex-col md:flex-row md:items-center md:justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold text-white">Garden</h1>
            <p className="text-slate-400 text-sm">
              {plants.length} plant{plants.length !== 1 ? 's' : ''} · {today?.due_count ?? 0} due today
            </p>
          </div>
          <div className="flex items-center gap-2">
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
          </div>
        </div>

        {tab === 'today' ? (
          <TodayTab today={today} locations={locations} />
        ) : (
          <PlantsTab plants={plants} locations={locations} />
        )}

        <div className="mt-6 flex justify-end">
          <button
            type="button"
            onClick={() => setAddOpen(true)}
            className="flex items-center gap-2 px-5 py-2.5 bg-purple-600 hover:bg-purple-500 text-white rounded-xl text-sm font-medium transition-colors shadow-lg shadow-purple-500/25"
          >
            <Plus size={16} /> Add Plant
          </button>
        </div>

        <AddPlantModal
          open={addOpen}
          onClose={() => setAddOpen(false)}
          locations={locations}
        />
      </div>
    </div>
  );
}

/* ========================= Today Tab ========================= */

function TodayTab({
  today,
  locations,
}: {
  today: GardenTodaySummary | undefined;
  locations: GardenLocation[];
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

  return (
    <div className="space-y-6">
      {today.due_count === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-slate-400">
          <Flower size={48} className="mb-4 opacity-50" />
          <h3 className="text-lg font-medium text-slate-300">All caught up</h3>
          <p className="text-sm text-slate-500 mt-1">No plants need care today. Add some plants to get started.</p>
        </div>
      ) : (
        <>
          {roomEntries.map(([roomName, tasks]) => {
            const roomTasks = tasks as GardenTodayTask[];
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
                      {roomTasks.length}
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
                <div className="space-y-0">{roomTasks.map(renderTask)}</div>
              </section>
            );
          })}
        </>
      )}
    </div>
  );
}

/* ========================= Plants Tab ========================= */

function PlantsTab({ plants, locations }: { plants: GardenPlant[]; locations: GardenLocation[] }) {
  const [filter, setFilter] = useState<'all' | 'houseplant' | 'herb' | 'outdoor'>('all');

  const locationMap = new Map(locations.map((l) => [l.id, l.name]));

  const FILTERS: Array<'all' | 'houseplant' | 'herb' | 'outdoor'> = ['all', 'houseplant', 'herb', 'outdoor'];

  const filtered = plants.filter((p) => {
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
      onClick={() => navigate(`/garden/plants/${plant.id}`)}
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
      <div className="flex items-center gap-2 pt-3 border-t border-white/5">
        <Droplets size={14} className="text-slate-400" />
        <span className="text-sm text-slate-300">Next care due</span>
      </div>
    </div>
  );
}

/* ========================= Plant Detail ========================= */

export function PlantDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const plantQuery = useQuery({
    queryKey: ['garden-plant', id],
    queryFn: () => {
      // Not implemented as a separate endpoint; derive from list
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
  const [newRoomName, setNewRoomName] = useState('');
  const [showTuning, setShowTuning] = useState(false);
  const [potSize, setPotSize] = useState<string>('');
  const [potMaterial, setPotMaterial] = useState<string>('');
  const [windowDirection, setWindowDirection] = useState<string>('');
  const [distanceFromWindow, setDistanceFromWindow] = useState<string>('');
  const [creating, setCreating] = useState(false);

  const searchMutation = useMutation({
    mutationFn: async (q: string) => {
      const resp = await api.gardenSearch({ q, limit: 10 });
      return resp.results;
    },
    onSuccess: (results) => setSpeciesResults(results),
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
      setShowTuning(false);
      setPotSize('');
      setPotMaterial('');
      setWindowDirection('');
      setDistanceFromWindow('');
      setNewRoomName('');
    },
    onError: () => {
      setCreating(false);
      toast.error('Failed to add plant');
    },
  });

  const handleSearch = (e: React.ChangeEvent<HTMLInputElement>) => {
    const q = e.target.value;
    setQuery(q);
    if (q.length >= 2) {
      searchMutation.mutate(q);
    } else {
      setSpeciesResults([]);
    }
  };

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

          {speciesResults.length > 0 && (
            <div className="max-h-48 overflow-y-auto space-y-1">
              {speciesResults.map((s) => (
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
                  <div className="text-xs text-slate-500">{s.scientific_name || ''}</div>
                </button>
              ))}
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
                    onClick={() => setNewRoomName('+ New room')}
                    className="px-2 py-2 bg-white/5 border border-white/10 rounded-lg text-purple-400 hover:bg-white/10 hover:text-purple-300 focus:outline-none focus:ring-2 focus:ring-purple-500"
                    title="Add a new room"
                  >
                    +
                  </button>
                </div>
                {newRoomName === '+ New room' && (
                  <div className="mt-2 flex gap-2">
                    <input
                      type="text"
                      value={locationId}
                      onChange={(e) => setLocationId(e.target.value)}
                      placeholder="Room name"
                      aria-label="New room name"
                      className="flex-1 px-3 py-2 bg-white/5 border border-white/10 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-purple-500"
                    />
                    <button
                      type="button"
                      onClick={async () => {
                        if (!locationId.trim()) return;
                        setCreating(true);
                        try {
                          const resp = await api.gardenCreateLocation({
                            name: locationId.trim(),
                            kind: 'indoor_room',
                          });
                          setNewRoomName('');
                          setLocationId(resp.id);
                          queryClient.invalidateQueries({ queryKey: ['garden-locations'] });
                          toast.success('Room added');
                        } catch {
                          toast.error('Failed to add room');
                        } finally {
                          setCreating(false);
                        }
                      }}
                      disabled={creating || !locationId.trim()}
                      className="px-3 py-2 bg-purple-600 text-white rounded-lg hover:bg-purple-500 disabled:opacity-50"
                    >
                      Add
                    </button>
                  </div>
                )}
                {newRoomName !== '+ New room' && newRoomName !== '' && (
                  <button
                    type="button"
                    onClick={() => setNewRoomName('')}
                    className="text-xs text-slate-500 hover:text-slate-300 underline"
                  >
                    Cancel
                  </button>
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
