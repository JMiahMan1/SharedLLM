import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { CheckCircle2, Cpu, ExternalLink, FlaskConical, Info, Loader2, RotateCcw, Search, TriangleAlert } from 'lucide-react';
import { confirmDialog } from '../ui/confirmDialog';
import { failureText } from '../../lib/discoveredDevices';
import {
  KIND_LABEL, applyRequestFor, applyTemplate, catalogKey, deviceTemplateKey, filterCatalog, forgetMeasuredTemplate,
  getDeviceTemplate, getTemplateCatalog, measuredLabel, pasteProblem, restorePreviousTemplate, saveMeasuredTemplate,
  templateName,
  type ApplyRequest, type ApplyResult, type CatalogTemplate, type DeviceTemplate, type TemplateKind,
} from '../../lib/tasmotaTemplates';
import TasmotaPinFinder from './TasmotaPinFinder';

const CARD = 'rounded-2xl border border-white/10 bg-white/[0.03]';
const INPUT = 'min-h-11 w-full min-w-0 rounded-xl border border-white/10 bg-black/30 px-3 text-sm text-white placeholder:text-slate-500';
const BTN = 'inline-flex min-h-11 items-center justify-center gap-1.5 rounded-xl border border-white/10 bg-white/[0.04] px-3 text-sm text-slate-200 hover:bg-white/10 disabled:opacity-50';
const PRIMARY = 'inline-flex min-h-11 items-center justify-center gap-1.5 rounded-xl bg-blue-600 px-4 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-50';
const SHOWN = 8;

type Choice = { kind: 'catalog'; entry: CatalogTemplate } | { kind: 'paste' };

function label(e: CatalogTemplate) {
  return `${e.manufacturer} ${e.model}`;
}

function Row({ entry, chosen, onChoose, reasons, testId, onForget }: {
  entry: CatalogTemplate; chosen: boolean; onChoose: () => void; reasons?: string[]; testId?: string;
  onForget?: () => void;
}) {
  const measured = measuredLabel(entry);
  return (
    <li className="flex flex-col gap-2 py-2 sm:flex-row sm:items-start sm:justify-between"
      data-testid={testId ?? (reasons ? 'template-suggestion' : 'template-option')} data-source={entry.source ?? 'catalog'}>
      <div className="min-w-0">
        <p className="break-words text-sm text-slate-100">{label(entry)}</p>
        {measured && (
          <p className="mt-0.5 inline-flex items-center gap-1 rounded-full bg-emerald-500/15 px-2 py-0.5 text-[11px] text-emerald-200"
            data-testid="measured-label">
            <FlaskConical size={11} aria-hidden /> {measured}{entry.measured_at ? `, ${entry.measured_at}` : ''}
          </p>
        )}
        <p className="break-words text-xs text-slate-400">
          {KIND_LABEL[entry.kind]?.replace(/s$/, '') ?? entry.kind} · {entry.chip} · &quot;{entry.name}&quot;
        </p>
        {reasons && reasons.length > 0 && (
          <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs text-slate-300" aria-label={`Why ${label(entry)}`}>
            {reasons.map((r) => <li key={r} className="break-words">{r}</li>)}
          </ul>
        )}
        {entry.notes && <p className="mt-1 break-words text-xs text-slate-500">{entry.notes}</p>}
      </div>
      <div className="flex shrink-0 gap-2">
        {onForget && (
          <button type="button" className={BTN} onClick={onForget} aria-label={`Forget ${label(entry)}`}>Forget</button>
        )}
        <button type="button" className={`${BTN} ${chosen ? 'border-blue-400/60 bg-blue-500/20 text-white' : ''}`}
          aria-pressed={chosen} onClick={onChoose}>
          {chosen ? 'Chosen' : 'Choose'}
        </button>
      </div>
    </li>
  );
}

function Result({ result }: { result: ApplyResult }) {
  const ok = result.verified;
  return (
    <div className={`flex gap-2 rounded-2xl border p-3 text-sm ${ok ? 'border-emerald-400/30 bg-emerald-500/10 text-emerald-100'
      : 'border-amber-400/30 bg-amber-500/10 text-amber-100'}`} role="status" data-testid="template-result">
      {ok ? <CheckCircle2 size={16} className="mt-0.5 shrink-0" aria-hidden /> : <TriangleAlert size={16} className="mt-0.5 shrink-0" aria-hidden />}
      <p className="min-w-0 break-words">{result.message}</p>
    </div>
  );
}

