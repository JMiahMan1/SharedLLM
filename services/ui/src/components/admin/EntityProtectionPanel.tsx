import { useMemo, useState, type FC } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Lock, ShieldCheck, ShieldOff, Trash2 } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import type { EntityProtection, UserProfile } from '../../types/api';
import EntitySearchDropdown from '../ui/EntitySearchDropdown';
import HelpTooltip from '../ui/HelpTooltip';

/**
 * Admin control over which Home Assistant entities normal users may touch.
 *
 * A protected entity is one whose device assignment is ignored: only admins,
 * the system default user, and the names on the entity's own permit list can
 * see or control it. Everyone else does not even see it in their entity list,
 * because the same rule hides it from `/execute/entity/search`. Releasing a
 * lock deletes it outright, so the entity reverts to plain assignment rules.
 *
 * Server-authoritative by design: nothing here is cached client-side, and a
 * refusal (an unknown username, say) surfaces as the error Identity returned
 * rather than being quietly stored.
 */

const formatWhen = (iso?: string | null) => {
  if (!iso) return '';
  const parsed = Date.parse(iso);
  return Number.isNaN(parsed) ? iso : new Date(parsed).toLocaleString();
};

export const EntityProtectionPanel: FC<{ users: UserProfile[] }> = ({ users }) => {
  const queryClient = useQueryClient();
  const [entityId, setEntityId] = useState('');
  const [permitted, setPermitted] = useState<string[]>([]);
  const [note, setNote] = useState('');

  const { data: protections = [], isPending, error } = useQuery<EntityProtection[]>({
    queryKey: ['entity-protection'],
    queryFn: () => api.getEntityProtections(),
  });

  const byEntity = useMemo(
    () => new Map(protections.map((p) => [p.entity_id, p])),
    [protections]
  );
  const selected = entityId ? byEntity.get(entityId) : undefined;

  // Picking an already-protected entity loads its current permit list, so
  // editing is a re-save rather than a second, competing lock. The draft is
  // reset during render rather than in an effect: an effect here would be a
  // cascading render, and would briefly show the previous entity's permits
  // under the new entity's name.
  const [draftFor, setDraftFor] = useState(entityId);
  if (entityId !== draftFor) {
    setDraftFor(entityId);
    setPermitted(selected ? selected.permitted_usernames : []);
  }

  // Admins and the system default user bypass every lock, so offering them as
  // permits would be a no-op that reads as if it did something.
  const alwaysAllowed = useMemo(
    () => users.filter((u) => u.is_admin || u.username.toLowerCase() === 'default'),
    [users]
  );
  const choosable = useMemo(
    () => users.filter((u) => !u.is_admin && u.username.toLowerCase() !== 'default'),
    [users]
  );

  const saveMutation = useMutation({
    mutationFn: (vars: { entityId: string; protected: boolean; permitted: string[]; note: string | null }) =>
      api.setEntityProtection(vars.entityId, {
        protected: vars.protected,
        permitted_usernames: vars.permitted,
        note: vars.note,
      }),
    onSuccess: (updated: EntityProtection) => {
      queryClient.invalidateQueries({ queryKey: ['entity-protection'] });
      setEntityId('');
      setPermitted([]);
      setNote('');
      toast.success(
        updated.permitted_usernames.length
          ? `${updated.entity_id} locked for everyone else`
          : `${updated.entity_id} locked to admins only`
      );
    },
    onError: (err: unknown) => {
      // Identity refuses an unknown username rather than storing a grant that
      // permits nobody; show exactly what it said.
      toast.error(err instanceof Error ? err.message : 'Failed to update protection');
    },
  });

  const toggle = (username: string) => {
    setPermitted((prev) =>
      prev.includes(username) ? prev.filter((u) => u !== username) : [...prev, username]
    );
  };

  return (
    <section className="glass-panel p-6 min-w-0 overflow-hidden" data-testid="entity-protection">
      <div className="mb-6 flex items-center justify-between gap-3">
        <div>
          <h3 className="flex items-center gap-3 text-xl font-bold text-white">
            <Lock size={20} className="text-emerald-300" />
            Entity Protection
          </h3>
          <p className="mt-1 text-sm text-slate-400">
            Lock an entity so only admins and the users you permit can see or control it.
          </p>
        </div>
        <HelpTooltip docName="architecture.md" sectionTitle="Entity Protection" label="Entity Protection" />
      </div>

      <p className="mb-4 rounded-lg border border-white/5 bg-black/20 p-3 text-[11px] leading-relaxed text-slate-400">
        A lock overrides the device assignments below entirely: while an entity is protected, an
        assignment naming a normal user grants them nothing. Protected entities also disappear from
        everyone else&apos;s device lists, so an unpermitted user never sees them at all.
      </p>

      <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-[1fr_220px_auto]">
        <EntitySearchDropdown
          value={entityId}
          onChange={setEntityId}
          placeholder="Search Home Assistant entities..."
          testId="protection-entity-search"
        />
        <input
          type="text"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Note (optional)"
          aria-label="Protection note"
          className="glass-input bg-black/30"
        />
        <button
          type="button"
          disabled={!entityId || saveMutation.isPending}
          onClick={() => {
            if (!entityId.trim()) {
              toast.error('Choose an entity to protect');
              return;
            }
            saveMutation.mutate({
              entityId: entityId.trim(),
              protected: true,
              permitted,
              note: note.trim() || null,
            });
          }}
          className="glass-button px-4 py-3 text-[10px] font-black uppercase tracking-widest disabled:opacity-40 pointer-coarse:min-h-11"
        >
          <ShieldCheck size={14} />
          {selected ? 'Update Lock' : 'Protect'}
        </button>
      </div>

      {entityId && (
        <div className="mb-4 rounded-xl border border-white/5 bg-black/20 p-3">
          <p className="mb-2 text-[11px] font-semibold text-slate-300">
            Who may still control <span className="font-mono text-emerald-300">{entityId}</span>
          </p>
          {alwaysAllowed.length > 0 && (
            <p className="mb-2 text-[10px] text-slate-500">
              Always allowed, lock or not:{' '}
              {alwaysAllowed.map((u) => (
                <span key={u.username} className="mr-1 font-mono text-slate-400">
                  @{u.username}
                </span>
              ))}
            </p>
          )}
          {choosable.length === 0 ? (
            <p className="text-[10px] text-slate-500">
              There are no other users to permit — only admins exist on this system.
            </p>
          ) : (
            <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {choosable.map((user) => {
                const on = permitted.includes(user.username.toLowerCase());
                return (
                  <li key={user.username}>
                    <label className="flex min-h-11 items-center justify-between gap-3 rounded-lg border border-white/5 bg-black/20 p-2 pointer-coarse:min-h-11">
                      <span className={`text-xs ${on ? 'text-emerald-300' : 'text-slate-400'}`}>
                        @{user.username}
                        {user.display_name ? (
                          <span className="ml-1 text-slate-500">{user.display_name}</span>
                        ) : null}
                      </span>
                      <input
                        type="checkbox"
                        checked={on}
                        disabled={saveMutation.isPending}
                        onChange={() => toggle(user.username.toLowerCase())}
                        aria-label={`Permit @${user.username}`}
                        className="h-4 w-4 accent-emerald-500 disabled:opacity-40"
                      />
                    </label>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}

      <div className="max-h-[28rem] space-y-3 overflow-y-auto pr-2">
        {isPending && <p className="text-xs text-slate-400">Loading protected entities…</p>}
        {error && (
          <p className="text-xs text-red-300">
            Could not load entity protection: {error instanceof Error ? error.message : 'unknown error'}
          </p>
        )}
        {!isPending && !error && protections.length === 0 && (
          <p className="text-xs text-slate-500">No entities are protected.</p>
        )}
        {protections.map((protection) => (
          <div key={protection.entity_id} className="glass-card flex flex-wrap items-center justify-between gap-3 p-4">
            <div className="min-w-0 flex-1">
              <p className="truncate font-mono text-sm text-white">{protection.entity_id}</p>
              <p className="mt-1 text-xs text-slate-400">
                {protection.permitted_usernames.length
                  ? `Also permitted: ${protection.permitted_usernames.map((u) => `@${u}`).join(', ')}`
                  : 'Admins and the default user only'}
              </p>
              {protection.granted_by && (
                <p className="mt-1 text-[10px] text-slate-500">
                  Locked by <span className="font-mono">@{protection.granted_by}</span>
                  {protection.granted_at ? ` on ${formatWhen(protection.granted_at)}` : ''}
                  {protection.note ? ` — “${protection.note}”` : ''}
                </p>
              )}
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => {
                  setEntityId(protection.entity_id);
                  setNote(protection.note ?? '');
                }}
                className="glass-button px-3 py-2 text-[10px] font-black uppercase tracking-widest pointer-coarse:min-h-11"
              >
                Edit
              </button>
              <button
                type="button"
                disabled={saveMutation.isPending}
                onClick={() =>
                  saveMutation.mutate({
                    entityId: protection.entity_id,
                    protected: false,
                    permitted: [],
                    note: null,
                  })
                }
                className="rounded-xl p-2 text-slate-400 transition hover:bg-red-500/10 hover:text-red-300 disabled:opacity-40 pointer-coarse:min-h-11"
                aria-label={`Release protection for ${protection.entity_id}`}
              >
                <ShieldOff size={16} />
              </button>
            </div>
          </div>
        ))}
      </div>

      {protections.length > 0 && (
        <p className="mt-4 flex items-start gap-2 text-[10px] text-slate-500">
          <Trash2 size={12} className="mt-0.5 shrink-0" />
          Releasing a lock restores plain device-assignment rules for that entity and forgets the
          permit list entirely.
        </p>
      )}
    </section>
  );
};

export default EntityProtectionPanel;
