import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Copy, Eye, EyeOff } from 'lucide-react';
import SetupSection from './SetupSection';
import { BTN } from './setupStyles';
import { failureText } from '../../lib/discoveredDevices';
import {
  bulkActions, getDuplicates, hideCopiesOf, matchLabel, setDeviceHidden, showCopiesOf, type DuplicateCopy,
} from '../../lib/duplicates';
import { ago } from '../../lib/hardware';

const DUPLICATES_KEY = ['homehub-duplicates'];

/*
 * Devices > Your devices > Duplicates (admins). The same bulb is often in the hub twice: once
 * through Matter, once through WiZ. Deleting the Matter copy would take it off the fabric, so a
 * copy is hidden instead: it keeps working (automations, schedules, a command naming it), and
 * only the rooms page, Jarvis's lists and voice leave it out. Each group says how the copies were
 * matched and which integration each comes from; one tap hides every copy of an integration.
 */
export default function DuplicatesPanel() {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: DUPLICATES_KEY, queryFn: getDuplicates, retry: false, refetchInterval: 60_000 });
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: DUPLICATES_KEY });
    void qc.invalidateQueries({ queryKey: ['homehub-hardware'] });
    void qc.invalidateQueries({ queryKey: ['dashboard', 'rooms'] });
  };
  const one = useMutation({
    mutationFn: ({ copy, hidden }: { copy: DuplicateCopy; hidden: boolean }) => setDeviceHidden(copy.id, hidden),
    onSettled: refresh,
  });
  const bulk = useMutation({
    mutationFn: ({ domain, hide }: { domain: string; hide: boolean }) => (hide ? hideCopiesOf(domain) : showCopiesOf(domain)),
    onSuccess: (data) => qc.setQueryData(DUPLICATES_KEY, { groups: data.groups, integrations: data.integrations }),
    onSettled: refresh,
  });

  if (list.error) {
    return (
      <SetupSection title="Duplicates" icon={Copy} testId="duplicates">
        <p className="p-4 text-sm text-amber-300/90" role="alert">{failureText(list.error)}</p>
      </SetupSection>
    );
  }
  const data = list.data;
  if (!data || data.groups.length === 0) return null;
  const busy = one.isPending || bulk.isPending;
  const error = one.error ?? bulk.error;

  return (
    <SetupSection title="Duplicates" icon={Copy} testId="duplicates"
      aside={<span className="text-xs text-slate-500">{data.groups.length} seen twice</span>}>
      <div className="space-y-3 p-3 sm:p-4">
        <p className="text-sm text-slate-400">
          These devices were added through two integrations. Hide one copy: it keeps working, and rooms, Jarvis and
          voice use the copy that shows.
        </p>
        <div className="flex flex-wrap gap-2" role="group" aria-label="Hide or show every copy from one integration"
          data-testid="duplicates-bulk">
          {bulkActions(data).map((a) => (
            <span key={a.domain} className="contents">
              {a.canHide && (
                <button type="button" className={BTN} disabled={busy}
                  onClick={() => bulk.mutate({ domain: a.domain, hide: true })}>
                  <EyeOff size={14} aria-hidden /> Hide all {a.label} copies
                </button>
              )}
              {a.canShow && (
                <button type="button" className={BTN} disabled={busy}
                  onClick={() => bulk.mutate({ domain: a.domain, hide: false })}>
                  <Eye size={14} aria-hidden /> Show all {a.label} copies
                </button>
              )}
            </span>
          ))}
        </div>
        {error && <p className="text-xs text-amber-300/90" role="alert">{failureText(error)}</p>}
        <ul className="space-y-2">
          {data.groups.map((g) => (
            <li key={g.id} className="rounded-xl border border-white/10 bg-black/20" data-testid="duplicate-group"
              aria-label={g.name}>
              <div className="flex min-w-0 flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 px-3 pt-2.5">
                <h3 className="min-w-0 break-words text-sm font-semibold text-white">{g.name}</h3>
                <span className="text-xs text-slate-500">{matchLabel(g)}</span>
              </div>
              <ul className="mt-1 divide-y divide-white/5">
                {g.devices.map((c) => (
                  <li key={c.id} className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2"
                    data-testid="duplicate-copy" data-hidden={c.hidden ? 'true' : 'false'}>
                    <div className="min-w-0 flex-1 basis-40">
                      <p className="flex flex-wrap items-center gap-2 text-sm text-slate-200">
                        <span className="rounded-md bg-white/10 px-1.5 py-0.5 text-xs font-medium text-slate-100">
                          {c.label || c.domain || 'Unknown'}
                        </span>
                        {c.hidden && <span className="text-xs text-amber-200/90">Hidden</span>}
                      </p>
                      <p className="mt-0.5 truncate text-xs text-slate-500">
                        {[c.entry_title !== c.label ? c.entry_title : null, `${c.entity_ids.length} ${c.entity_ids.length === 1 ? 'entity' : 'entities'}`,
                          c.last_updated ? `updated ${ago(c.last_updated)}` : 'no updates yet'].filter(Boolean).join(' · ')}
                      </p>
                    </div>
                    <button type="button" className={BTN} disabled={busy}
                      aria-label={`${c.hidden ? 'Show' : 'Hide'} the ${c.label || c.domain} copy of ${g.name}`}
                      onClick={() => one.mutate({ copy: c, hidden: !c.hidden })}>
                      {c.hidden ? <Eye size={14} aria-hidden /> : <EyeOff size={14} aria-hidden />}
                      {c.hidden ? 'Show' : 'Hide'}
                    </button>
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      </div>
    </SetupSection>
  );
}
