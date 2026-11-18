import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeftRight, Combine, Copy, Eye, EyeOff, Unlink, X } from 'lucide-react';
import SetupSection from './SetupSection';
import { BTN } from './setupStyles';
import { failureText } from '../../lib/discoveredDevices';
import {
  bulkActions, clearRule, combineAllLike, combineCopies, combinedLabel, combinedStatus, defaultMain, getDuplicates,
  hideCopiesOf, matchLabel, setDeviceHidden, showCopiesOf, swapMain, uncombine, type DuplicateCopy, type DuplicateGroup,
  type Duplicates,
} from '../../lib/duplicates';
import { ago } from '../../lib/hardware';

const DUPLICATES_KEY = ['homehub-duplicates'];

/*
 * Devices > Your devices > Duplicates (admins). The same bulb is often in the hub twice: once
 * through Matter, once through WiZ. Deleting the Matter copy would take it off the fabric, so a
 * copy is hidden instead: it keeps working (automations, schedules, a command naming it), and
 * only the rooms page, Jarvis's lists and voice leave it out. Each group says how the copies were
 * matched and which integration each comes from; one tap hides every copy of an integration.
 *
 * Or the two copies are combined into one device: a person picks the main (the copy commands go to
 * first) and the other copy is kept as its fallback, used when the main does not answer. "Combine all
 * like this" does every pair of those two integrations the same way and keeps it as the household rule.
 */

type CombineAction =
  | { kind: 'combine'; main: string; fallback: string }
  | { kind: 'all'; mainDomain: string; fallbackDomain: string }
  | { kind: 'uncombine' | 'swap'; device: string }
  | { kind: 'clear'; domains: string[] };

function runCombine(a: CombineAction): Promise<Duplicates> {
  if (a.kind === 'combine') return combineCopies(a.main, a.fallback);
  if (a.kind === 'all') return combineAllLike(a.mainDomain, a.fallbackDomain);
  if (a.kind === 'clear') return clearRule(a.domains);
  return a.kind === 'swap' ? swapMain(a.device) : uncombine(a.device);
}

const label = (c: DuplicateCopy) => c.label || c.domain || 'Unknown';

/** Before combining: pick the main, then combine this pair or every pair like it. */
function CombineChoice({ group, busy, onRun }: { group: DuplicateGroup; busy: boolean; onRun: (a: CombineAction) => void }) {
  const [mainId, setMainId] = useState(() => defaultMain(group));
  const main = group.devices.find((c) => c.id === mainId) ?? group.devices[0];
  const fallback = group.devices.find((c) => c.id !== main.id)!;
  return (
    <div className="space-y-2 border-t border-white/5 px-3 py-2.5" data-testid="combine-choice">
      <p className="text-xs text-slate-400">
        Or combine them into one device: commands go to the main, and if it does not answer, the fallback takes over.
      </p>
      <div className="flex flex-wrap items-center gap-2" role="radiogroup" aria-label={`Main copy of ${group.name}`}>
        <span className="text-xs text-slate-500">Main:</span>
        {group.devices.map((c) => (
          <button key={c.id} type="button" role="radio" aria-checked={c.id === main.id} disabled={busy}
            aria-label={`${label(c)} as the main for ${group.name}`}
            onClick={() => setMainId(c.id)}
            className={`${BTN} ${c.id === main.id ? 'border-sky-400/60 bg-sky-500/15 text-white' : ''}`}>
            {label(c)}
          </button>
        ))}
      </div>
      <p className="text-xs text-slate-500" data-testid="combine-preview">
        {label(main)} first, {label(fallback)} as the fallback.
      </p>
      <div className="flex flex-wrap gap-2">
        <button type="button" className={BTN} disabled={busy} aria-label={`Combine ${group.name}`}
          onClick={() => onRun({ kind: 'combine', main: main.id, fallback: fallback.id })}>
          <Combine size={14} aria-hidden /> Combine
        </button>
        {main.domain && fallback.domain && (
          <button type="button" className={BTN} disabled={busy}
            aria-label={`Combine all ${label(main)} and ${label(fallback)} copies with ${label(main)} first`}
            onClick={() => onRun({ kind: 'all', mainDomain: main.domain!, fallbackDomain: fallback.domain! })}>
            <Combine size={14} aria-hidden /> Combine all like this
          </button>
        )}
      </div>
    </div>
  );
}