/** "Save this device's template": keep the template a working device runs, as measured on it. */
function SaveThisDevice({ entryId, d }: { entryId: string; d: DeviceTemplate }) {
  const qc = useQueryClient();
  const [maker, setMaker] = useState('');
  const [model, setModel] = useState('');
  const [kind, setKind] = useState<TemplateKind>(d.current.light ? 'bulb' : d.current.relays ? 'plug' : 'bulb');
  const [done, setDone] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: () => saveMeasuredTemplate(entryId, { manufacturer: maker.trim(), model: model.trim(), kind }),
    onSuccess: (r) => {
      setDone(r.message);
      void qc.invalidateQueries({ queryKey: catalogKey });
      void qc.invalidateQueries({ queryKey: deviceTemplateKey(entryId) });
    },
    onError: (err) => toast.error(failureText(err)),
  });
  return (
    <details className="group border-t border-white/5 pt-2" data-testid="template-save-device">
      <summary className="flex min-h-11 cursor-pointer list-none items-center text-xs font-medium text-slate-400 [&::-webkit-details-marker]:hidden">
        Save this device&apos;s template
      </summary>
      {done ? (
        <p className="flex gap-1.5 text-sm text-emerald-200" role="status"><CheckCircle2 size={16} className="mt-0.5 shrink-0" aria-hidden /> {done}</p>
      ) : (
        <form className="space-y-2" onSubmit={(e) => { e.preventDefault(); if (model.trim()) save.mutate(); }}>
          <p className="text-xs text-slate-400">
            It works, so keep its template (&quot;{d.current.template_name}&quot;) for other devices like it. The hub only reads it.
          </p>
          <div className="grid gap-2 sm:grid-cols-3">
            <label className="block text-xs text-slate-400">Manufacturer
              <input className={`${INPUT} mt-1`} value={maker} maxLength={60} onChange={(e) => setMaker(e.target.value)}
                placeholder="As printed on the box" autoComplete="off" />
            </label>
            <label className="block text-xs text-slate-400">Model
              <input className={`${INPUT} mt-1`} value={model} maxLength={60} onChange={(e) => setModel(e.target.value)}
                placeholder="Model" autoComplete="off" />
            </label>
            <label className="block text-xs text-slate-400">Kind
              <select className={`${INPUT} mt-1`} value={kind} onChange={(e) => setKind(e.target.value as TemplateKind)}>
                {(Object.keys(KIND_LABEL) as TemplateKind[]).map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
              </select>
            </label>
          </div>
          <button type="submit" className={PRIMARY} disabled={!model.trim() || save.isPending}>
            {save.isPending ? <Loader2 size={14} className="animate-spin" aria-hidden /> : null} Save as measured
          </button>
        </form>
      )}
    </details>
  );
}

/**
 * Devices > a Tasmota device > Device template. A reflashed bulb or plug needs its template (Tasmota's GPIO map)
 * before it can drive its LEDs or relays. Shows the device's template now, the hub's suggestions with their
 * reasons, a searchable catalog and a paste box; loading one asks first (the device restarts) and can be undone.
 */
