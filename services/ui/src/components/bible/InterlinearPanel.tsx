import { ExternalLink, X } from 'lucide-react';
import type { BibleInterlinear } from '../../types/api';

/**
 * An imported word-by-word interlinear: each original word with its
 * transliteration, English and Strong's number. Hebrew runs right to left.
 * Shown only for imported data; a passage without it opens Blue Letter Bible's.
 */
export default function InterlinearPanel({
  data,
  blbUrl,
  onClose,
}: {
  data: BibleInterlinear;
  /** The same passage on Blue Letter Bible's interlinear tab, when known. */
  blbUrl?: string;
  onClose: () => void;
}) {
  return (
    <div className="fixed inset-0 z-[60] flex items-end sm:items-center sm:justify-center" role="dialog" aria-modal="true" aria-label={`Interlinear for ${data.ref}`}>
      <button type="button" aria-label="Close interlinear" onClick={onClose} className="absolute inset-0 bg-black/60 backdrop-blur-sm" />
      <div
        className="relative w-full sm:max-w-2xl bg-slate-950/97 border border-white/10 border-b-0 sm:border-b sm:rounded-2xl p-4 pb-[max(1rem,env(safe-area-inset-bottom))] max-h-[85vh] overflow-y-auto"
        data-testid="bible-interlinear-panel"
      >
        <div className="flex items-center justify-between gap-3 mb-3">
          <p className="text-sm font-semibold text-amber-300">Interlinear · {data.ref}</p>
          <button type="button" onClick={onClose} aria-label="Close interlinear" className="min-h-11 min-w-11 flex items-center justify-center rounded-xl text-slate-400 hover:text-white">
            <X size={16} />
          </button>
        </div>

        <div className="space-y-4">
          {data.verses.map((verse) => {
            const hebrew = verse.language === 'hebrew';
            return (
              <section key={verse.reference} data-testid="bible-interlinear-verse">
                <p className="text-[11px] uppercase tracking-wide text-slate-500 mb-2">{verse.reference}</p>
                <div className="flex flex-wrap gap-2" dir={hebrew ? 'rtl' : 'ltr'}>
                  {verse.words.map((word, i) => {
                    const body = (
                      <>
                        <span lang={hebrew ? 'he' : 'grc'} className={`block text-lg leading-tight text-white ${hebrew ? 'font-serif' : ''}`}>
                          {word.w}
                        </span>
                        {word.t && <span className="block text-[11px] italic text-slate-400" dir="ltr">{word.t}</span>}
                        <span className="block text-xs text-slate-200" dir="ltr">{word.e || word.d}</span>
                        {word.s && (
                          <span className="block text-[10px] text-sky-300/80" dir="ltr">
                            {word.s}
                            {word.k && <span className="ml-1 text-amber-300/80" title="Not in every manuscript edition">†</span>}
                          </span>
                        )}
                      </>
                    );
                    const cls = 'min-w-[4.5rem] rounded-lg border border-white/10 bg-white/5 px-2 py-1.5 text-center';
                    return word.x ? (
                      <a key={i} href={word.x} target="_blank" rel="noopener noreferrer" className={`${cls} hover:bg-white/10`} title={word.l ? `${word.l} — ${word.d}` : word.d}>
                        {body}
                      </a>
                    ) : (
                      <span key={i} className={cls} title={word.l ? `${word.l} — ${word.d}` : word.d}>
                        {body}
                      </span>
                    );
                  })}
                </div>
              </section>
            );
          })}
        </div>

        <div className="mt-4 flex flex-wrap items-center justify-between gap-2 text-[11px] text-slate-500">
          <span>{data.attribution}{data.verses.some((v) => v.words.some((w) => w.k)) ? ' · † not in every manuscript edition' : ''}</span>
          {blbUrl && (
            <a href={blbUrl} target="_blank" rel="noopener noreferrer" className="inline-flex min-h-11 items-center gap-1 text-sky-300 hover:text-sky-200">
              <ExternalLink size={12} /> Blue Letter Bible
            </a>
          )}
        </div>
      </div>
    </div>
  );
}
