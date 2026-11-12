import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { Loader2, Plus, Trash2 } from 'lucide-react';
import Modal from '../ui/Modal';
import { confirmDialog } from '../ui/confirmDialog';
import { errorText } from '../../lib/errorText';
import {
  blankConfig, createItem, deleteItem, fromBuilder, getItem, importYaml, KIND_LABELS, toBuilder, updateItem,
  validateConfig, validationErrors,
  type Catalog, type Config, type ItemKind, type Step,
} from '../../lib/automations';
import { EntityPicker, Field, JsonBox, MessageList } from './FormBits';
import { StepList } from './StepEditor';
import { BUTTON, DANGER, INPUT, PRIMARY } from './styles';

const MODES = ['single', 'restart', 'queued', 'parallel'];

interface Props {
  kind: ItemKind;
  /** Null for a new item. */
  id: string | null;
  catalog?: Catalog;
  canEdit: boolean;
  onClose: () => void;
  onSaved: () => void;
}

/**
 * Edit (or, for a family member, read) one automation, script or scene, in a
 * visual builder or as YAML. Saving validates with the hub first and shows its
 * errors and any "not supported yet" reason in its own words.
 *
 * YAML mode: the hub hands out each item's YAML, and saving YAML goes through
 * the hub's import with overwrite, so the page never has to parse YAML itself.
 * Switching from the builder to YAML after an edit shows the config as JSON
 * (which is also valid YAML).
 */
export default function AutomationEditor({ kind, id, catalog, canEdit, onClose, onSaved }: Props) {
  const detail = useQuery({
    queryKey: ['homehub-automation', kind, id],
    queryFn: () => getItem(kind, id as string),
    enabled: !!id,
    retry: false,
  });
  const [config, setConfig] = useState<Config | null>(id ? null : blankConfig(kind));
  const [mode, setMode] = useState<'visual' | 'yaml'>('visual');
  const [yaml, setYaml] = useState('');
  const [yamlLoaded, setYamlLoaded] = useState('');
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [unsupported, setUnsupported] = useState<string | null>(null);

  useEffect(() => {
    if (!detail.data || config) return;
    // Seed the form once from the hub's copy; later refetches must not wipe edits.
    // eslint-disable-next-line react-hooks/set-state-in-effect -- one-time seed from the query
    setConfig(detail.data.config);
    setYaml(detail.data.yaml);
    setYamlLoaded(detail.data.yaml);
    setUnsupported(detail.data.item.unsupported_reason);
    setWarnings(detail.data.item.warnings ?? []);
  }, [detail.data, config]);

  const lists = useMemo(() => (config ? toBuilder(kind, config) : null), [kind, config]);
  const readOnly = !canEdit;
  const name = kind === 'scene' ? 'name' : 'alias';
  const title = id ? `${canEdit ? 'Edit' : 'View'} ${KIND_LABELS[kind].toLowerCase()}` : `New ${KIND_LABELS[kind].toLowerCase()}`;

  const update = (next: Config) => { setConfig(next); setDirty(true); };
  const setLists = (part: 'triggers' | 'conditions' | 'actions', steps: Step[]) => {
    if (!config || !lists) return;
    update(fromBuilder(kind, config, { ...lists, [part]: steps }));
  };

  const toYaml = () => {
    if (!dirty && yamlLoaded) setYaml(yamlLoaded);
    else setYaml(JSON.stringify(config ?? {}, null, 2));
    setMode('yaml');
  };

  const toVisual = async () => {
    const text = yaml.trim();
    if (text === (dirty ? JSON.stringify(config ?? {}, null, 2) : yamlLoaded).trim()) {
      setMode('visual');
      return;
    }
    try {
      const parsed = JSON.parse(text);
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        update(parsed as Config);
        setMode('visual');
        return;
      }
    } catch {
      // Real YAML: the page cannot read it without saving it through the hub.
    }
    const discard = await confirmDialog({
      title: 'Leave YAML without saving?',
      body: 'The builder cannot read YAML edits until they are saved. Save first to keep them, or discard them.',
      confirmLabel: 'Discard edits',
      danger: true,
    });
    if (discard) setMode('visual');
  };

  async function save() {
    if (!config) return;
    setBusy(true);
    setErrors([]);
    try {
      if (mode === 'yaml') {
        const result = await importYaml(kind, yaml, true);
        const problems = [...result.errors, ...result.skipped.map((s) => `${s.id}: ${s.reason}`)];
        if (problems.length) {
          setErrors(problems);
          return;
        }
      } else {
        const check = await validateConfig(kind, config);
        setWarnings(check.warnings ?? []);
        setUnsupported(check.unsupported_reason);
        if (!check.valid) {
          setErrors(check.errors.length ? check.errors : ['The hub did not accept this configuration.']);
          return;
        }
        if (id) await updateItem(kind, id, config);
        else await createItem(kind, config);
      }
      toast.success('Saved');
      setDirty(false);
      onSaved();
    } catch (err) {
      const list = validationErrors(err);
      setErrors(list.length ? [errorText(err), ...list] : [errorText(err, 'Saving did not work')]);
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!id) return;
    const ok = await confirmDialog({
      title: `Delete this ${KIND_LABELS[kind].toLowerCase()}?`,
      body: 'It stops running and is removed from the hub.',
      confirmLabel: 'Delete',
      danger: true,
    });
    if (!ok) return;
    try {
      await deleteItem(kind, id);
      toast.success('Deleted');
      onSaved();
    } catch (err) {
      toast.error(errorText(err, 'Deleting did not work'));
    }
  }

  return (
    <Modal isOpen onClose={onClose} title={title} size="3xl">
      {id && detail.isLoading && (
        <div className="flex justify-center py-8" role="status" aria-label="Loading"><Loader2 className="animate-spin text-slate-500" /></div>
      )}
      {detail.isError && <p className="text-sm text-red-300">{errorText(detail.error, 'Could not load it')}</p>}
      {config && lists && (
        <div className="space-y-4" data-testid="automation-editor">
          <div className="flex gap-2" role="tablist" aria-label="Editor mode">
            <button type="button" role="tab" aria-selected={mode === 'visual'} className={BUTTON} onClick={() => void toVisual()}>Visual</button>
            <button type="button" role="tab" aria-selected={mode === 'yaml'} className={BUTTON} onClick={toYaml}>YAML</button>
          </div>

          {unsupported && <MessageList tone="warning" title="Not supported on the hub yet" items={[unsupported]} />}
          <MessageList tone="warning" items={warnings} />
          <MessageList tone="error" title="The hub found problems" items={errors} />

          {mode === 'yaml' ? (
            <Field label={`${KIND_LABELS[kind]} YAML`}>
              <textarea className={`${INPUT} font-mono text-xs`} rows={18} spellCheck={false} readOnly={readOnly}
                value={yaml} onChange={(e) => { setYaml(e.target.value); setDirty(true); }} />
            </Field>
          ) : (
            <>
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Name">
                  <input className={INPUT} disabled={readOnly} value={String(config[name] ?? '')}
                    onChange={(e) => update({ ...config, [name]: e.target.value })} />
                </Field>
                {kind !== 'scene' && (
                  <Field label="When it is already running">
                    <select className={INPUT} disabled={readOnly} value={String(config.mode ?? 'single')}
                      onChange={(e) => update({ ...config, mode: e.target.value })}>
                      {MODES.map((m) => <option key={m} value={m}>{m}</option>)}
                    </select>
                  </Field>
                )}
                {kind === 'automation' && (
                  <div className="sm:col-span-2">
                    <Field label="Description">
                      <input className={INPUT} disabled={readOnly} value={String(config.description ?? '')}
                        onChange={(e) => update({ ...config, description: e.target.value })} />
                    </Field>
                  </div>
                )}
              </div>
              {kind === 'automation' && (
                <>
                  <StepList group="trigger" label="When" steps={lists.triggers} catalog={catalog} disabled={readOnly}
                    onChange={(s) => setLists('triggers', s)} />
                  <StepList group="condition" label="Only if" steps={lists.conditions} catalog={catalog} disabled={readOnly}
                    onChange={(s) => setLists('conditions', s)} />
                </>
              )}
              {kind !== 'scene' && (
                <StepList group="action" label="Do" steps={lists.actions} catalog={catalog} disabled={readOnly}
                  onChange={(s) => setLists('actions', s)} />
              )}
              {kind === 'scene' && <SceneEntities config={config} catalog={catalog} disabled={readOnly} onChange={update} />}
            </>
          )}

          {canEdit && (
            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-white/5 pt-3">
              {id ? <button type="button" className={DANGER} onClick={() => void remove()}><Trash2 size={14} /> Delete</button> : <span />}
              <button type="button" className={PRIMARY} disabled={busy} onClick={() => void save()}>
                {busy && <Loader2 size={14} className="animate-spin" />} Check and save
              </button>
            </div>
          )}
          {!canEdit && <p className="text-xs text-slate-500">Only an admin can change this.</p>}
        </div>
      )}
    </Modal>
  );
}