export default function TasmotaTemplateSection({ entryId, deviceName }: { entryId: string; deviceName: string }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: deviceTemplateKey(entryId), queryFn: () => getDeviceTemplate(entryId), retry: false });
  const catalog = useQuery({ queryKey: catalogKey, queryFn: getTemplateCatalog, retry: false, staleTime: 3_600_000 });
  const [choice, setChoice] = useState<Choice | null>(null);
  const [paste, setPaste] = useState('');
  const [search, setSearch] = useState('');
  const [kind, setKind] = useState<TemplateKind | ''>('');
  const [result, setResult] = useState<ApplyResult | null>(null);

  const done = (r: ApplyResult) => {
    setResult(r);
    setChoice(null);
    void qc.invalidateQueries({ queryKey: deviceTemplateKey(entryId) });
    void qc.invalidateQueries({ queryKey: ['homehub-hardware'] });
  };
  const apply = useMutation({ mutationFn: (what: ApplyRequest) => applyTemplate(entryId, what), onSuccess: done,
    onError: (err) => toast.error(failureText(err)) });
  const restore = useMutation({ mutationFn: () => restorePreviousTemplate(entryId), onSuccess: done,
    onError: (err) => toast.error(failureText(err)) });
  const forget = useMutation({ mutationFn: (id: string) => forgetMeasuredTemplate(id),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: catalogKey }); },
    onError: (err) => toast.error(failureText(err)) });
  const busy = apply.isPending || restore.isPending;

  const matches = useMemo(() => filterCatalog(catalog.data ?? [], search, kind), [catalog.data, search, kind]);
  const d = q.data;
  const pasteError = choice?.kind === 'paste' ? pasteProblem(paste) : null;
  const chosenName = choice?.kind === 'catalog' ? label(choice.entry) : templateName(paste.trim()) ?? 'the pasted template';
  const previous = result?.previous_template ?? d?.previous_template ?? null;

  const onApply = async () => {
    if (!choice || pasteError) return;
    const current = d?.current.template_name ? ` Its current template ("${d.current.template_name}") is kept so it can be restored.` : '';
    const yes = await confirmDialog({
      title: `Load this template onto ${deviceName}?`,
      body: `${deviceName} gets the ${chosenName} template. The device restarts and is offline for a few seconds while `
        + `the hub waits for it and checks the result.${current}`,
      confirmLabel: 'Load and restart',
    });
    if (!yes) return;
    apply.mutate(choice.kind === 'catalog' ? applyRequestFor(choice.entry) : { template: paste.trim() });
  };

  const onForget = async (e: CatalogTemplate) => {
    const yes = await confirmDialog({
      title: `Forget ${label(e)}?`,
      body: `The template measured on ${e.measured_on ?? 'a device here'} is no longer offered. Devices that run it keep it.`,
      confirmLabel: 'Forget', danger: true,
    });
    if (yes) forget.mutate(e.id);
  };

  const unknown = d?.identified === false;
  const savable = Boolean(d?.current.active && d.current.template_json && !(d.current.measured_matches ?? []).length);

  const onRestore = async () => {
    const yes = await confirmDialog({
      title: `Restore the previous template on ${deviceName}?`,
      body: `${deviceName} gets back its previous template ("${templateName(previous) ?? 'unnamed'}"). The device restarts.`,
      confirmLabel: 'Restore and restart',
    });
    if (yes) restore.mutate();
  };

  return (
    <section aria-label="Device template" className="space-y-2" data-testid="section-tasmota-template">
      <h3 className="flex items-center gap-2 px-1 text-sm font-semibold text-slate-300">
        <Cpu size={14} aria-hidden /> Device template
      </h3>
      <div className={`${CARD} space-y-4 p-3`}>
        {q.isLoading && <div className="skeleton h-12 w-full rounded-xl" role="status" aria-label="Reading the device's template" />}
        {q.error && <p className="text-sm text-amber-300/90" role="alert">{failureText(q.error)}</p>}
        {d && (
          <div className="space-y-1" data-testid="template-current">
            <p className="break-words text-sm text-slate-100">
              Now: <span className="font-semibold">{d.current.template_name ?? 'No template'}</span>
              {!d.current.active && d.current.module_name ? ` (not in use; it runs the ${d.current.module_name} module)` : ''}
            </p>
            <p className="break-words text-xs text-slate-400">
              {[d.current.chip, d.current.light ? `a light with ${d.current.light_channels} channel${d.current.light_channels === 1 ? '' : 's'}`
                : d.current.relays ? `${d.current.relays} relay${d.current.relays === 1 ? '' : 's'}` : 'drives nothing yet',
              d.current.firmware].filter(Boolean).join(' · ')}
            </p>
            {d.up_to_date && d.message && (
              <p className="flex gap-1.5 text-sm text-emerald-200" data-testid="template-up-to-date">
                <CheckCircle2 size={16} className="mt-0.5 shrink-0" aria-hidden /> {d.message}
              </p>
            )}
          </div>
        )}

        {result && <Result result={result} />}

        {unknown && d?.identify_note && (
          <p className="flex gap-1.5 rounded-xl border border-blue-400/20 bg-blue-500/5 p-2 text-sm text-slate-200" data-testid="template-unidentified">
            <Info size={16} className="mt-0.5 shrink-0 text-blue-300" aria-hidden />
            <span className="min-w-0 break-words">{d.identify_note}</span>
          </p>
        )}
        {unknown && <TasmotaPinFinder entryId={entryId} deviceName={deviceName} first />}

        {unknown && (d?.guesses ?? []).length > 0 && (
          <details className="group border-t border-white/5 pt-2" data-testid="template-guesses">
            <summary className="flex min-h-11 cursor-pointer list-none items-center text-xs font-medium text-slate-400 [&::-webkit-details-marker]:hidden">
              Guesses from its name only ({d?.guesses?.length})
            </summary>
            <ul className="divide-y divide-white/5" aria-label="Guessed templates">
              {(d?.guesses ?? []).map((s) => (
                <Row key={s.id} entry={s} reasons={s.reasons} testId="template-guess"
                  chosen={choice?.kind === 'catalog' && choice.entry.id === s.id}
                  onChoose={() => setChoice({ kind: 'catalog', entry: s })} />
              ))}
            </ul>
          </details>
        )}

        {d && d.suggestions.length > 0 && (
          <div>
            <p className="text-xs font-medium text-slate-400">Suggested for this device</p>
            <ul className="divide-y divide-white/5" aria-label="Suggested templates">
              {d.suggestions.map((s) => (
                <Row key={s.id} entry={s} reasons={s.reasons} chosen={choice?.kind === 'catalog' && choice.entry.id === s.id}
                  onChoose={() => setChoice({ kind: 'catalog', entry: s })} />
              ))}
            </ul>
          </div>
        )}

        <details className="group border-t border-white/5 pt-2" data-testid="template-catalog">
          <summary className="flex min-h-11 cursor-pointer list-none items-center text-xs font-medium text-slate-400 [&::-webkit-details-marker]:hidden">
            Choose from the catalog{catalog.data ? ` (${catalog.data.length})` : ''}
          </summary>
          <div className="space-y-2">
            <div className="flex flex-col gap-2 sm:flex-row">
              <label className="relative min-w-0 flex-1">
                <span className="sr-only">Search templates</span>
                <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" aria-hidden />
                <input className={`${INPUT} pl-8`} value={search} onChange={(e) => setSearch(e.target.value)}
                  placeholder="Maker or model" aria-label="Search templates" />
              </label>
              <select className={`${INPUT} sm:w-40`} value={kind} onChange={(e) => setKind(e.target.value as TemplateKind | '')}
                aria-label="Kind of device">
                <option value="">Every kind</option>
                {(Object.keys(KIND_LABEL) as TemplateKind[]).map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
              </select>
            </div>
            {catalog.error && <p className="text-xs text-amber-300/90">{failureText(catalog.error)}</p>}
            {catalog.data && matches.length === 0 && <p className="text-xs text-slate-500">No template matches.</p>}
            <ul className="divide-y divide-white/5" aria-label="Catalog templates">
              {matches.slice(0, SHOWN).map((e) => (
                <Row key={e.id} entry={e} chosen={choice?.kind === 'catalog' && choice.entry.id === e.id}
                  onChoose={() => setChoice({ kind: 'catalog', entry: e })}
                  onForget={e.source === 'measured' ? () => void onForget(e) : undefined} />
              ))}
            </ul>
            {matches.length > SHOWN && <p className="text-xs text-slate-500">{matches.length - SHOWN} more: narrow the search.</p>}
            <p className="text-[11px] text-slate-500">
              Measured ones were worked out in this household. The rest are copied from <a className="inline-flex min-h-11 items-center underline" href="https://templates.blakadder.com/" target="_blank" rel="noopener noreferrer">
                templates.blakadder.com<ExternalLink size={10} className="ml-0.5 inline" aria-hidden /></a>.
            </p>
          </div>
        </details>

        <details className="group border-t border-white/5 pt-2" data-testid="template-paste-box">
          <summary className="flex min-h-11 cursor-pointer list-none items-center text-xs font-medium text-slate-400 [&::-webkit-details-marker]:hidden">
            Paste a template
          </summary>
          <label className="block text-xs text-slate-400">
            <span className="sr-only">Template</span>
            <textarea className={`${INPUT} min-h-24 py-2 font-mono text-xs`} value={paste} spellCheck={false}
              aria-label="Template to paste" data-testid="template-paste"
              placeholder='{"NAME":"...","GPIO":[...],"FLAG":0,"BASE":18}'
              onChange={(e) => { setPaste(e.target.value); setChoice({ kind: 'paste' }); }} />
          </label>
          {pasteError && paste.trim() && <p className="mt-1 text-xs text-amber-300/90" role="alert">{pasteError}</p>}
        </details>

        <div className="flex flex-col gap-2 border-t border-white/5 pt-3 sm:flex-row sm:flex-wrap sm:items-center">
          <p className="min-w-0 flex-1 break-words text-xs text-slate-400" data-testid="template-chosen">
            {choice ? `Chosen: ${chosenName}` : 'Choose a suggestion or a catalog entry, or paste a template.'}
          </p>
          <button type="button" className={PRIMARY} disabled={!choice || Boolean(pasteError) || busy}
            onClick={() => void onApply()}>
            {apply.isPending ? <><Loader2 size={14} className="animate-spin" aria-hidden /> Loading, the device restarts...</> : 'Apply'}
          </button>
          {previous && (
            <button type="button" className={BTN} disabled={busy} onClick={() => void onRestore()}>
              {restore.isPending ? <Loader2 size={14} className="animate-spin" aria-hidden /> : <RotateCcw size={14} aria-hidden />}
              Restore previous
            </button>
          )}
        </div>

        {d && savable && <SaveThisDevice entryId={entryId} d={d} />}
        {d && !unknown && <TasmotaPinFinder entryId={entryId} deviceName={deviceName} first={false} />}
      </div>
    </section>
  );
}
