import { AlertTriangle, RotateCcw, Settings2 } from 'lucide-react';

/**
 * The note under a field whose standard value comes from the system config.
 *
 * Matching it: a quiet "From system config". Changed: the field keeps the new
 * value (the prefill no longer applies to it) and a warning says what the
 * system value is, until "Reset to system value" puts it back.
 */
export default function SystemValueHint({
  value,
  systemValue,
  onReset,
  source = 'system_config',
}: {
  value: string;
  systemValue: string;
  onReset: () => void;
  source?: string;
}) {
  const where = source === 'system_config' ? 'system config' : 'household admin account';
  if (value.trim() === systemValue.trim()) {
    return (
      <span className="mt-1 flex items-center gap-1 text-[10px] text-slate-500">
        <Settings2 size={11} /> From {where}
      </span>
    );
  }
  return (
    <span className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border border-amber-400/30 bg-amber-400/10 px-2 py-1 text-[11px] text-amber-200" role="status">
      <AlertTriangle size={12} className="shrink-0" />
      <span>
        Differs from the {where} (<span className="font-mono">{systemValue}</span>). Change it only if this account uses a different server.
      </span>
      <button type="button" onClick={onReset} className="inline-flex items-center gap-1 font-semibold underline">
        <RotateCcw size={11} /> Reset to system value
      </button>
    </span>
  );
}
