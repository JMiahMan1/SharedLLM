import { useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import type { FamilyRecord, FamilyRole } from '../../types/api';

const ROLE_LABELS: Record<FamilyRole, string> = {
  father: 'Father (admin)',
  mother: 'Mother',
  child: 'Child',
  teen: 'Teen',
  adult_child: 'Adult child',
  other: 'Other adult',
};

const ROLE_HELP: Record<FamilyRole, string> = {
  father: 'The admin account. Fatherhood does not grant admin by itself.',
  mother: 'Runs the house: devices, family links and shared calendars. No account or server settings.',
  child: 'A minor. Family circle activity sharing stays on and cannot be switched off.',
  teen: 'A minor. Family circle activity sharing stays on and cannot be switched off.',
  adult_child: 'An adult in the household. Can choose their own activity sharing.',
  other: 'An adult with no household role beyond their explicit permissions.',
};

interface Draft {
  role: FamilyRole;
  mother: string;
}

/**
 * Edit household relationships without exposing credentials.
 *
 * Father is the admin flag; choosing "Father" here never promotes an account.
 * A mother or another household manager can edit children and other adults,
 * but cannot edit a parent or make someone a parent.
 */
export default function FamilyPanel() {
  const queryClient = useQueryClient();
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [saving, setSaving] = useState<string | null>(null);

  const { data: me } = useQuery({
    queryKey: ['me'],
    queryFn: () => api.getMe(),
    staleTime: 300_000,
  });
  const canManage = Boolean(me?.is_admin || me?.permissions?.includes('family.manage'));

  const { data: people = [] } = useQuery({
    queryKey: ['family-roster'],
    queryFn: () => api.getFamily(),
    enabled: canManage,
  });

  const mothers = useMemo(
    () => (Array.isArray(people) ? people : []).filter((p) => p.family_role === 'mother'),
    [people],
  );

  if (!canManage) return null;

  const draftFor = (person: FamilyRecord): Draft =>
    drafts[person.username] ?? {
      role: person.family_role,
      mother: person.mother_username ?? '',
    };

  const patch = (person: FamilyRecord, updates: Partial<Draft>) => {
    setDrafts((current) => ({
      ...current,
      [person.username]: { ...draftFor(person), ...updates },
    }));
  };

  const save = async (person: FamilyRecord) => {
    const draft = draftFor(person);
    setSaving(person.username);
    try {
      await api.updateFamilyMember(person.username, {
        family_role: draft.role,
        ...(draft.role === 'child' || draft.role === 'teen'
          ? { mother_username: draft.mother || null, clear_mother: !draft.mother }
          : { clear_mother: true }),
      });
      setDrafts((current) => {
        const next = { ...current };
        delete next[person.username];
        return next;
      });
      await queryClient.invalidateQueries({ queryKey: ['family-roster'] });
      await queryClient.invalidateQueries({ queryKey: ['me'] });
      await queryClient.invalidateQueries({ queryKey: ['family-directory'] });
      toast.success(`Updated ${person.display_name || person.username}`);
    } catch {
      toast.error('Could not update that family role');
    } finally {
      setSaving(null);
    }
  };

  const canEdit = (person: FamilyRecord) => {
    if (me?.is_admin) return true;
    if (person.is_admin || person.family_role === 'father' || person.family_role === 'mother') {
      return false;
    }
    return person.username !== me?.username;
  };

  return (
    <div className="glass-panel rounded-2xl p-4 space-y-4" data-testid="family-panel">
      <div>
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider">Family roles</h2>
        <p className="mt-1 text-xs text-slate-500">
          Father is the admin account. Mother can run the household but not administer accounts or the
          server. Children and teens keep family circle sharing on; adult children can turn theirs off.
        </p>
      </div>

      <div className="space-y-3">
        {people.map((person) => {
          const draft = draftFor(person);
          const editable = canEdit(person);
          const roleOptions: FamilyRole[] = me?.is_admin
            ? ['father', 'mother', 'child', 'teen', 'adult_child', 'other']
            : ['child', 'teen', 'adult_child', 'other'];
          return (
            <div
              key={person.username}
              className="rounded-xl border border-white/10 bg-white/5 p-3 space-y-3"
              data-testid={`family-row-${person.username}`}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-white">
                    {person.display_name || person.username}
                  </p>
                  <p className="truncate text-xs text-slate-500">@{person.username}</p>
                </div>
                {person.sharing_locked && (
                  <span className="shrink-0 rounded-full bg-amber-500/15 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-amber-200">
                    Minor · sharing locked on
                  </span>
                )}
              </div>

              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <label className="space-y-1 text-xs text-slate-400">
                  <span>Family role</span>
                  <select
                    className="glass-input min-h-11 w-full px-3 py-2 text-sm"
                    value={draft.role}
                    disabled={!editable}
                    onChange={(event) => patch(person, { role: event.target.value as FamilyRole })}
                    data-testid={`family-role-${person.username}`}
                  >
                    {roleOptions.map((role) => (
                      <option key={role} value={role}>
                        {ROLE_LABELS[role]}
                      </option>
                    ))}
                  </select>
                </label>

                {(draft.role === 'child' || draft.role === 'teen') && (
                  <label className="space-y-1 text-xs text-slate-400">
                    <span>Mother</span>
                    <select
                      className="glass-input min-h-11 w-full px-3 py-2 text-sm"
                      value={draft.mother}
                      disabled={!editable}
                      onChange={(event) => patch(person, { mother: event.target.value })}
                      data-testid={`family-mother-${person.username}`}
                    >
                      <option value="">Not set</option>
                      {mothers
                        .filter((mother) => mother.username !== person.username)
                        .map((mother) => (
                          <option key={mother.username} value={mother.username}>
                            {mother.display_name || mother.username}
                          </option>
                        ))}
                    </select>
                  </label>
                )}
              </div>

              <p className="text-[11px] text-slate-500">{ROLE_HELP[draft.role]}</p>

              {editable && (
                <button
                  type="button"
                  onClick={() => void save(person)}
                  disabled={saving === person.username}
                  className="glass-button min-h-11 px-4 py-2 text-sm disabled:opacity-50"
                  data-testid={`family-save-${person.username}`}
                >
                  {saving === person.username ? 'Saving…' : 'Save'}
                </button>
              )}
            </div>
          );
        })}
        {people.length === 0 && (
          <p className="rounded-xl border border-white/5 bg-white/5 px-4 py-6 text-center text-sm text-slate-500">
            No family members found.
          </p>
        )}
      </div>
    </div>
  );
}
