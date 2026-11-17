import { useMemo, useState } from 'react';
import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { ChevronDown, HelpCircle, Loader2, Search, X } from 'lucide-react';
import { api } from '../../services/api';
import { errorText } from '../../lib/errorText';
import { SEARCH_PAGE, effectiveQuery, highlighted } from './searchText';
import type { BibleSearchHit, BibleSearchMatch, BibleSearchResult, BibleSearchSort, BibleVersionInfo } from '../../types/api';


const PREFS_KEY = 'jarvis_bible_search_prefs';

/** Where to look. Section keys are the ones the server's `in:` understands. */
const SECTIONS: Array<{ id: string; label: string }> = [
  { id: '', label: 'Whole Bible' },
  { id: 'ot', label: 'Old Testament' },
  { id: 'nt', label: 'New Testament' },
  { id: 'law', label: 'The Law' },
  { id: 'history', label: 'History' },
  { id: 'poetry', label: 'Poetry & Wisdom' },
  { id: 'prophets', label: 'Prophets' },
  { id: 'gospels', label: 'Gospels' },
  { id: 'acts', label: 'Acts' },
  { id: 'pauline', label: "Paul's Letters" },
  { id: 'generalepistles', label: 'General Letters' },
  { id: 'rev', label: 'Revelation' },
];

const MATCHES: Array<{ id: BibleSearchMatch; label: string }> = [
  { id: 'all', label: 'All words' },
  { id: 'any', label: 'Any word' },
  { id: 'exact', label: 'Exact phrase' },
];

const TIPS: Array<[string, string]> = [
  ['grace faith', 'verses with both words, in any form (love finds loved and loveth)'],
  ['"living water"', 'the exact phrase'],
  ['faith OR works', 'either word'],
  ['faith -law', 'leave out verses with a word'],
  ['rejoic*', 'words that start with this'],
  ['faith NEAR/5 works', 'the words close together'],
  ['in:John  in:Rom-Gal  in:gospels', 'a book, a range of books, or a section'],
  ['ver:all  ver:esv', 'every translation, or one'],
  ['G26  H2617', "a Greek or Hebrew word by its Strong's number"],
  ['has:highlight  has:note', 'only verses you marked'],
];

type Prefs = { match: BibleSearchMatch; every: boolean };

function loadPrefs(): Prefs {
  try {
    const saved = JSON.parse(localStorage.getItem(PREFS_KEY) ?? '{}') as Partial<Prefs>;
    return {
      match: MATCHES.some((m) => m.id === saved.match) ? (saved.match as BibleSearchMatch) : 'all',
      every: saved.every === true,
    };
  } catch {
    return { match: 'all', every: false };
  }
}

function savePrefs(prefs: Prefs) {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
  } catch {
    /* a private window: the choice lasts for this visit only */
  }
}

function describe(result: BibleSearchResult | undefined): string {
  const parsed = result?.parsed;
  if (!parsed) return '';
  const parts: string[] = [];
  if (parsed.all.length) parts.push(parsed.all.join(' + '));
  if (parsed.strongs.length) parts.push(`Strong's ${parsed.strongs.join(', ')}`);
  if (parsed.has.length) parts.push(`your ${parsed.has.map((h) => (h === 'any' ? 'marks' : `${h}s`)).join(' and ')}`);
  if (parsed.without.length) parts.push(`without ${parsed.without.join(', ')}`);
  if (parsed.scopes.length) parts.push(`in ${parsed.scopes.join(', ')}`);
  return parts.join(' · ');
}

function HitContext({ hit }: { hit: BibleSearchHit }) {
  const start = Math.max(1, hit.verse - 2);
  const ref = `${hit.book_name} ${hit.chapter}:${start}-${hit.verse + 2}`;
  const { data, isLoading, error } = useQuery({
    queryKey: ['bible-passage', hit.version, ref],
    queryFn: () => api.getBiblePassage(ref, hit.version),
    retry: 0,
  });
  if (isLoading) return <p className="text-xs text-slate-500">Loading the verses around it…</p>;
  if (error || !data) return <p className="text-xs text-rose-300">Could not load the verses around it.</p>;
  return (
    <div className="space-y-1 border-l-2 border-white/10 pl-3" data-testid="bible-search-context">
      {data.verses.map((v) => (
        <p
          key={v.verse}
          className={`text-sm font-serif leading-relaxed ${v.verse === hit.verse ? 'text-slate-100' : 'text-slate-400'}`}
        >
          <sup className="mr-1 text-[10px] text-slate-500">{v.verse}</sup>
          {v.text}
        </p>
      ))}
    </div>
  );
}

interface Props {
  term: string;
  version: string;
  versions: BibleVersionInfo[];
  onOpen: (reference: string) => void;
  onClear: () => void;
}

/**
 * Bible search results: filters, matches by book, highlighted verses, the
 * verses around a hit, and paging. The filters become part of the query in
 * the server's own syntax (`in:gospels`, `ver:all`), so what the screen does
 * and what a typed search does are the same search.
 */
