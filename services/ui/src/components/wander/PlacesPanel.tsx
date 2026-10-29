import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Crosshair, MapPin, Pencil, Plus, ShieldAlert, Trash2, X } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import { errorText } from '../../lib/errorText';
import { confirmDialog } from '../ui/confirmDialog';
import Toggle from '../ui/Toggle';
import type { SavedPlace, SavedPlaceInput, VisitSuggestion } from '../../types/api';

export interface PlaceSetting {
  id: string;
  name: string;
  important: boolean;
  notify: string[];
}

export interface PlaceMember {
  username: string;
  name: string;
}

interface Props {
  places: PlaceSetting[];
  members: PlaceMember[];
  /** Whether the viewer may change the Home Assistant place settings
   *  (admins only; the server refuses everyone else). */
  canEdit: boolean;
  /** Called after a Home Assistant place is saved, so the caller can reload. */
  onSaved: () => void;
  /** The signed-in person, so their own saved places can be edited. */
  currentUser: string;
  /** Admins may edit anyone's saved place. */
  isAdmin: boolean;
}

interface Draft {
  id?: string;
  name: string;
  lat: string;
  lon: string;
  radius: string;
  scope: 'family' | 'user';
  important: boolean;
  notify: string[];
}

const EMPTY_DRAFT: Draft = {
  name: '',
  lat: '',
  lon: '',
  radius: '150',
  scope: 'user',
  important: false,
  notify: [],
};

/** A saved place's owner may edit it; admins may edit any. */
function mayEdit(place: SavedPlace, currentUser: string, isAdmin: boolean): boolean {
  return isAdmin || place.owner.toLowerCase() === currentUser.toLowerCase();
}

function minutesLabel(seconds: number): string {
  const mins = Math.round(seconds / 60);
  return mins >= 60 ? `${Math.round(mins / 60)} h` : `${mins} min`;
}

/**
 * The family's places, in two kinds.
 *
 * Home Assistant zones (a school, a grandparent's house) are drawn in Home
 * Assistant; an admin marks one important so arriving or leaving becomes a
 * family safety notice to the people picked, or everyone who may see the
 * person. Places saved here are added from Wander: one shared family list plus
 * each person's own private places, and whoever adds one owns it.
 */
