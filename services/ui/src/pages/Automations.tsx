import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { AlertTriangle, Download, History, Loader2, Play, Plus, Search, Upload, Workflow } from 'lucide-react';
import Toggle from '../components/ui/Toggle';
import Modal from '../components/ui/Modal';
import AutomationEditor from '../components/automations/AutomationEditor';
import TraceViewer from '../components/automations/TraceViewer';
import RepairsPanel from '../components/automations/RepairsPanel';
import { Chip, Field, MessageList } from '../components/automations/FormBits';
import { BUTTON, INPUT, PRIMARY } from '../components/automations/styles';
import { errorText } from '../lib/errorText';
import {
  exportYaml, filterItems, getCatalog, importYaml, ITEM_KINDS, KIND_LABELS, listItems, relativeAgo, runItem,
  setItemEnabled,
  type AutomationItem, type ItemKind,
} from '../lib/automations';

const LIST_KEY = ['homehub-automations'];

/**
 * Automations, scripts and scenes on the device hub: search, turn on and off,
 * run now, look at past runs, and (admins) edit in a visual builder or as
 * YAML. Everything listed comes from the hub; a family member sees only what
 * touches devices they may see, read-only.
 */
export default function Automations() {
  const queryClient = useQueryClient();
  const list = useQuery({ queryKey: LIST_KEY, queryFn: listItems, refetchInterval: () => (document.hidden ? false : 15_000) });
  const canEdit = !!list.data?.can_edit;
  const catalog = useQuery({ queryKey: ['homehub-automation-catalog'], queryFn: getCatalog, staleTime: 5 * 60_000, retry: false });
  const [query, setQuery] = useState('');
  const [kind, setKind] = useState<ItemKind | 'all'>('all');
  const [editing, setEditing] = useState<{ kind: ItemKind; id: string | null } | null>(null);
  const [tracing, setTracing] = useState<AutomationItem | null>(null);
  const [importing, setImporting] = useState(false);
  const [showRepairs, setShowRepairs] = useState(false);

  const items = useMemo(() => filterItems(list.data?.items ?? [], query, kind), [list.data, query, kind]);
  const refresh = () => void queryClient.invalidateQueries({ queryKey: LIST_KEY });

  const enable = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => setItemEnabled(id, enabled),
    onSuccess: refresh,
    onError: (err) => toast.error(errorText(err, 'That did not work')),
  });
  const run = useMutation({
    mutationFn: (item: AutomationItem) => runItem(item.kind, item.id),
    onSuccess: (_d, item) => { toast.success(`${item.alias || item.id} started`); refresh(); },
    onError: (err) => toast.error(errorText(err, 'It did not start')),
  });

  async function exportAll() {
    try {
      const out = await exportYaml(kind === 'all' ? 'automation' : kind);
      const url = URL.createObjectURL(new Blob([out.yaml], { type: 'text/yaml' }));
      const a = document.createElement('a');
      a.href = url;
      a.download = out.filename || `${kind}.yaml`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      toast.error(errorText(err, 'Export did not work'));
    }
  }

  return (
    <div className="mx-auto w-full max-w-5xl space-y-4 pb-24">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Workflow className="text-sky-400" size={22} aria-hidden />
          <h1 className="text-2xl font-bold text-white">Automations</h1>
        </div>
        {canEdit && (
          <div className="flex flex-wrap gap-2">
            <NewItemButton onPick={(k) => setEditing({ kind: k, id: null })} />
            <button type="button" className={BUTTON} onClick={() => setImporting(true)}><Upload size={14} /> Import</button>
            <button type="button" className={BUTTON} onClick={() => void exportAll()}><Download size={14} /> Export</button>
            <button type="button" className={BUTTON} aria-pressed={showRepairs} onClick={() => setShowRepairs((v) => !v)}>Repairs</button>
          </div>
        )}
      </div>

      {canEdit && showRepairs && <RepairsPanel onSaved={refresh} />}

      <div className="flex flex-col gap-2 sm:flex-row">
        <label className="relative flex-1">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" aria-hidden />
          <input className={`${INPUT} pl-8`} placeholder="Search by name or device" aria-label="Search automations"
            value={query} onChange={(e) => setQuery(e.target.value)} />
        </label>
        <div className="flex flex-wrap gap-1.5">
          <Chip active={kind === 'all'} onClick={() => setKind('all')}>All</Chip>
          {ITEM_KINDS.map((k) => <Chip key={k} active={kind === k} onClick={() => setKind(k)}>{KIND_LABELS[k]}s</Chip>)}
        </div>
      </div>

      {list.isLoading ? (
        <div className="flex justify-center py-10" role="status" aria-label="Loading"><Loader2 className="animate-spin text-slate-500" /></div>
      ) : list.isError ? (
        <div className="glass-panel rounded-2xl p-6 text-center">
          <p className="text-sm text-red-300">{errorText(list.error, 'Could not reach the hub')}</p>
          <button type="button" className={`${BUTTON} mt-2`} onClick={() => void list.refetch()}>Try again</button>
        </div>
      ) : items.length === 0 ? (
        <div className="glass-panel rounded-2xl p-6 text-center text-sm text-slate-400">
          {query || kind !== 'all' ? 'Nothing matches.' : 'No automations yet.'}
        </div>
      ) : (
        <ul className="space-y-2" data-testid="automation-list">
          {items.map((item) => (
            <ItemRow
              key={`${item.kind}:${item.id}`}
              item={item}
              canEdit={canEdit}
              onOpen={() => setEditing({ kind: item.kind, id: item.id })}
              onTraces={() => setTracing(item)}
              onRun={() => run.mutate(item)}
              onEnabled={(enabled) => enable.mutate({ id: item.id, enabled })}
              running={run.isPending && run.variables?.id === item.id}
            />
          ))}
        </ul>
      )}

      {editing && (
        <AutomationEditor
          kind={editing.kind}
          id={editing.id}
          catalog={catalog.data}
          canEdit={canEdit}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); refresh(); }}
        />
      )}
      {tracing && <TraceViewer item={tracing} onClose={() => setTracing(null)} />}
      {importing && <ImportDialog onClose={() => setImporting(false)} onDone={refresh} />}
    </div>
  );
}

