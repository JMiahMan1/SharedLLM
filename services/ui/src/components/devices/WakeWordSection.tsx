import { AlertTriangle } from 'lucide-react';
import { SelectControl } from '../dashboards/EntityControls';
import type { HardwareEntity } from '../../lib/hardware';
import { detectionTime, probabilityText, wakeWordInfo } from '../../lib/wakeWord';

const CARD = 'rounded-2xl border border-white/10 bg-white/[0.03]';
/** The newest wake-ups shown; HomeHub keeps 20. */
const SHOWN = 10;

/**
 * A hassmic Echo's wake word sensitivity (HomeHub's per-Echo setting, applied to the Echo's microWakeWord models
 * without a reflash) with its warning, and the recent wake-ups with the probability each passed at, so the setting
 * can be tuned from what the Echo actually heard. Renders nothing for a device without these entities.
 */
export default function WakeWordSection({ entities, onChanged }: { entities: HardwareEntity[]; onChanged?: () => void }) {
  const info = wakeWordInfo(entities);
  if (!info) return null;
  const level = info.sensitivity?.state ?? '';
  const raised = level !== '' && level.toLowerCase() !== 'standard' && level !== 'unavailable' && level !== 'unknown';
  const shown = info.detections.slice(0, SHOWN);
  return (
    <section aria-label="Wake word" data-testid="section-wake-word" className="space-y-2">
      <h3 className="px-1 text-sm font-semibold text-slate-300">Wake word</h3>
      <div className={`${CARD} space-y-3 p-3`}>
        {info.sensitivity && (
          <div data-testid="wake-sensitivity">
            <SelectControl entity={{ ...info.sensitivity, name: 'Sensitivity' }} onChanged={onChanged} />
          </div>
        )}
        <p role="note" data-testid="wake-sensitivity-warning"
          className={`flex gap-2 text-xs leading-relaxed ${raised ? 'text-amber-200' : 'text-slate-400'}`}>
          <AlertTriangle size={14} className="mt-0.5 shrink-0" aria-hidden />
          <span>{info.warning}</span>
        </p>
        {info.reason && (
          <p role="alert" data-testid="wake-sensitivity-reason" className="break-words text-xs text-rose-200">{info.reason}</p>
        )}
        {info.note && <p className="text-xs text-slate-500" data-testid="wake-sensitivity-note">{info.note}</p>}

        {info.detectionsEntityId && (
          <div className="border-t border-white/5 pt-3" data-testid="wake-detections">
            <p className="text-xs font-medium text-slate-400">Recent wake-ups</p>
            {shown.length === 0 ? (
              <p className="mt-1 text-xs text-slate-500">None heard since HomeHub started reading this Echo&apos;s log.</p>
            ) : (
              <ul className="mt-1 divide-y divide-white/5">
                {shown.map((d) => (
                  <li key={`${d.at}-${d.wakeWord}`} data-testid="wake-detection"
                    className="flex min-h-9 flex-wrap items-center justify-between gap-x-3 gap-y-0.5 py-1 text-xs">
                    <span className="min-w-0 break-words text-slate-200">
                      {d.wakeWord}{d.second ? <span className="text-slate-500"> (second)</span> : null}
                    </span>
                    <span className="tabular-nums text-slate-300">{probabilityText(d)}</span>
                    <span className="w-full text-[11px] text-slate-500 sm:w-auto">{detectionTime(d.at)}</span>
                  </li>
                ))}
              </ul>
            )}
            <p className="mt-2 text-[11px] leading-relaxed text-slate-500">
              The Echo logs each wake-up with the probability it passed at; near misses are not logged. Many
              wake-ups nobody asked for mean it is set too sensitive.
            </p>
          </div>
        )}
      </div>
    </section>
  );
}