/** A scene: the entities it sets, each with the state and attributes it sets them to. */
function SceneEntities({ config, catalog, disabled, onChange }: {
  config: Config; catalog?: Catalog; disabled: boolean; onChange: (c: Config) => void;
}) {
  const entities = (config.entities && typeof config.entities === 'object' ? config.entities : {}) as Record<string, unknown>;
  const ids = Object.keys(entities);
  const setEntities = (next: Record<string, unknown>) => onChange({ ...config, entities: next });
  return (
    <div className="space-y-3">
      <p className="text-[10px] font-black uppercase tracking-widest text-slate-400">Sets</p>
      {ids.map((entityId) => (
        <div key={entityId} className="space-y-2 rounded-xl border border-white/10 p-3">
          <div className="flex items-center justify-between gap-2">
            <span className="truncate text-sm text-white">{catalog?.entities.find((e) => e.entity_id === entityId)?.name || entityId}</span>
            {!disabled && (
              <button type="button" className="min-h-11 min-w-11 text-red-300" aria-label={`Remove ${entityId}`}
                onClick={() => setEntities(Object.fromEntries(Object.entries(entities).filter(([k]) => k !== entityId)))}>
                <Trash2 size={16} className="mx-auto" />
              </button>
            )}
          </div>
          <JsonBox label="State and attributes (JSON)" value={typeof entities[entityId] === 'object' ? entities[entityId] : { state: entities[entityId] }}
            disabled={disabled} onChange={(v) => setEntities({ ...entities, [entityId]: v })} />
        </div>
      ))}
      {!disabled && (
        <Field group label="Add a device">
          <EntityPicker entities={catalog?.entities ?? []} value={[]} label="Add a device to the scene"
            onChange={(next) => next[0] && setEntities({ ...entities, [next[0]]: { state: 'on' } })} />
        </Field>
      )}
      {ids.length === 0 && <p className="text-xs text-slate-500"><Plus size={12} className="inline" /> Add devices to set.</p>}
    </div>
  );
}
