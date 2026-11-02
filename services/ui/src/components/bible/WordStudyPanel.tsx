import { useState } from 'react';
import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { ArrowLeft, ExternalLink, Languages, Loader2, Search, X } from 'lucide-react';
import { api } from '../../services/api';
import type { BibleLexiconEntry, BibleLexiconWord } from '../../types/api';

const PAGE = 50;

/** "to love; in the NT usually ...; love feast" -> the senses a reader scans, as BLB's outline does. */
function senses(definition: string): string[] {
  return definition
    .split(/;\s+(?![^(]*\))/)
    .map((s) => s.trim())
    .filter(Boolean);
}

function languageLabel(language: string): string {
  return language === 'greek' ? 'Greek' : language === 'aramaic' ? 'Aramaic' : 'Hebrew';
}

function StrongsChip({ strongs }: { strongs: string }) {
  return (
    <span className="rounded-md border border-sky-400/30 bg-sky-400/10 px-1.5 py-0.5 text-[11px] font-semibold tabular-nums text-sky-200">
      {strongs}
    </span>
  );
}

function EntryView({
  strongs,
  onOpenRef,
}: {
  strongs: string;
  onOpenRef?: (reference: string) => void;
}) {
  const entry = useInfiniteQuery({
    queryKey: ['bible-lexicon-entry', strongs],
    queryFn: ({ pageParam }) => api.getBibleLexiconEntry(strongs, pageParam, PAGE),
    initialPageParam: 0,
    getNextPageParam: (last: BibleLexiconEntry, pages) => {
      const shown = pages.reduce((n, p) => n + p.occurrences.verses.length, 0);
      return shown < last.occurrences.total ? shown : undefined;
    },
    staleTime: 60 * 60 * 1000,
    retry: false,
  });

  if (entry.isLoading) {
    return (
      <p className="flex items-center gap-2 py-6 text-sm text-slate-400">
        <Loader2 size={14} className="animate-spin" /> Opening {strongs}…
      </p>
    );
  }
  if (entry.isError || !entry.data) {
    return (
      <p className="py-6 text-sm text-slate-400" data-testid="bible-word-missing">
        {strongs} is not in the installed concordance.
      </p>
    );
  }
  const first = entry.data.pages[0];
  const verses = entry.data.pages.flatMap((p) => p.occurrences.verses);
  const hebrew = first.language !== 'greek';
  return (
    <div className="space-y-4" data-testid="bible-word-entry">
      <header className="space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <StrongsChip strongs={first.strongs} />
          <span className="text-[11px] uppercase tracking-wide text-slate-500">
            {languageLabel(first.language)}
            {first.part_of_speech ? ` · ${first.part_of_speech}` : ''}
            {first.gk ? ` · GK ${first.gk}` : ''}
          </span>
        </div>
        {first.lemma && (
          <p lang={hebrew ? 'he' : 'grc'} dir={hebrew ? 'rtl' : 'ltr'} className={`text-2xl leading-tight text-white ${hebrew ? 'font-serif' : ''}`}>
            {first.lemma}
          </p>
        )}
        <p className="text-base italic text-amber-200">{first.transliteration || first.gloss}</p>
      </header>

      {first.definition && (
        <section>
          <h4 className="mb-1 text-[11px] uppercase tracking-wider text-slate-500">Outline of usage</h4>
          <ol className="list-decimal space-y-0.5 pl-5 text-sm leading-relaxed text-slate-200 marker:text-slate-500">
            {senses(first.definition).map((sense, i) => (
              <li key={i} className="break-words">{sense}</li>
            ))}
          </ol>
        </section>
      )}

      {first.usage.length > 0 && (
        <section>
          <h4 className="mb-1.5 text-[11px] uppercase tracking-wider text-slate-500">
            KJV translation count · {first.usage_total}
          </h4>
          <div className="flex flex-wrap gap-1.5" data-testid="bible-word-usage">
            {first.usage.map((u) => (
              <span key={u.word} className="rounded-full border border-white/10 bg-white/5 px-2.5 py-1 text-xs text-slate-200">
                {u.word} <span className="tabular-nums text-slate-400">{u.count}</span>
              </span>
            ))}
          </div>
        </section>
      )}

      <section>
        <h4 className="mb-1 text-[11px] uppercase tracking-wider text-slate-500">
          {first.occurrences.complete
            ? `Used in ${first.occurrences.total} verses`
            : `${first.occurrences.total} verses read from the concordance`}
        </h4>
        {!first.occurrences.complete && first.occurrences.total > 0 && (
          <p className="mb-2 text-[11px] text-slate-500">
            The scanned concordance gives these verses; installing an interlinear lists every one.
          </p>
        )}
        <ul className="divide-y divide-white/5" data-testid="bible-word-verses">
          {verses.map((v) => (
            <li key={`${v.osis}-${v.chapter}-${v.verse}-${v.word}`}>
              <button
                type="button"
                onClick={() => onOpenRef?.(v.reference)}
                disabled={!onOpenRef}
                className="flex min-h-11 w-full flex-col items-start gap-0.5 py-1.5 text-left disabled:cursor-default"
              >
                <span className="text-xs font-semibold text-amber-300">{v.reference}</span>
                {(v.context || v.word) && (
                  <span className="text-xs text-slate-300 break-words">{v.context || v.word}</span>
                )}
              </button>
            </li>
          ))}
        </ul>
        {entry.hasNextPage && (
          <button
            type="button"
            onClick={() => void entry.fetchNextPage()}
            disabled={entry.isFetchingNextPage}
            className="mt-2 min-h-11 rounded-xl border border-white/10 px-3 text-xs text-sky-200 hover:bg-white/5"
          >
            {entry.isFetchingNextPage ? 'Loading…' : `Show more (${verses.length} of ${first.occurrences.total})`}
          </button>
        )}
      </section>

      {first.blb_url && (
        <a
          href={first.blb_url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex min-h-11 items-center gap-1 text-xs text-sky-300 hover:text-sky-200"
        >
          <ExternalLink size={12} /> {first.strongs} on Blue Letter Bible
        </a>
      )}
    </div>
  );
}

function WordRow({ word, onOpen }: { word: BibleLexiconWord; onOpen: () => void }) {
  return (
    <li>
      <button
        type="button"
        onClick={onOpen}
        disabled={!word.in_lexicon}
        className="flex min-h-11 w-full items-start gap-3 rounded-xl px-2 py-2 text-left hover:bg-white/5 disabled:opacity-60"
        data-testid={`bible-word-${word.strongs}`}
      >
        <div className="min-w-0 flex-1">
          <p className="flex flex-wrap items-baseline gap-x-2 text-sm text-slate-100">
            <span className="font-semibold">{word.word || word.transliteration}</span>
            {word.original && <span className="font-serif text-slate-300">{word.original}</span>}
            {word.transliteration && <span className="text-xs italic text-slate-400">{word.transliteration}</span>}
          </p>
          {word.definition && <p className="mt-0.5 line-clamp-2 text-xs text-slate-400">{word.definition}</p>}
        </div>
        <StrongsChip strongs={word.strongs} />
      </button>
    </li>
  );
}

/**
 * A word study in the manner of Blue Letter Bible: the words of a verse with
 * their Strong's numbers, and for each number the lexicon entry -- the
 * original word, its senses, how the KJV renders it and every verse it is in.
 * Offered only when a Strong's concordance is installed from the library.
 */
export default function WordStudyPanel({
  reference,
  initialStrongs,
  onOpenRef,
  onClose,
}: {
  /** The verse whose words are listed; empty opens straight on an entry or the search. */
  reference?: string;
  initialStrongs?: string;
  onOpenRef?: (reference: string) => void;
  onClose: () => void;
}) {
  const [trail, setTrail] = useState<string[]>(initialStrongs ? [initialStrongs] : []);
  const [query, setQuery] = useState('');
  const [searched, setSearched] = useState('');
  const current = trail[trail.length - 1];

  const words = useQuery({
    queryKey: ['bible-lexicon-verse', reference],
    queryFn: () => api.getBibleLexiconVerse(reference as string),
    enabled: Boolean(reference) && !current && !searched,
    staleTime: 60 * 60 * 1000,
    retry: false,
  });
  const hits = useQuery({
    queryKey: ['bible-lexicon-search', searched],
    queryFn: () => api.searchBibleLexicon(searched),
    enabled: Boolean(searched),
    staleTime: 10 * 60 * 1000,
    retry: false,
  });

  const open = (strongs: string) => setTrail((t) => [...t, strongs]);
  const back = () => (current ? setTrail((t) => t.slice(0, -1)) : setSearched(''));

  return (
    <div className="fixed inset-0 z-[60] flex items-end sm:items-center sm:justify-center" role="dialog" aria-modal="true" aria-label="Word study">
      <button type="button" aria-label="Close word study" onClick={onClose} className="absolute inset-0 bg-black/60 backdrop-blur-sm" />
      <div
        className="relative w-full min-w-0 sm:max-w-lg bg-slate-950/97 border border-white/10 border-b-0 sm:border-b rounded-t-3xl sm:rounded-2xl p-4 pb-[max(1rem,env(safe-area-inset-bottom))] max-h-[85vh] overflow-y-auto overflow-x-hidden overscroll-contain"
        data-testid="bible-word-study"
      >
        <div className="mb-3 flex items-center gap-2">
          {(current || searched) && (
            <button type="button" onClick={back} aria-label="Back" className="min-h-11 min-w-11 flex items-center justify-center rounded-xl text-slate-300 hover:text-white">
              <ArrowLeft size={16} />
            </button>
          )}
          <p className="flex min-w-0 flex-1 items-center gap-2 text-sm font-semibold text-amber-300">
            <Languages size={15} aria-hidden />
            <span className="truncate">{current ? `Strong's ${current}` : searched ? `“${searched}”` : `Word study${reference ? ` · ${reference}` : ''}`}</span>
          </p>
          <button type="button" onClick={onClose} aria-label="Close word study" className="min-h-11 min-w-11 flex items-center justify-center rounded-xl text-slate-400 hover:text-white">
            <X size={16} />
          </button>
        </div>

        {!current && (
          <form
            className="mb-3 flex items-center gap-2 rounded-xl border border-white/10 bg-white/[0.04] px-3"
            onSubmit={(e) => {
              e.preventDefault();
              setSearched(query.trim());
            }}
          >
            <Search size={14} className="shrink-0 text-slate-500" aria-hidden />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search: love, agape, G26"
              aria-label="Search the lexicon"
              className="min-h-11 min-w-0 flex-1 bg-transparent text-sm text-slate-100 placeholder:text-slate-500 focus:outline-none"
            />
          </form>
        )}

        {current ? (
          <EntryView key={current} strongs={current} onOpenRef={onOpenRef} />
        ) : searched ? (
          hits.isLoading ? (
            <p className="flex items-center gap-2 text-sm text-slate-400"><Loader2 size={14} className="animate-spin" /> Searching…</p>
          ) : (hits.data?.results.length ?? 0) === 0 ? (
            <p className="text-sm text-slate-400">No entry matches “{searched}”.</p>
          ) : (
            <ul className="space-y-1" data-testid="bible-word-hits">
              {hits.data?.results.map((hit) => (
                <WordRow
                  key={hit.strongs}
                  word={{ word: hit.transliteration, original: '', transliteration: '', strongs: hit.strongs, definition: hit.definition, in_lexicon: true }}
                  onOpen={() => open(hit.strongs)}
                />
              ))}
            </ul>
          )
        ) : reference ? (
          words.isLoading ? (
            <p className="flex items-center gap-2 text-sm text-slate-400"><Loader2 size={14} className="animate-spin" /> Reading the words…</p>
          ) : (words.data?.words.length ?? 0) === 0 ? (
            <p className="text-sm text-slate-400" data-testid="bible-word-none">
              The concordance has no numbered words for {reference}. Search for a word above.
            </p>
          ) : (
            <>
              <ul className="space-y-1" data-testid="bible-word-list">
                {words.data?.words.map((word, i) => (
                  <WordRow key={`${word.strongs}-${i}`} word={word} onOpen={() => open(word.strongs)} />
                ))}
              </ul>
              <p className="mt-3 text-[11px] text-slate-500">
                {words.data?.source === 'interlinear'
                  ? 'Words from the interlinear.'
                  : "KJV words as Strong's concordance numbers them."}
              </p>
            </>
          )
        ) : null}
      </div>
    </div>
  );
}
