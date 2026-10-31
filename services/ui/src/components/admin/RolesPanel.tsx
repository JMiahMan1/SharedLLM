import { useState, type FC } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { BadgeCheck, Plus, Trash2 } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import type { Role, UserProfile } from '../../types/api';
import { confirmDialog } from '../ui/confirmDialog';

/**
 * Admin > Users > Roles: named sets of permissions, and who holds them.
 *
 * A role only bundles permissions Identity enforces (services/identity/roles.py);
 * admins hold all of them without one. Built-in roles can be edited, not deleted.
 */

type Draft = { id?: number; name: string; description: string; permissions: string[]; members: string[] };

const EMPTY: Draft = { name: '', description: '', permissions: [], members: [] };

/** Household relationship roles; membership follows the family roster. */
const FAMILY_BUILTINS = new Set(['father', 'mother', 'teen', 'adult_child', 'child']);

const errorText = (err: unknown, fallback: string) => {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof detail === 'string' ? detail : fallback;
};

export const RolesPanel: FC<{ users: UserProfile[] }> = ({ users }) => {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<Draft | null>(null);

  const { data, isPending } = useQuery({ queryKey: ['roles'], queryFn: () => api.getRoles() });
  const roles = data?.roles ?? [];
  const catalog = data?.permissions ?? [];
  const describe = (key: string) => catalog.find((p) => p.key === key)?.description ?? key;
  // Admins already hold everything, so a role would change nothing for them.
  const assignable = users.filter((u) => !u.is_admin);

  const save = useMutation({
    mutationFn: async (d: Draft) => {
      const role = await api.saveRole(d);
      // Household roles get their members from each person's family role, so
      // the roster owns membership and this form only edits permissions.
      const isFamilyRole = FAMILY_BUILTINS.has(roles.find((r) => r.id === d.id)?.builtin_key ?? '');
      if (isFamilyRole) return role;
      // Membership is stored per user: re-save only the users whose roles change.
      const before = new Set(roles.find((r) => r.id === role.id)?.members ?? []);
      const after = new Set(d.members);
      for (const user of assignable) {
        const name = user.username;
        if (before.has(name) === after.has(name)) continue;
        const others = (user.role_ids ?? []).filter((id) => id !== role.id);
        await api.setUserRoles(name, after.has(name) ? [...others, role.id] : others);
      }
      return role;
    },
    onSuccess: (role) => {
      queryClient.invalidateQueries({ queryKey: ['roles'] });
      queryClient.invalidateQueries({ queryKey: ['users'] });
      setDraft(null);
      toast.success(`Saved ${role.name}`);
    },
    onError: (err) => toast.error(errorText(err, 'Could not save the role')),
  });

  const remove = useMutation({
    mutationFn: (role: Role) => api.deleteRole(role.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['roles'] });
      queryClient.invalidateQueries({ queryKey: ['users'] });
      setDraft(null);
      toast.success('Role deleted');
    },
    onError: (err) => toast.error(errorText(err, 'Could not delete the role')),
  });

  const toggle = (field: 'permissions' | 'members', value: string) =>
    setDraft((d) => d && {
      ...d,
      [field]: d[field].includes(value) ? d[field].filter((v) => v !== value) : [...d[field], value],
    });

  return (
    <section className="glass-panel p-4 sm:p-6 min-w-0 overflow-hidden" data-testid="roles-panel">
      <div className="mb-4 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="flex items-center gap-3 text-xl font-bold text-white">
            <BadgeCheck size={20} className="text-sky-300" />
            Roles
          </h3>
          <p className="mt-1 text-sm text-slate-400">
            What people can do beyond their own account. Admins can do everything without a role. Household roles
            (Father, Mother, Teen, Adult child, Child) live here too; their members follow each person&apos;s family
            role.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setDraft({ ...EMPTY })}
          className="glass-button shrink-0 px-4 min-h-11 text-[10px] font-black uppercase tracking-widest"
        >
          <Plus size={14} />
          New role
        </button>
      </div>

      {isPending && <p className="text-sm text-slate-500">Loading roles…</p>}

      <ul className="space-y-3">
        {roles.map((role) => (
          <li key={role.id}>
            <button
              type="button"
              onClick={() => setDraft({ ...role })}
              className="glass-card w-full p-4 text-left transition hover:border-white/20"
            >
              <span className="flex flex-wrap items-center gap-2">
                <span className="font-semibold text-white">{role.name}</span>
                {role.builtin && (
                  <span className="rounded-full bg-white/5 px-2 py-0.5 text-[10px] font-black uppercase tracking-widest text-slate-400">
                    Built-in
                  </span>
                )}
                {FAMILY_BUILTINS.has(role.builtin_key ?? '') && (
                  <span className="rounded-full bg-sky-500/15 px-2 py-0.5 text-[10px] font-black uppercase tracking-widest text-sky-200">
                    Family
                  </span>
                )}
              </span>
              {role.description && <span className="mt-1 block break-words text-xs text-slate-400">{role.description}</span>}
              <span className="mt-2 block text-[11px] text-slate-500">
                {role.permissions.length ? role.permissions.map(describe).join(' · ') : 'Nothing beyond assigned devices'}
              </span>
              <span className="mt-1 block text-[11px] text-slate-500">
                {role.members.length ? role.members.map((m) => `@${m}`).join(', ') : 'Nobody has this role'}
              </span>
            </button>
          </li>
        ))}
      </ul>

      {draft && (
        <form
          className="mt-4 space-y-4 rounded-2xl border border-white/10 bg-black/20 p-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (!draft.name.trim()) {
              toast.error('Give the role a name');
              return;
            }
            save.mutate(draft);
          }}
        >
          <label className="block space-y-1">
            <span className="text-xs font-semibold text-slate-300">Name</span>
            <input
              value={draft.name}
              onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              className="glass-input w-full"
              aria-label="Role name"
            />
          </label>
          <label className="block space-y-1">
            <span className="text-xs font-semibold text-slate-300">Description</span>
            <input
              value={draft.description}
              onChange={(e) => setDraft({ ...draft, description: e.target.value })}
              className="glass-input w-full"
              aria-label="Role description"
            />
          </label>

          <fieldset className="space-y-2">
            <legend className="mb-1 text-xs font-semibold text-slate-300">Can</legend>
            {catalog.map((p) => (
              <label key={p.key} className="flex min-h-11 items-center gap-3 rounded-xl bg-white/5 px-3 text-sm text-slate-200">
                <input
                  type="checkbox"
                  checked={draft.permissions.includes(p.key)}
                  onChange={() => toggle('permissions', p.key)}
                  className="h-4 w-4"
                />
                {p.description}
              </label>
            ))}
          </fieldset>

          {FAMILY_BUILTINS.has(roles.find((r) => r.id === draft.id)?.builtin_key ?? '') ? (
            <p className="rounded-xl bg-white/5 px-3 py-3 text-xs text-slate-400">
              Household roles are assigned from each person&apos;s family role in Admin → Users → Family or Settings →
              Family roles. This form edits what the role allows.
            </p>
          ) : (
            <fieldset className="space-y-2">
              <legend className="mb-1 text-xs font-semibold text-slate-300">People with this role</legend>
              {assignable.length === 0 && <p className="text-xs text-slate-500">Everyone here is an admin.</p>}
              {assignable.map((u) => (
                <label key={u.username} className="flex min-h-11 items-center gap-3 rounded-xl bg-white/5 px-3 text-sm text-slate-200">
                  <input
                    type="checkbox"
                    checked={draft.members.includes(u.username)}
                    onChange={() => toggle('members', u.username)}
                    className="h-4 w-4"
                  />
                  {u.full_name || u.display_name || u.username}
                </label>
              ))}
            </fieldset>
          )}

          <div className="flex flex-wrap items-center gap-2">
            <button type="submit" disabled={save.isPending} className="glass-button px-4 min-h-11 text-[10px] font-black uppercase tracking-widest disabled:opacity-40">
              Save role
            </button>
            <button type="button" onClick={() => setDraft(null)} className="rounded-xl px-4 min-h-11 text-xs text-slate-400 hover:bg-white/5 hover:text-white">
              Cancel
            </button>
            {draft.id && !roles.find((r) => r.id === draft.id)?.builtin && (
              <button
                type="button"
                disabled={remove.isPending}
                onClick={async () => {
                  const role = roles.find((r) => r.id === draft.id);
                  if (role && await confirmDialog({ title: 'Delete this role?', body: `Delete the ${role.name} role? Everyone with it loses what it allowed.`, confirmLabel: 'Delete', danger: true })) {
                    remove.mutate(role);
                  }
                }}
                className="ml-auto flex items-center gap-2 rounded-xl px-4 min-h-11 text-xs text-rose-300 hover:bg-rose-500/10"
              >
                <Trash2 size={14} />
                Delete
              </button>
            )}
          </div>
        </form>
      )}
    </section>
  );
};

export default RolesPanel;
