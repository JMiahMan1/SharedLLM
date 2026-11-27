import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { CheckCircle2, Lightbulb, Loader2, RefreshCw, RotateCcw, TriangleAlert, X } from 'lucide-react';
import { confirmDialog } from '../ui/confirmDialog';
import { failureText } from '../../lib/discoveredDevices';
import {
  KIND_LABEL, PIN_FINDER_LIGHT_KINDS, answerPinFinder, cancelPinFinder, catalogKey, deviceTemplateKey, getPinFinder,
  pinFinderKey, saveMeasuredTemplate, startPinFinder,
  type PinFinderState, type TemplateKind,
} from '../../lib/tasmotaTemplates';

const INPUT = 'min-h-11 w-full min-w-0 rounded-xl border border-white/10 bg-black/30 px-3 text-sm text-white placeholder:text-slate-500';
const BTN = 'inline-flex min-h-11 items-center justify-center gap-1.5 rounded-xl border border-white/10 bg-white/[0.04] px-3 text-sm text-slate-200 hover:bg-white/10 disabled:opacity-50';
const PRIMARY = 'inline-flex min-h-11 items-center justify-center gap-1.5 rounded-xl bg-blue-600 px-4 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-50';
const ANSWER = 'inline-flex min-h-12 w-full items-center justify-center rounded-xl border border-white/15 bg-white/[0.06] px-3 text-sm font-medium text-white hover:bg-white/15 disabled:opacity-50';

const ONE: Partial<Record<TemplateKind, string>> = { bulb: 'Bulb', strip: 'LED strip', dimmer: 'Dimmer' };

function MakerModel({ maker, model, onMaker, onModel, idPrefix }: {
  maker: string; model: string; onMaker: (v: string) => void; onModel: (v: string) => void; idPrefix: string;
}) {
  return (
    <div className="grid gap-2 sm:grid-cols-2">
      <label className="block text-xs text-slate-400" htmlFor={`${idPrefix}-maker`}>
        Manufacturer
        <input id={`${idPrefix}-maker`} className={`${INPUT} mt-1`} value={maker} maxLength={60}
          onChange={(e) => onMaker(e.target.value)} placeholder="As printed on the box" autoComplete="off" />
      </label>
      <label className="block text-xs text-slate-400" htmlFor={`${idPrefix}-model`}>
        Model
        <input id={`${idPrefix}-model`} className={`${INPUT} mt-1`} value={model} maxLength={60}
          onChange={(e) => onModel(e.target.value)} placeholder="For example A19 CCT" autoComplete="off" />
      </label>
    </div>
  );
}

/** The found template, saved as measured on this device so the next one like it is offered it. */
function SaveFound({ entryId, state }: { entryId: string; state: PinFinderState }) {
  const qc = useQueryClient();
  const [maker, setMaker] = useState(state.manufacturer ?? '');
  const [model, setModel] = useState(state.model ?? '');
  const [saved, setSaved] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: () => saveMeasuredTemplate(entryId, { manufacturer: maker.trim(), model: model.trim(),
      kind: state.result?.kind ?? state.kind }),
    onSuccess: (r) => {
      setSaved(r.message);
      void qc.invalidateQueries({ queryKey: catalogKey });
      void qc.invalidateQueries({ queryKey: deviceTemplateKey(entryId) });
    },
    onError: (err) => toast.error(failureText(err)),
  });
  if (saved) {
    return <p className="flex gap-1.5 text-sm text-emerald-200" role="status" data-testid="pin-finder-saved">
      <CheckCircle2 size={16} className="mt-0.5 shrink-0" aria-hidden /> {saved}</p>;
  }
  return (
    <form className="space-y-2" onSubmit={(e) => { e.preventDefault(); if (model.trim()) save.mutate(); }}
      aria-label="Save the template that was found">
      <p className="text-xs text-slate-400">Save it for other devices like this one. It is offered first to any device
        whose name matches the model.</p>
      <MakerModel maker={maker} model={model} onMaker={setMaker} onModel={setModel} idPrefix="pin-finder-save" />
      <button type="submit" className={PRIMARY} disabled={!model.trim() || save.isPending}>
        {save.isPending ? <Loader2 size={14} className="animate-spin" aria-hidden /> : null} Save as measured
      </button>
    </form>
  );
}

