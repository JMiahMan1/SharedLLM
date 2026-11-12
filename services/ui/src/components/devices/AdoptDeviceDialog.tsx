import { useEffect, useRef, useState } from 'react';
import { Loader2, MonitorCheck } from 'lucide-react';
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

const STEP_TITLES: Record<string, string> = {
  accept_on_tv: 'Accept on your TV',
  accept_on_device: 'Accept on the device',
};
const DEFAULT_POLL_MS = 2000;

/**
 * Adding one device: an optional name, plus whatever the device needs (a key,
 * a setup code, a login). The hub drives the steps:
 *
 * - `needs_input`: the fields it names are shown with its message, and the
 *   admin tries again. When a pairing is open on the device (the TV shows a
 *   code) the answer carries a `session`, which goes back with the code.
 * - `pending`: the device is waiting for a person (Allow on the TV). The dialog
 *   says so and asks again with the `session` until the hub answers otherwise.
 */
export default function AdoptDeviceDialog({ title, intro, needs: initialNeeds, submitLabel = 'Add', onSubmit, onDone, onClose }: Props) {
  const [name, setName] = useState('');
  const [values, setValues] = useState<Record<string, string>>({});
  const [needs, setNeeds] = useState<DiscoveryInput[]>(initialNeeds);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [waiting, setWaiting] = useState<{ step: string; message: string } | null>(null);
  const [session, setSession] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      if (timer.current) clearTimeout(timer.current);
    };
  }, []);

  const missing = needs.some((n) => !n.optional && !(values[n.key] || '').trim());

  async function run(current: Record<string, string>, sessionId: string | null) {
    setBusy(true);
    setError(null);
    const input: Record<string, string> = Object.fromEntries(
      Object.entries(current).map(([k, v]) => [k, v.trim()]).filter(([, v]) => v),
    );
    if (sessionId) input.session = sessionId;
    try {
      const result = await onSubmit({ name: name.trim() || undefined, input });
      if (alive.current) handle(result, current, sessionId);
    } catch (err) {
      if (!alive.current) return;
      setWaiting(null);
      setError(failureText(err));
      // The hub dropped the pairing (expired, refused): the next try starts over.
      setSession(null);
    } finally {
      if (alive.current) setBusy(false);
    }
  }

  function handle(result: AdoptResult, current: Record<string, string>, sessionId: string | null) {
    if (result.status === 'pending') {
      const next = result.session || sessionId;
      setSession(next);
      setWaiting({ step: result.step || 'accept_on_device', message: result.message || '' });
      timer.current = setTimeout(() => void run(current, next), result.poll_ms || DEFAULT_POLL_MS);
      return;
    }
    setWaiting(null);
    if (result.status === 'needs_input') {
      const next = result.session || null;
      if (sessionId && next && next !== sessionId) {
        // The device opened a new pairing (a new code on the TV): clear what was typed for the old one.
        const keys = new Set((result.needs || []).map((n) => n.key));
        setValues((v) => Object.fromEntries(Object.entries(v).filter(([k]) => !keys.has(k))));
      }
      setSession(next);
      setNeeds(result.needs || []);
      setMessage(result.message || null);
      setError(result.error || null);
      return;
    }
    if (result.status === 'adopted') {
      onDone(result);
      return;
    }
    setSession(null);
    setError(result.message || 'That did not work. Try again.');
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy || missing || waiting) return;
    await run(values, session);
  }

  return (
    <Modal isOpen onClose={onClose} title={title} size="md">
      <form onSubmit={submit} className="space-y-3" data-testid="adopt-dialog">
        {intro && <p className="text-sm text-slate-400">{intro}</p>}
        {waiting ? (
          <div className="flex items-start gap-3 rounded-xl border border-sky-500/30 bg-sky-500/10 p-3" role="status" data-testid="adopt-waiting">
            <MonitorCheck size={22} className="mt-0.5 shrink-0 text-sky-300" />
            <div className="min-w-0">
              <p className="text-sm font-semibold text-white">{STEP_TITLES[waiting.step] || 'Waiting for the device'}</p>
              {waiting.message && <p className="text-sm text-slate-300">{waiting.message}</p>}
              <p className="mt-1 flex items-center gap-1.5 text-xs text-slate-400">
                <Loader2 size={12} className="animate-spin" /> Waiting…
              </p>
            </div>
          </div>
        ) : (
          <>
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
          </>
        )}
        {error && <p className="text-sm text-amber-300" role="alert">{error}</p>}
        <div className="flex justify-end gap-2 pt-1">
          <button type="button" onClick={onClose} className="min-h-11 rounded-lg px-4 text-sm text-slate-300 hover:bg-white/5">
            Cancel
          </button>
          {!waiting && (
            <button
              type="submit"
              disabled={busy || missing}
              className="inline-flex min-h-11 items-center gap-2 rounded-lg bg-sky-600 px-4 text-sm font-semibold text-white disabled:opacity-50"
            >
              {busy && <Loader2 size={14} className="animate-spin" />} {busy ? 'Working…' : (session ? 'Continue' : submitLabel)}
            </button>
          )}
        </div>
      </form>
    </Modal>
  );
}