/** A combined device: which copy is the main, what it is using now, swap and uncombine. */
function CombinedControls({ group, busy, onRun }: { group: DuplicateGroup; busy: boolean; onRun: (a: CombineAction) => void }) {
  const c = group.combined!;
  const fallbackName = c.fallback_label || c.fallback_domain || 'the fallback';
  return (
    <div className="space-y-2 border-t border-white/5 px-3 py-2.5" data-testid="combined">
      <p className="text-sm text-slate-200">{combinedLabel(c)}</p>
      <p className={`text-xs ${c.using === 'main' ? 'text-slate-500' : 'text-amber-200/90'}`} data-testid="combined-status">
        {combinedStatus(c)}
      </p>
      <div className="flex flex-wrap gap-2">
        <button type="button" className={BTN} disabled={busy} aria-label={`Make ${fallbackName} the main for ${group.name}`}
          onClick={() => onRun({ kind: 'swap', device: c.main })}>
          <ArrowLeftRight size={14} aria-hidden /> Make {fallbackName} the main
        </button>
        <button type="button" className={BTN} disabled={busy} aria-label={`Uncombine ${group.name}`}
          onClick={() => onRun({ kind: 'uncombine', device: c.main })}>
          <Unlink size={14} aria-hidden /> Uncombine
        </button>
      </div>
    </div>
  );
}
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
    onSuccess: (data) => qc.setQueryData(DUPLICATES_KEY, { groups: data.groups, integrations: data.integrations, rules: data.rules ?? [] }),
    onSettled: refresh,
  });
  const combo = useMutation({
    mutationFn: runCombine,
    onSuccess: (data) => qc.setQueryData(DUPLICATES_KEY, { groups: data.groups, integrations: data.integrations, rules: data.rules ?? [] }),
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
  const busy = one.isPending || bulk.isPending || combo.isPending;
  const error = one.error ?? bulk.error ?? combo.error;
  const run = (a: CombineAction) => combo.mutate(a);

  return (
    <SetupSection title="Duplicates" icon={Copy} testId="duplicates"
      aside={<span className="text-xs text-slate-500">{data.groups.length} seen twice</span>}>
      <div className="space-y-3 p-3 sm:p-4">
        <p className="text-sm text-slate-400">
          These devices were added through two integrations. Combine the two copies into one device with a main and a
          fallback, or hide one copy: it keeps working, and rooms, Jarvis and voice use the copy that shows.
        </p>
        {(data.rules ?? []).map((r) => (
          <p key={r.domains.join('|')} className="flex flex-wrap items-center gap-2 text-xs text-slate-400" data-testid="duplicates-rule">
            <span className="min-w-0 flex-1 basis-48">Household rule: prefer {r.main_label}, fall back to {r.fallback_label}.</span>
            <button type="button" className={BTN} disabled={busy}
              aria-label={`Forget the rule to prefer ${r.main_label} over ${r.fallback_label}`}
              onClick={() => run({ kind: 'clear', domains: r.domains })}>
              <X size={14} aria-hidden /> Forget rule
            </button>
          </p>
        ))}
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
              aria-label={g.name} data-combined={g.combined ? 'true' : 'false'}>
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
                        {c.role === 'main' && <span className="text-xs text-sky-200/90">Main</span>}
                        {c.role === 'fallback' && <span className="text-xs text-slate-400">Fallback</span>}
                        {c.hidden && !c.role && <span className="text-xs text-amber-200/90">Hidden</span>}
                      </p>
                      <p className="mt-0.5 truncate text-xs text-slate-500">
                        {[c.entry_title !== c.label ? c.entry_title : null, `${c.entity_ids.length} ${c.entity_ids.length === 1 ? 'entity' : 'entities'}`,
                          c.last_updated ? `updated ${ago(c.last_updated)}` : 'no updates yet'].filter(Boolean).join(' · ')}
                      </p>
                    </div>
                    {!g.combined && (
                      <button type="button" className={`${BTN} min-w-[5.5rem] justify-center`} disabled={busy}
                        aria-label={`${c.hidden ? 'Show' : 'Hide'} the ${c.label || c.domain} copy of ${g.name}`}
                        onClick={() => one.mutate({ copy: c, hidden: !c.hidden })}>
                        {c.hidden ? <Eye size={14} aria-hidden /> : <EyeOff size={14} aria-hidden />}
                        {c.hidden ? 'Show' : 'Hide'}
                      </button>
                    )}
                  </li>
                ))}
              </ul>
              {g.combined && <CombinedControls group={g} busy={busy} onRun={run} />}
              {!g.combined && g.can_combine && <CombineChoice key={g.id} group={g} busy={busy} onRun={run} />}
            </li>
          ))}
        </ul>
      </div>
    </SetupSection>
  );
}