/**
 * Devices > a Tasmota light > Device template > Find my pins. Saves the device's template, loads a test template,
 * lights one pin at a time and asks what the light looks like, works out the template, loads it and checks it by
 * eye. Cancel (or any failure) puts the saved template back (services/homehub/tasmota_pin_finder.py).
 */
export default function TasmotaPinFinder({ entryId, deviceName, first }: {
  entryId: string; deviceName: string; first: boolean;
}) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: pinFinderKey(entryId), queryFn: () => getPinFinder(entryId), retry: false });
  const [maker, setMaker] = useState('');
  const [model, setModel] = useState('');
  const [kind, setKind] = useState<TemplateKind>('bulb');
  const [open, setOpen] = useState(first);

  const settle = (s: PinFinderState) => {
    qc.setQueryData(pinFinderKey(entryId), s);
    if (s.status !== 'asking') {
      void qc.invalidateQueries({ queryKey: deviceTemplateKey(entryId) });
      void qc.invalidateQueries({ queryKey: ['homehub-hardware'] });
    }
  };
  const onError = (err: unknown) => toast.error(failureText(err));
  const start = useMutation({ mutationFn: () => startPinFinder(entryId, { manufacturer: maker.trim(), model: model.trim(), kind }),
    onSuccess: settle, onError });
  const reply = useMutation({ mutationFn: ({ step, answer }: { step: number; answer: string }) =>
    answerPinFinder(entryId, step, answer), onSuccess: settle, onError });
  const cancel = useMutation({ mutationFn: () => cancelPinFinder(entryId), onSuccess: settle, onError });
  const busy = start.isPending || reply.isPending || cancel.isPending;

  const s = q.data;
  const question = s?.status === 'asking' ? s.question : null;
  const running = Boolean(question);
  const ended = s?.status === 'failed' || s?.status === 'cancelled';

  const onStart = async () => {
    const yes = await confirmDialog({
      title: `Find the pins of ${deviceName}?`,
      body: `${deviceName}'s template is saved first, then a test template is loaded and the light restarts. You will be `
        + 'asked what the light looks like as each pin is turned on, so stay where you can see it. It takes a few '
        + 'minutes and restarts the light two or three times. Cancel at any point puts the saved template back.',
      confirmLabel: 'Save and start',
    });
    if (yes) start.mutate();
  };

  const onCancel = async () => {
    const yes = await confirmDialog({
      title: `Stop and put ${deviceName} back?`,
      body: `${deviceName} gets back the template it had before the pin finder started. The light restarts.`,
      confirmLabel: 'Put it back',
    });
    if (yes) cancel.mutate();
  };

  const working = start.isPending ? 'Saving its template and loading the test template; the light restarts...'
    : reply.isPending ? 'Sending...' : cancel.isPending ? 'Putting the saved template back; the light restarts...' : null;

  return (
    <div className={`space-y-3 rounded-2xl border p-3 ${first ? 'border-blue-400/40 bg-blue-500/10' : 'border-white/10 bg-white/[0.02]'}`}
      data-testid="pin-finder">
      <button type="button" className="flex min-h-11 w-full items-center gap-2 text-left text-sm font-semibold text-slate-100"
        aria-expanded={open || running} onClick={() => setOpen((v) => !v)} disabled={running}>
        <Lightbulb size={16} aria-hidden className="shrink-0 text-amber-300" />
        <span className="min-w-0 flex-1 break-words">Find my pins</span>
        {!running && <span className="text-xs font-normal text-slate-400">{open ? 'Hide' : 'Lights only'}</span>}
      </button>

      {q.error && <p className="text-sm text-amber-300/90" role="alert">{failureText(q.error)}</p>}

      {working && (
        <p className="flex items-center gap-2 text-sm text-slate-200" role="status" data-testid="pin-finder-working">
          <Loader2 size={14} className="animate-spin" aria-hidden /> {working}
        </p>
      )}

      {s?.interrupted && !running && (
        <div className="space-y-2 rounded-xl border border-amber-400/30 bg-amber-500/10 p-3 text-sm text-amber-100"
          role="alert" data-testid="pin-finder-interrupted">
          <p className="break-words">{s.message}</p>
          <button type="button" className={BTN} disabled={busy} onClick={() => void onCancel()}>
            <RotateCcw size={14} aria-hidden /> Put the saved template back
          </button>
        </div>
      )}

      {question && (
        <div className="space-y-3" data-testid="pin-finder-question">
          {question.progress.total > 0 && question.id !== 'verify' && (
            <p className="text-xs text-slate-400">Pins tried: {question.progress.done} of {question.progress.total}</p>
          )}
          <p className="break-words text-base text-white" aria-live="polite">{question.text}</p>
          {question.hint && <p className="break-words text-xs text-slate-400">{question.hint}</p>}
          <div className="grid grid-cols-1 gap-2 min-[380px]:grid-cols-2" role="group" aria-label="Answers">
            {question.answers.map((a) => (
              <button key={a.value} type="button" className={ANSWER} disabled={busy}
                onClick={() => reply.mutate({ step: question.step, answer: a.value })}>
                {a.label}
              </button>
            ))}
          </div>
          <div className="flex flex-col gap-2 sm:flex-row">
            {question.again && (
              <button type="button" className={BTN} disabled={busy}
                onClick={() => reply.mutate({ step: question.step, answer: 'again' })}>
                <RefreshCw size={14} aria-hidden /> Show again
              </button>
            )}
            <button type="button" className={BTN} disabled={busy} onClick={() => void onCancel()}>
              <X size={14} aria-hidden /> Cancel and put it back
            </button>
          </div>
        </div>
      )}

      {s?.status === 'done' && s.result && (
        <div className="space-y-3" data-testid="pin-finder-done">
          <p className="flex gap-1.5 text-sm text-emerald-100" role="status">
            <CheckCircle2 size={16} className="mt-0.5 shrink-0" aria-hidden />
            <span className="min-w-0 break-words">{s.message}</span>
          </p>
          {s.result.how && <p className="break-words text-xs text-slate-300">{s.result.how}</p>}
          <pre className="whitespace-pre-wrap break-all rounded-xl bg-black/30 p-2 font-mono text-[11px] text-slate-300"
            data-testid="pin-finder-template">{s.result.template}</pre>
          <SaveFound entryId={entryId} state={s} />
        </div>
      )}

      {(s?.status === 'failed' || s?.status === 'cancelled') && !s.interrupted && s.message && (
        <p className="flex gap-1.5 text-sm text-amber-100" role="status" data-testid="pin-finder-ended">
          <TriangleAlert size={16} className="mt-0.5 shrink-0" aria-hidden />
          <span className="min-w-0 break-words">{s.message}</span>
        </p>
      )}

      {(open || first || ended) && !running && !s?.interrupted && s?.status !== 'done' && (
        <div className="space-y-2" data-testid="pin-finder-intro">
          <p className="break-words text-sm text-slate-300">
            For a light that no template fits: it turns its pins on one at a time and asks you what the light looks
            like, then writes the template and checks it with you. Its current template is saved first and put back
            if you cancel or anything goes wrong.
          </p>
          <MakerModel maker={maker} model={model} onMaker={setMaker} onModel={setModel} idPrefix="pin-finder" />
          <label className="block text-xs text-slate-400" htmlFor="pin-finder-kind">
            What it is
            <select id="pin-finder-kind" className={`${INPUT} mt-1 sm:w-48`} value={kind}
              onChange={(e) => setKind(e.target.value as TemplateKind)}>
              {PIN_FINDER_LIGHT_KINDS.map((k) => <option key={k} value={k}>{ONE[k] ?? KIND_LABEL[k]}</option>)}
            </select>
          </label>
          <p className="text-xs text-slate-500">Plugs and switches are not covered yet: finding a relay means driving
            pins that may be wired to a button or a power meter.</p>
          <button type="button" className={PRIMARY} disabled={busy || q.isLoading} onClick={() => void onStart()}>
            {start.isPending ? <Loader2 size={14} className="animate-spin" aria-hidden /> : null}
            {ended ? 'Start again' : 'Start the pin finder'}
          </button>
        </div>
      )}
    </div>
  );
}
