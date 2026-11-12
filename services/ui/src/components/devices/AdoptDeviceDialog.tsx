import { useState } from 'react';
import { Loader2 } from 'lucide-react';
import Modal from '../ui/Modal';
import { failureText } from '../../lib/discoveredDevices';
import type { AdoptResult, DiscoveryInput } from '../../lib/discoveredDevices';

interface Props {
  title: string;
  intro?: string;
  /** Fields known up front (an encrypted node's key, a Matter code); the hub may ask for more. */
  needs: DiscoveryInput[];
  submitLabel?: string;
  onSubmit: (values: { name?: string; input: Record<string, string> }) => Promise<AdoptResult>;
  onDone: (result: AdoptResult) => void;
  onClose: () => void;
}

/**
 * Adding one device: an optional name, plus whatever the device needs (a key,
 * a setup code). When the hub answers "needs input" the fields it names are
 * shown with its message, and the admin tries again.
 */
export default function AdoptDeviceDialog({ title, intro, needs: initialNeeds, submitLabel = 'Add', onSubmit, onDone, onClose }: Props) {
  const [name, setName] = useState('');
  const [values, setValues] = useState<Record<string, string>>({});
  const [needs, setNeeds] = useState<DiscoveryInput[]>(initialNeeds);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const missing = needs.some((n) => !n.optional && !(values[n.key] || '').trim());

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy || missing) return;
    setBusy(true);
    setError(null);
    try {
      const input = Object.fromEntries(Object.entries(values).map(([k, v]) => [k, v.trim()]).filter(([, v]) => v));
      const result = await onSubmit({ name: name.trim() || undefined, input });
      if (result.status === 'needs_input') {
        setNeeds(result.needs || []);
        setMessage(result.message || null);
        setError(result.error || null);
        return;
      }
      if (result.status === 'adopted') {
        onDone(result);
        return;
      }
      setError(result.message || 'That did not work. Try again.');
    } catch (err) {
      setError(failureText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal isOpen onClose={onClose} title={title} size="md">
      <form onSubmit={submit} className="space-y-3" data-testid="adopt-dialog">
        {intro && <p className="text-sm text-slate-400">{intro}</p>}
        {message && <p className="text-sm text-slate-300">{message}</p>}
        <label className="block text-xs text-slate-400">
          Name (optional)
          <input
            className="mt-1 w-full rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm text-white"
            value={name}
            onChange={(e) => setName(e.target.value)}
            autoComplete="off"
          />
        </label>
        {needs.map((n) => (
          <label key={n.key} className="block text-xs text-slate-400">
            {n.label}{n.optional ? ' (optional)' : ''}
            <input
              className="mt-1 w-full rounded-lg border border-white/10 bg-black/30 px-3 py-2 font-mono text-sm text-white"
              type={n.type === 'password' ? 'password' : 'text'}
              value={values[n.key] || ''}
              onChange={(e) => setValues((v) => ({ ...v, [n.key]: e.target.value }))}
              autoComplete="off"
              spellCheck={false}
            />
            {n.help && <span className="mt-1 block text-[11px] text-slate-500">{n.help}</span>}
          </label>
        ))}
        {error && <p className="text-sm text-amber-300" role="alert">{error}</p>}
        <div className="flex justify-end gap-2 pt-1">
          <button type="button" onClick={onClose} className="min-h-11 rounded-lg px-4 text-sm text-slate-300 hover:bg-white/5">
            Cancel
          </button>
          <button
            type="submit"
            disabled={busy || missing}
            className="inline-flex min-h-11 items-center gap-2 rounded-lg bg-sky-600 px-4 text-sm font-semibold text-white disabled:opacity-50"
          >
            {busy && <Loader2 size={14} className="animate-spin" />} {busy ? 'Adding…' : submitLabel}
          </button>
        </div>
      </form>
    </Modal>
  );
}
