import type { BibleVerseToken } from '../../types/api';

interface Word {
  text: string;
  /** Punctuation that follows the word, kept with it so a line never starts with a comma. */
  trailing: string;
  token: BibleVerseToken;
}

function words(tokens: BibleVerseToken[]): { lead: string; words: Word[] } {
  const out: Word[] = [];
  let lead = '';
  for (const token of tokens) {
    if (/^[A-Za-z]/.test(token.text)) {
      out.push({ text: token.text, trailing: '', token });
    } else if (out.length) {
      out[out.length - 1].trailing += token.text.trim();
    } else {
      lead += token.text.trim();
    }
  }
  return { lead, words: out };
}

/**
 * A verse word for word, as Blue Letter Bible sets it: each KJV word over its
 * Strong's number, the numbered ones a tap away from their lexicon entry. A
 * number marked "≈" is the one the word nearly always carries in that
 * Testament, used where the scanned concordance did not keep this verse's line.
 */
export default function WordForWord({
  tokens,
  onWord,
}: {
  tokens: BibleVerseToken[];
  onWord: (strongs: string) => void;
}) {
  const { lead, words: list } = words(tokens);
  const anyInferred = list.some((w) => w.token.inferred);
  return (
    <div data-testid="bible-word-for-word">
      <div className="flex flex-wrap items-start gap-x-1 gap-y-1.5">
        {lead && <span className="pt-1 text-sm text-slate-400">{lead}</span>}
        {list.map((w, i) => {
          const label = (
            <>
              <span className="block text-[15px] leading-tight text-slate-100">
                {w.text}
                <span className="text-slate-400">{w.trailing}</span>
              </span>
              <span
                className={`block text-[10px] leading-tight tabular-nums ${
                  w.token.strongs ? (w.token.inferred ? 'text-sky-300/60' : 'text-sky-300') : 'text-transparent'
                }`}
                aria-hidden={!w.token.strongs}
              >
                {w.token.strongs ? `${w.token.inferred ? '≈' : ''}${w.token.strongs}` : '·'}
              </span>
            </>
          );
          return w.token.strongs ? (
            <button
              key={i}
              type="button"
              onClick={() => onWord(w.token.strongs as string)}
              title={w.token.definition ? `${w.token.transliteration ?? ''} — ${w.token.definition}` : undefined}
              aria-label={`${w.text}, Strong's ${w.token.strongs}${w.token.inferred ? ' (inferred)' : ''}`}
              className="min-h-11 rounded-lg px-1.5 py-1 text-left hover:bg-sky-400/10 focus-visible:bg-sky-400/10"
              data-testid={`bible-wfw-${i}`}
            >
              {label}
            </button>
          ) : (
            <span key={i} className="px-1.5 py-1">
              {label}
            </span>
          );
        })}
      </div>
      <p className="mt-1.5 text-[11px] text-slate-500">
        KJV word for word · tap a word for its Strong&apos;s entry
        {anyInferred ? ' · ≈ the number this word usually carries' : ''}
      </p>
    </div>
  );
}
