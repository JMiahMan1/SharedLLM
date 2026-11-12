import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { Loader2, Wrench } from 'lucide-react';
import { errorText } from '../../lib/errorText';
import { getRemap, previewRemap, saveRemap, type Remap, type RemapRow } from '../../lib/automations';
import { Field, MessageList } from './FormBits';
import { BUTTON, INPUT, PRIMARY } from './styles';

const QUERY_KEY = ['homehub-remap'];

/**
 * Admins: imported automations that name old device or entity ids, and what
 * the stored remap changes in each. The remap itself is household data held by
 * the hub; it is edited here as JSON, previewed, then saved.
 */
export default function RepairsPanel({ onSaved }: { onSaved: () => void }) {
  const queryClient = useQueryClient();
  const remap = useQuery({ queryKey: QUERY_KEY, queryFn: getRemap, retry: false });
  const [text, setText] = useState('');
  const [preview, setPreview] = useState<RemapRow[] | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    // Seed the editor from the stored remap once it arrives.
    // eslint-disable-next-line react-hooks/set-state-in-effect -- one-time seed from the query
    if (remap.data && !text) setText(JSON.stringify(remap.data.remap ?? {}, null, 2));
  }, [remap.data, text]);

  const parsed = (): Remap | null => {
    try {
      const value = JSON.parse(text || '{}');
      if (value && typeof value === 'object' && !Array.isArray(value)) {
        setError('');
        return value as Remap;
      }
    } catch {
      // fall through
    }
    setError('The remap must be a JSON object.');
    return null;
  };

  async function run(save: boolean) {
    const value = parsed();
    if (!value) return;
    setBusy(true);
    try {
      if (save) {
        const result = await saveRemap(value);
        queryClient.setQueryData(QUERY_KEY, result);
        setPreview(null);
        toast.success('Remap saved');
        onSaved();
      } else {
        setPreview((await previewRemap(value)).report);
      }
    } catch (err) {
      setError(errorText(err, 'That did not work'));
    } finally {
      setBusy(false);
    }
  }

  const rows = preview ?? remap.data?.report ?? [];
  return (
    <section className="glass-panel space-y-3 rounded-2xl border border-white/10 p-4" data-testid="repairs">
      <h2 className="flex items-center gap-2 text-[10px] font-black uppercase tracking-widest text-amber-300">
        <Wrench size={14} /> Repairs
      </h2>
      {remap.isLoading && <Loader2 className="animate-spin text-slate-500" />}
      {remap.isError && <p className="text-sm text-red-300">{errorText(remap.error, 'Could not load the repairs')}</p>}
      {remap.data && (
        <>
          <p className="text-xs text-slate-400">
            {preview ? 'Preview of the remap below (not saved):' : 'What the saved remap changes:'} {rows.length} item{rows.length === 1 ? '' : 's'}.
          </p>
          <ul className="space-y-2">
            {rows.map((row) => <RepairRow key={`${row.kind}:${row.id}`} row={row} />)}
          </ul>
          <Field label="Remap (JSON)" hint='{"entities": {"old.id": "new.id"}, "devices": {}, "drop_actions": [], "only": []}'>
            <textarea className={`${INPUT} font-mono text-xs`} rows={8} spellCheck={false} value={text}
              onChange={(e) => setText(e.target.value)} />
          </Field>
          {error && <MessageList tone="error" items={[error]} />}
          <div className="flex flex-wrap gap-2">
            <button type="button" className={BUTTON} disabled={busy} onClick={() => void run(false)}>Preview</button>
            <button type="button" className={PRIMARY} disabled={busy} onClick={() => void run(true)}>Save remap</button>
          </div>
        </>
      )}
    </section>
  );
}

function RepairRow({ row }: { row: RemapRow }) {
  return (
    <li className="rounded-xl border border-white/10 p-3 text-xs">
      <p className="text-sm text-white">{row.alias || row.id} <span className="text-slate-500">({row.kind})</span></p>
      {row.reason_before && <p className="text-amber-200">Before: {row.reason_before}</p>}
      <p className={row.reason_after ? 'text-amber-200' : 'text-emerald-300'}>After: {row.reason_after || 'runs on the hub'}</p>
      {row.changes.map((c, i) => (
        <p key={i} className="break-all text-slate-300">{c.path}: {String(c.old)} to {String(c.new)}</p>
      ))}
      {row.dropped.map((d, i) => <p key={`d${i}`} className="break-all text-slate-400">Dropped {d.path}: {d.reason}</p>)}
    </li>
  );
}
