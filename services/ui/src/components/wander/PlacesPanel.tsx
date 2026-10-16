import { useState } from 'react';
import { MapPin, ShieldAlert } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import { errorText } from '../../lib/errorText';
import Toggle from '../ui/Toggle';

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
  /** Whether the viewer may change the settings (admins only; the server
   *  refuses everyone else), otherwise they are shown read only. */
  canEdit: boolean;
  /** Called after a place is saved, so the caller can reload the zones. */
  onSaved: () => void;
}

/**
 * The family's places (Home Assistant zones) with an "Important" switch.
 *
 * Arriving at or leaving an important place (a school, a grandparent's house)
 * is a family safety notice rather than an ordinary arrival, and goes to the
 * people picked here; nobody picked means everyone who may see that person.
 * The places themselves are drawn in Home Assistant. Only an admin can change
 * them; everyone else sees which places are important and who hears about them.
 */
export default function PlacesPanel({ places, members, canEdit, onSaved }: Props) {
  const [saving, setSaving] = useState<string | null>(null);

  const save = async (place: PlaceSetting, important: boolean, notify: string[]) => {
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
    void save(place, true, next);
  };

  if (places.length === 0) return null;

  return (
    <div className="glass-panel space-y-3 rounded-2xl border border-white/5 p-4" data-testid="places-panel">
      <div>
        <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-200">
          <MapPin size={15} className="text-purple-400" /> Places
        </h3>
        <p className="mt-1 text-xs text-slate-500">
          {canEdit
            ? 'Mark a place important to get a safety alert when someone arrives or leaves. Places are drawn in Home Assistant.'
            : 'Arriving at or leaving an important place sends a safety alert. An admin chooses the places.'}
        </p>
      </div>
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
                    onChange={(next) => void save(place, next, next ? place.notify : [])}
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
            {canEdit && place.important && members.length > 0 && (
              <div className="flex flex-wrap items-center gap-2 pb-1">
                <span className="text-xs text-slate-500">
                  {place.notify.length === 0 ? 'Alerts go to everyone. Or only:' : 'Alerts go to:'}
                </span>
                {members.map((m) => {
                  const on = place.notify.includes(m.username);
                  return (
                    <button
                      key={m.username}
                      type="button"
                      aria-pressed={on}
                      disabled={saving === place.id}
                      onClick={() => toggleMember(place, m.username)}
                      className={`min-h-11 rounded-full border px-3 text-xs transition ${
                        on
                          ? 'border-purple-400/60 bg-purple-500/20 text-purple-100'
                          : 'border-white/10 text-slate-400 hover:text-slate-200'
                      }`}
                    >
                      {m.name}
                    </button>
                  );
                })}
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