export default function PlacesPanel({ places, members, canEdit, onSaved, currentUser, isAdmin }: Props) {
  const queryClient = useQueryClient();
  const [saving, setSaving] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);

  const savedQuery = useQuery<SavedPlace[]>({ queryKey: ['saved-places'], queryFn: api.getSavedPlaces });
  const suggestionsQuery = useQuery<VisitSuggestion[]>({ queryKey: ['visit-suggestions'], queryFn: api.getVisitSuggestions });
  const savedPlaces = savedQuery.data ?? [];
  const suggestions = suggestionsQuery.data ?? [];

  const refreshSaved = () => void queryClient.invalidateQueries({ queryKey: ['saved-places'] });

  const saveZone = async (place: PlaceSetting, important: boolean, notify: string[]) => {
    setSaving(place.id);
    try {
      await api.setPlaceSettings(place.id, { important, notify });
      onSaved();
    } catch (err) {
      toast.error(errorText(err, `Could not save ${place.name}`));
    } finally {
      setSaving(null);
    }
  };

  const toggleMember = (place: PlaceSetting, username: string) => {
    const next = place.notify.includes(username)
      ? place.notify.filter((u) => u !== username)
      : [...place.notify, username];
    void saveZone(place, true, next);
  };

  const savePlace = useMutation({
    mutationFn: (payload: { id?: string; body: SavedPlaceInput }) =>
      payload.id ? api.updateSavedPlace(payload.id, payload.body) : api.addSavedPlace(payload.body),
    onSuccess: (place, payload) => {
      toast.success(payload.id ? `Saved ${place.name}` : `Added ${place.name}`);
      setDraft(null);
      refreshSaved();
    },
    onError: (err) => toast.error(errorText(err, 'Could not save the place')),
  });

  const removePlace = useMutation({
    mutationFn: (place: SavedPlace) => api.deleteSavedPlace(place.id),
    onSuccess: () => {
      toast.success('Place removed');
      refreshSaved();
    },
    onError: (err) => toast.error(errorText(err, 'Could not remove the place')),
  });

  const dismiss = useMutation({
    mutationFn: (spot: VisitSuggestion) => api.dismissVisitSuggestion(spot.lat, spot.lon),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['visit-suggestions'] }),
    onError: (err) => toast.error(errorText(err, 'Could not dismiss that')),
  });

  const useMyLocation = () => {
    if (!navigator.geolocation) {
      toast.error('This device cannot share its location');
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => setDraft((d) => (d ? { ...d, lat: pos.coords.latitude.toFixed(5), lon: pos.coords.longitude.toFixed(5) } : d)),
      () => toast.error('Could not read your location'),
    );
  };

  const submitDraft = () => {
    if (!draft) return;
    const name = draft.name.trim();
    const lat = Number(draft.lat);
    const lon = Number(draft.lon);
    const radius = Number(draft.radius);
    if (!name) return toast.error('Give the place a name');
    if (!Number.isFinite(lat) || lat < -90 || lat > 90) return toast.error('Latitude must be between -90 and 90');
    if (!Number.isFinite(lon) || lon < -180 || lon > 180) return toast.error('Longitude must be between -180 and 180');
    const body: SavedPlaceInput = {
      name,
      lat,
      lon,
      radius: Number.isFinite(radius) ? radius : 150,
      scope: draft.scope,
      important: draft.important,
      notify: draft.important ? draft.notify : [],
    };
    savePlace.mutate({ id: draft.id, body });
  };

  const toggleDraftMember = (username: string) =>
    setDraft((d) =>
      d ? { ...d, notify: d.notify.includes(username) ? d.notify.filter((u) => u !== username) : [...d.notify, username] } : d,
    );

  const showPanel = places.length > 0 || savedPlaces.length > 0 || suggestions.length > 0 || draft !== null;
  if (!showPanel) return null;

  const notifyPicker = (on: boolean, notify: string[], disabled: boolean, toggle: (u: string) => void) => (
    <div className="flex flex-wrap items-center gap-2 pb-1">
      <span className="text-xs text-slate-500">{notify.length === 0 ? 'Alerts go to everyone. Or only:' : 'Alerts go to:'}</span>
      {members.map((m) => {
        const on2 = notify.includes(m.username);
        return (
          <button
            key={m.username}
            type="button"
            aria-pressed={on2}
            disabled={disabled || !on}
            onClick={() => toggle(m.username)}
            className={`min-h-11 rounded-full border px-3 text-xs transition ${
              on2 ? 'border-purple-400/60 bg-purple-500/20 text-purple-100' : 'border-white/10 text-slate-400 hover:text-slate-200'
            }`}
          >
            {m.name}
          </button>
        );
      })}
    </div>
  );

  return (
    <div className="glass-panel space-y-4 rounded-2xl border border-white/5 p-4" data-testid="places-panel">
      <div>
        <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-200">
          <MapPin size={15} className="text-purple-400" /> Places
        </h3>
        <p className="mt-1 text-xs text-slate-500">
          {canEdit
            ? 'Mark a place important to get a safety alert when someone arrives or leaves. Home Assistant places are drawn in Home Assistant; the ones you add here live in Wander.'
            : 'Arriving at or leaving an important place sends a safety alert. You can add your own places here.'}
        </p>
      </div>

      {places.length > 0 && (
        <ul className="divide-y divide-white/5">
          {places.map((place) => (
            <li key={place.id} className="py-2" data-testid={`place-${place.id}`}>
              <div className="flex min-h-11 items-center justify-between gap-3">
                <span className="flex min-w-0 items-center gap-2 text-sm text-slate-200">
                  {place.important && <ShieldAlert size={14} className="shrink-0 text-amber-300" />}
                  <span className="truncate">{place.name}</span>
                </span>
                {canEdit ? (
                  <span className="flex items-center gap-2 text-xs text-slate-400">
                    Important
                    <Toggle
                      checked={place.important}
                      disabled={saving === place.id}
                      ariaLabel={`${place.name} is important`}
                      onChange={(next) => void saveZone(place, next, next ? place.notify : [])}
                    />
                  </span>
                ) : (
                  place.important && <span className="text-xs text-amber-300">Important</span>
                )}
              </div>
              {!canEdit && place.important && (
                <p className="pb-1 text-xs text-slate-500">
                  Alerts go to{' '}
                  {place.notify.length === 0
                    ? 'everyone'
                    : place.notify.map((u) => members.find((m) => m.username === u)?.name ?? u).join(', ')}
                  .
                </p>
              )}
              {canEdit && place.important && members.length > 0 &&
                notifyPicker(true, place.notify, saving === place.id, (u) => toggleMember(place, u))}
            </li>
          ))}
        </ul>
      )}

      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-400">Saved places</h4>
          <button
            type="button"
            data-testid="add-place"
            onClick={() => setDraft({ ...EMPTY_DRAFT })}
            className="glass-button flex min-h-9 items-center gap-1.5 px-3 text-xs text-slate-300"
          >
            <Plus size={14} /> Add place
          </button>
        </div>

        {savedPlaces.length === 0 && !draft && <p className="text-xs text-slate-500">No places saved yet.</p>}

        <ul className="divide-y divide-white/5">
          {savedPlaces.map((place) => {
            const editable = mayEdit(place, currentUser, isAdmin);
            return (
              <li key={place.id} className="py-2" data-testid={`saved-place-${place.id}`}>
                <div className="flex min-h-11 items-center justify-between gap-3">
                  <span className="flex min-w-0 items-center gap-2 text-sm text-slate-200">
                    {place.important && <ShieldAlert size={14} className="shrink-0 text-amber-300" />}
                    <span className="truncate">{place.name}</span>
                    <span className="shrink-0 rounded-full border border-white/10 px-2 py-0.5 text-[10px] uppercase text-slate-400">
                      {place.scope === 'family' ? 'Family' : 'Private'}
                    </span>
                  </span>
                  {editable && (
                    <span className="flex shrink-0 items-center gap-1">
                      <button
                        type="button"
                        aria-label={`Edit ${place.name}`}
                        onClick={() =>
                          setDraft({
                            id: place.id,
                            name: place.name,
                            lat: String(place.lat),
                            lon: String(place.lon),
                            radius: String(place.radius),
                            scope: place.scope,
                            important: place.important,
                            notify: place.notify,
                          })
                        }
                        className="glass-button flex h-9 w-9 items-center justify-center p-0 text-slate-300"
                      >
                        <Pencil size={14} />
                      </button>
                      <button
                        type="button"
                        aria-label={`Remove ${place.name}`}
                        onClick={async () => {
                          if (await confirmDialog({ title: `Remove ${place.name}?`, body: 'It will no longer send arrival alerts.' })) {
                            removePlace.mutate(place);
                          }
                        }}
                        className="glass-button flex h-9 w-9 items-center justify-center p-0 text-rose-300"
                      >
                        <Trash2 size={14} />
                      </button>
                    </span>
                  )}
                </div>
                {place.important && (
                  <p className="pb-1 text-xs text-slate-500">
                    Alerts go to{' '}
                    {place.notify.length === 0
                      ? 'everyone'
                      : place.notify.map((u) => members.find((m) => m.username === u)?.name ?? u).join(', ')}
                    .
                  </p>
                )}
              </li>
            );
          })}
        </ul>

        {draft && (
          <div className="space-y-2 rounded-xl border border-white/10 p-3" data-testid="place-form">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-slate-300">{draft.id ? 'Edit place' : 'New place'}</span>
              <button type="button" aria-label="Close" onClick={() => setDraft(null)} className="text-slate-400 hover:text-slate-200">
                <X size={14} />
              </button>
            </div>
            <input
              aria-label="Place name"
              value={draft.name}
              onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              placeholder="Name (e.g. Grandma's house)"
              className="glass-input w-full px-2 py-1 text-sm"
            />
            <div className="flex flex-wrap items-center gap-2">
              <input
                aria-label="Latitude"
                value={draft.lat}
                onChange={(e) => setDraft({ ...draft, lat: e.target.value })}
                placeholder="Latitude"
                className="glass-input w-full min-w-0 flex-1 px-2 py-1 text-sm sm:w-32"
              />
              <input
                aria-label="Longitude"
                value={draft.lon}
                onChange={(e) => setDraft({ ...draft, lon: e.target.value })}
                placeholder="Longitude"
                className="glass-input w-full min-w-0 flex-1 px-2 py-1 text-sm sm:w-32"
              />
              <button type="button" onClick={useMyLocation} className="glass-button flex min-h-9 items-center gap-1.5 px-2 text-xs text-slate-300">
                <Crosshair size={13} /> Use mine
              </button>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <input
                aria-label="Radius in metres"
                value={draft.radius}
                onChange={(e) => setDraft({ ...draft, radius: e.target.value })}
                placeholder="Radius (m)"
                className="glass-input w-28 px-2 py-1 text-sm"
              />
              <div className="flex items-center gap-1 text-xs text-slate-400">
                {(['user', 'family'] as const).map((scope) => (
                  <button
                    key={scope}
                    type="button"
                    aria-pressed={draft.scope === scope}
                    onClick={() => setDraft({ ...draft, scope })}
                    className={`min-h-9 rounded-full border px-3 text-xs ${
                      draft.scope === scope
                        ? 'border-purple-400/60 bg-purple-500/20 text-purple-100'
                        : 'border-white/10 text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    {scope === 'family' ? 'Whole family' : 'Just me'}
                  </button>
                ))}
              </div>
              <span className="flex items-center gap-2 text-xs text-slate-400">
                Important
                <Toggle
                  checked={draft.important}
                  ariaLabel="New place is important"
                  onChange={(next) => setDraft({ ...draft, important: next, notify: next ? draft.notify : [] })}
                />
              </span>
            </div>
            {draft.important && members.length > 0 && notifyPicker(true, draft.notify, false, toggleDraftMember)}
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={submitDraft}
                disabled={savePlace.isPending}
                className="glass-button min-h-9 px-3 text-xs text-emerald-200 disabled:opacity-60"
              >
                {draft.id ? 'Save place' : 'Add place'}
              </button>
              <button type="button" onClick={() => setDraft(null)} className="glass-button min-h-9 px-3 text-xs text-slate-400">
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>

      {suggestions.length > 0 && (
        <div className="space-y-2" data-testid="visit-suggestions">
          <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-400">Places you keep visiting</h4>
          <ul className="space-y-2">
            {suggestions.map((spot) => (
              <li
                key={`${spot.lat},${spot.lon}`}
                data-testid={`visit-suggestion-${spot.lat},${spot.lon}`}
                className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-white/10 p-3"
              >
                <span className="min-w-0 text-xs text-slate-300">
                  <span className="font-medium text-slate-200">{spot.place}</span>
                  <span className="text-slate-500">
                    {' '}
                    - {spot.count} visits, about {minutesLabel(spot.total_s / Math.max(1, spot.count))} each.
                  </span>
                </span>
                <span className="flex shrink-0 items-center gap-2">
                  <button
                    type="button"
                    onClick={() =>
                      setDraft({
                        ...EMPTY_DRAFT,
                        name: spot.place,
                        lat: spot.lat.toFixed(5),
                        lon: spot.lon.toFixed(5),
                      })
                    }
                    className="glass-button min-h-9 px-3 text-xs text-emerald-200"
                  >
                    Add as a place
                  </button>
                  <button
                    type="button"
                    aria-label={`Not a place: ${spot.place}`}
                    onClick={() => dismiss.mutate(spot)}
                    className="glass-button min-h-9 px-3 text-xs text-slate-400"
                  >
                    Not a place
                  </button>
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