function ItemRow({ item, canEdit, onOpen, onTraces, onRun, onEnabled, running }: {
  item: AutomationItem; canEdit: boolean; onOpen: () => void; onTraces: () => void; onRun: () => void;
  onEnabled: (v: boolean) => void; running: boolean;
}) {
  const name = item.alias || item.id;
  return (
    <li className="glass-panel rounded-2xl border border-white/10 p-3" data-testid="automation-row">
      <div className="flex items-start gap-3">
        <button type="button" className="min-h-11 min-w-0 flex-1 text-left" onClick={onOpen} aria-label={`Open ${name}`}>
          <span className="flex flex-wrap items-center gap-2">
            <span className="truncate text-sm font-semibold text-white">{name}</span>
            <span className="rounded-full border border-white/10 px-2 py-0.5 text-[10px] uppercase text-slate-400">{KIND_LABELS[item.kind]}</span>
            {item.running > 0 && (
              <span className="inline-flex items-center gap-1 text-[11px] text-sky-300" role="status">
                <Loader2 size={11} className="animate-spin" aria-hidden />Running{item.running > 1 ? ` (${item.running})` : ''}
              </span>
            )}
          </span>
          <span className="block text-xs text-slate-500">
            {item.kind === 'scene' ? 'Last activated' : 'Last ran'} {relativeAgo(item.last_triggered)}
            {item.description ? ` · ${item.description}` : ''}
          </span>
          {item.unsupported_reason && (
            <span className="mt-1 inline-flex items-center gap-1 rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-[11px] text-amber-200">
              <AlertTriangle size={11} aria-hidden />{item.unsupported_reason}
            </span>
          )}
          {item.last_error && <span className="mt-1 block text-[11px] text-red-300">{item.last_error}</span>}
        </button>
        {item.kind === 'automation' && (
          <div className="flex min-h-11 items-center">
            <Toggle checked={item.enabled} disabled={!canEdit} ariaLabel={`${name} enabled`} onChange={onEnabled} />
          </div>
        )}
      </div>
      <div className="mt-2 flex flex-wrap gap-2">
        {item.kind !== 'scene' && (
          <button type="button" className={BUTTON} onClick={onTraces} aria-label={`Runs of ${name}`}><History size={14} /> Runs</button>
        )}
        {canEdit && item.runnable && (
          <button type="button" className={BUTTON} onClick={onRun} disabled={running} aria-label={`Run ${name} now`}>
            {running ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />} {item.kind === 'scene' ? 'Activate' : 'Run now'}
          </button>
        )}
      </div>
    </li>
  );
}

function NewItemButton({ onPick }: { onPick: (kind: ItemKind) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative">
      <button type="button" className={PRIMARY} aria-expanded={open} onClick={() => setOpen((v) => !v)}><Plus size={14} /> New</button>
      {open && (
        <div className="absolute right-0 z-20 mt-1 w-44 rounded-xl border border-white/10 bg-slate-900 p-1 shadow-xl">
          {ITEM_KINDS.map((k) => (
            <button key={k} type="button" className="min-h-11 w-full rounded-lg px-3 text-left text-sm text-slate-200 hover:bg-white/5"
              onClick={() => { setOpen(false); onPick(k); }}>
              {KIND_LABELS[k]}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function ImportDialog({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [kind, setKind] = useState<ItemKind>('automation');
  const [yaml, setYaml] = useState('');
  const [overwrite, setOverwrite] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ok: string[]; problems: string[] } | null>(null);

  async function go() {
    setBusy(true);
    try {
      const out = await importYaml(kind, yaml, overwrite);
      setResult({
        ok: [...out.created.map((id) => `Added ${id}`), ...out.updated.map((id) => `Updated ${id}`)],
        problems: [...out.errors, ...out.skipped.map((s) => `Skipped ${s.id}: ${s.reason}`)],
      });
      onDone();
    } catch (err) {
      setResult({ ok: [], problems: [errorText(err, 'Import did not work')] });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal isOpen onClose={onClose} title="Import YAML" size="2xl">
      <div className="space-y-3">
        <div className="flex flex-wrap gap-1.5">
          {ITEM_KINDS.map((k) => <Chip key={k} active={kind === k} onClick={() => setKind(k)}>{KIND_LABELS[k]}s</Chip>)}
        </div>
        <Field label="YAML (as in automations.yaml, scripts.yaml or scenes.yaml)">
          <textarea className={`${INPUT} font-mono text-xs`} rows={12} spellCheck={false} value={yaml} onChange={(e) => setYaml(e.target.value)} />
        </Field>
        <div className="flex min-h-11 items-center justify-between">
          <span className="text-xs text-slate-400">Replace items with the same id</span>
          <Toggle checked={overwrite} onChange={setOverwrite} ariaLabel="Replace items with the same id" />
        </div>
        {result && <MessageList tone="warning" items={result.ok} />}
        {result && <MessageList tone="error" items={result.problems} />}
        <button type="button" className={PRIMARY} disabled={busy || !yaml.trim()} onClick={() => void go()}>
          {busy && <Loader2 size={14} className="animate-spin" />} Import
        </button>
      </div>
    </Modal>
  );
}