export default function BibleSearchResults({ term, version, versions, onOpen, onClear }: Props) {
  const [prefs, setPrefs] = useState<Prefs>(loadPrefs);
  const [where, setWhere] = useState('');
  const [sort, setSort] = useState<BibleSearchSort | undefined>(undefined);
  const [tips, setTips] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const installed = versions.filter((v) => v.installed !== false);
  const multiple = installed.length > 1;
  const q = effectiveQuery(term, where, prefs.every && multiple);
  const ready = term.trim().length >= 2;

  const search = useInfiniteQuery({
    queryKey: ['bible-search', q, version, prefs.match, sort],
    queryFn: ({ pageParam }) =>
      api.searchBible(q, { version, limit: SEARCH_PAGE, offset: pageParam, match: prefs.match, sort }),
    initialPageParam: 0,
    getNextPageParam: (last) => {
      const next = (last.offset ?? 0) + last.count;
      return last.total !== undefined && next < last.total ? next : undefined;
    },
    enabled: ready,
    retry: 0,
  });
  const first = search.data?.pages[0];
  const hits = useMemo(() => search.data?.pages.flatMap((p) => p.results) ?? [], [search.data]);
  const total = first?.total ?? first?.count ?? 0;
  const byBook = first?.by_book ?? [];
  const most = Math.max(1, ...byBook.map((b) => b.count));
  const versionName = (code: string) => versions.find((v) => v.code === code)?.name ?? code.toUpperCase();
  const sortShown = sort ?? first?.sort ?? 'rank';
  const onBook = byBook.length === 1 && where === byBook[0].osis;

  const change = (patch: Partial<Prefs>) => {
    const next = { ...prefs, ...patch };
    setPrefs(next);
    savePrefs(next);
  };

  const chip = (active: boolean) =>
    `min-h-11 shrink-0 rounded-full border px-3 text-xs font-semibold transition-colors ${
      active ? 'border-sky-400/40 bg-sky-500/15 text-sky-100' : 'border-white/10 bg-white/[0.04] text-slate-300 hover:bg-white/10'
    }`;

  return (
    <section className="glass-panel rounded-2xl p-4 border border-white/10" data-testid="bible-search-results">
      <div className="flex items-center gap-2 mb-1">
        <Search size={15} className="text-sky-300 shrink-0" />
        <h2 className="min-w-0 flex-1 text-sm font-bold text-white truncate">Results for “{term}”</h2>
        <button
          type="button"
          onClick={() => setTips((t) => !t)}
          aria-expanded={tips}
          aria-label="Search tips"
          data-testid="bible-search-tips-toggle"
          className="min-h-11 min-w-11 shrink-0 flex items-center justify-center rounded-xl text-slate-400 hover:text-white aria-expanded:text-sky-200"
        >
          <HelpCircle size={16} />
        </button>
        <button
          type="button"
          onClick={onClear}
          aria-label="Clear search"
          className="min-h-11 min-w-11 shrink-0 flex items-center justify-center rounded-xl text-slate-400 hover:text-white"
        >
          <X size={15} />
        </button>
      </div>
      {ready && first ? (
        <p className="mb-3 text-xs text-slate-400" data-testid="bible-search-summary">
          <span className="font-semibold text-slate-200">
            {total.toLocaleString()} {total === 1 ? 'verse' : 'verses'}
          </span>
          {describe(first) ? ` · ${describe(first)}` : ''}
        </p>
      ) : null}

      {tips ? (
        <dl className="mb-3 grid gap-x-3 gap-y-1.5 rounded-xl bg-black/20 p-3 text-xs sm:grid-cols-[auto_1fr]" data-testid="bible-search-tips">
          {TIPS.map(([example, meaning]) => (
            <div key={example} className="contents">
              <dt className="font-mono text-sky-200 break-words">{example}</dt>
              <dd className="text-slate-400 mb-1 sm:mb-0">{meaning}</dd>
            </div>
          ))}
        </dl>
      ) : null}

      <div className="mb-3 flex flex-wrap gap-2" data-testid="bible-search-filters">
        <div className="flex flex-wrap gap-2" role="group" aria-label="Match">
          {MATCHES.map((m) => (
            <button
              key={m.id}
              type="button"
              aria-pressed={prefs.match === m.id}
              onClick={() => change({ match: m.id })}
              className={chip(prefs.match === m.id)}
              data-testid={`bible-search-match-${m.id}`}
            >
              {m.label}
            </button>
          ))}
        </div>
        <label className="relative">
          <span className="sr-only">Where to search</span>
          <select
            value={SECTIONS.some((s) => s.id === where) ? where : '__book'}
            onChange={(e) => setWhere(e.target.value === '__book' ? where : e.target.value)}
            className={`${chip(Boolean(where))} appearance-none pr-8`}
            data-testid="bible-search-where"
          >
            {SECTIONS.map((s) => (
              <option key={s.id} value={s.id}>
                {s.label}
              </option>
            ))}
            {where && !SECTIONS.some((s) => s.id === where) ? (
              <option value="__book">{byBook.find((b) => b.osis === where)?.name ?? where}</option>
            ) : null}
          </select>
          <ChevronDown size={14} className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-slate-400" />
        </label>
        {multiple ? (
          <button
            type="button"
            aria-pressed={prefs.every}
            onClick={() => change({ every: !prefs.every })}
            className={chip(prefs.every)}
            data-testid="bible-search-every"
          >
            All translations
          </button>
        ) : null}
        <button
          type="button"
          onClick={() => setSort(sortShown === 'rank' ? 'canon' : 'rank')}
          className={chip(false)}
          data-testid="bible-search-sort"
        >
          {sortShown === 'rank' ? 'Best match first' : 'Bible order'}
        </button>
      </div>

      {byBook.length > 1 || onBook ? (
        <div className="mb-3 -mx-1 overflow-x-auto pb-1" data-testid="bible-search-books">
          <ul className="flex min-w-max items-end gap-1 px-1">
            {byBook.map((b) => (
              <li key={b.osis}>
                <button
                  type="button"
                  onClick={() => setWhere(where === b.osis ? '' : b.osis)}
                  aria-pressed={where === b.osis}
                  title={`${b.name}: ${b.count}`}
                  aria-label={`${b.name}, ${b.count} ${b.count === 1 ? 'verse' : 'verses'}`}
                  className="flex min-h-11 min-w-11 flex-col items-center justify-end gap-1 rounded-lg px-1.5 py-1 hover:bg-white/5 aria-pressed:bg-sky-500/15"
                >
                  <span
                    className="w-4 rounded-sm bg-sky-400/60"
                    style={{ height: `${Math.max(3, Math.round((b.count / most) * 36))}px` }}
                    aria-hidden
                  />
                  <span className="text-[10px] leading-none text-slate-400">{b.osis}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {first?.index === 'like' ? (
        <p className="mb-3 text-xs text-amber-200/80">Search is still getting ready, so some results may be missing.</p>
      ) : null}

      {!ready ? (
        <p className="text-sm text-slate-400">Type at least two letters to search.</p>
      ) : search.isLoading ? (
        <div className="space-y-2">
          {[0, 1, 2].map((i) => (
            <div key={i} className="skeleton h-10 rounded-xl" />
          ))}
        </div>
      ) : search.error ? (
        <p className="text-sm text-rose-300" role="alert" data-testid="bible-search-error">
          {errorText(search.error, 'Search failed. Try again in a moment.')}
        </p>
      ) : !hits.length ? (
        <p className="text-sm text-slate-400">
          Nothing found. Try fewer words, “Any word”, or the whole Bible.
        </p>
      ) : (
        <>
          <ul className="space-y-1.5">
            {hits.map((hit) => {
              const key = `${hit.osis}-${hit.chapter}-${hit.verse}`;
              return (
                <li key={key} className="rounded-xl hover:bg-white/[0.03]">
                  <div className="flex items-start gap-1">
                    <button
                      type="button"
                      onClick={() => onOpen(hit.reference)}
                      className="min-w-0 flex-1 text-left rounded-xl px-3 py-2.5 min-h-11"
                    >
                      <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                        <span className="text-xs font-semibold text-amber-300">{hit.reference}</span>
                        {prefs.every && hit.versions?.length ? (
                          <span className="flex flex-wrap gap-1">
                            {hit.versions.map((code) => (
                              <span
                                key={code}
                                title={versionName(code)}
                                className="rounded bg-white/10 px-1.5 text-[10px] font-semibold uppercase text-slate-300"
                              >
                                {code}
                              </span>
                            ))}
                          </span>
                        ) : null}
                      </span>
                      <span className="block text-sm text-slate-300 font-serif leading-relaxed">
                        {highlighted(hit.text, hit.highlights)}
                      </span>
                    </button>
                    <button
                      type="button"
                      onClick={() => setOpen(open === key ? null : key)}
                      aria-expanded={open === key}
                      aria-label={`Verses around ${hit.reference}`}
                      className="mt-1 min-h-11 min-w-11 shrink-0 flex items-center justify-center rounded-xl text-slate-500 hover:text-white aria-expanded:text-sky-200"
                    >
                      <ChevronDown size={16} className={open === key ? 'rotate-180 transition-transform' : 'transition-transform'} />
                    </button>
                  </div>
                  {open === key ? (
                    <div className="px-3 pb-3">
                      <HitContext hit={hit} />
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ul>
          {search.hasNextPage ? (
            <button
              type="button"
              onClick={() => void search.fetchNextPage()}
              disabled={search.isFetchingNextPage}
              className="glass-button mt-3 w-full min-h-11 text-sm font-semibold inline-flex items-center justify-center gap-2"
              data-testid="bible-search-more"
            >
              {search.isFetchingNextPage ? <Loader2 size={14} className="animate-spin" /> : null}
              Show more ({(total - hits.length).toLocaleString()} left)
            </button>
          ) : null}
        </>
      )}
    </section>
  );
}
