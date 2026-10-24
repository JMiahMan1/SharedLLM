import { useEffect, useRef, type KeyboardEvent } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { History, Search, X } from 'lucide-react';
import { MIN_SEARCH_LENGTH, useMediaSearch } from '../api/queries';
import type { MediaItem, MediaSearchResponse } from '../types';
import { detailPath, itemKind, itemSubtitle, KIND_LABEL, playRequestFor } from '../items';
import { serviceProblem } from '../copy';
import { useMediaStore } from '../store/mediaStore';
import { Artwork } from '../components/Artwork';
import { ItemContextMenu } from '../components/ItemContextMenu';
import { MediaCard } from '../components/MediaCard';
import { Shelf, ShelfRow } from '../components/Shelf';
import { EmptyState, ErrorRetry, PartialNotice, RowSkeletons } from '../components/StateViews';
import { TrackRow } from '../components/TrackRow';
import { chip, muted, primaryButton, row, text } from '../components/ui';
import { useMediaScreen } from './MediaScreen';

type Bucket = 'tracks' | 'artists' | 'albums' | 'playlists' | 'audiobooks' | 'podcasts';

const FILTERS: Array<{ id: 'all' | Bucket; label: string }> = [
  { id: 'all', label: 'All' },
  { id: 'tracks', label: 'Songs' },
  { id: 'artists', label: 'Artists' },
  { id: 'albums', label: 'Albums' },
  { id: 'playlists', label: 'Playlists' },
  { id: 'audiobooks', label: 'Audiobooks' },
  { id: 'podcasts', label: 'Podcasts' },
];

/** Shown in "All" before "See more" (the filter shows everything). */
const PREVIEW = 5;

function countFor(data: MediaSearchResponse, filter: 'all' | Bucket): number {
  const buckets: Bucket[] = filter === 'all' ? ['tracks', 'artists', 'albums', 'playlists', 'audiobooks', 'podcasts'] : [filter];
  return buckets.reduce((n, b) => n + data[b].length, 0) + (filter === 'all' ? data.authors.length : 0);
}

/**
 * Search (§4.2): one box over Music Assistant and Audiobookshelf, results
 * grouped (top result, songs, artists, albums, playlists, books, podcasts,
 * authors) with a filter row. The query lives in the URL, so back and
 * forward keep it (UX-42); the last ten searches are offered when the box is
 * empty. ↓ moves from the box into the results, ↑/↓ between them, Enter
 * plays or opens; Enter in the box takes the top result.
 */
export default function SearchPage() {
  const [params, setParams] = useSearchParams();
  const q = params.get('q') ?? '';
  const filterParam = params.get('type') as Bucket | null;
  const filter: 'all' | Bucket = FILTERS.some((f) => f.id === filterParam) ? (filterParam as Bucket) : 'all';
  const search = useMediaSearch(q);
  const { playback } = useMediaScreen();
  const recent = useMediaStore((s) => s.recentSearches);
  const { addRecentSearch, clearRecentSearches } = useMediaStore((s) => s.actions);
  const inputRef = useRef<HTMLInputElement>(null);
  const resultsRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Arriving from a header field mid-word: keep typing where it left off.
    const el = inputRef.current;
    el?.focus();
    el?.setSelectionRange(el.value.length, el.value.length);
  }, []);

  const update = (next: { q?: string; type?: 'all' | Bucket }) => {
    const query = next.q ?? q;
    const type = next.type ?? filter;
    const p: Record<string, string> = {};
    if (query) p.q = query;
    if (type !== 'all') p.type = type;
    // Typing replaces the entry; a new filter is a step back can undo.
    setParams(p, { replace: next.q !== undefined });
  };

  const results = (): HTMLElement[] => Array.from(resultsRef.current?.querySelectorAll<HTMLElement>('[data-result]') ?? []);
  const onInputKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      results()[0]?.focus();
    } else if (e.key === 'Enter') {
      e.preventDefault();
      addRecentSearch(q);
      results()[0]?.click();
    }
  };
  const onResultsKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
    const list = results();
    const i = list.indexOf(document.activeElement as HTMLElement);
    if (i < 0) return;
    e.preventDefault();
    if (e.key === 'ArrowUp' && i === 0) inputRef.current?.focus();
    else list[e.key === 'ArrowDown' ? Math.min(list.length - 1, i + 1) : i - 1]?.focus();
  };

  const searching = search.debouncedQuery.length >= MIN_SEARCH_LENGTH;
  const data = searching ? search.data : undefined;
  const show = (b: Bucket) => filter === 'all' || filter === b;
  const cap = (list: MediaItem[]) => (filter === 'all' ? list.slice(0, PREVIEW) : list);
  const card = (item: MediaItem) => {
    const req = playRequestFor(item);
    const key = `play:${item.uri}`;
    return (
      <MediaCard
        key={item.uri}
        title={item.name}
        subtitle={itemSubtitle(item)}
        image={item.image}
        kind={itemKind(item)}
        to={detailPath(item)}
        busy={playback.busy === key}
        onPlay={req ? () => void playback.play(req, key) : undefined}
        menuItem={item}
      />
    );
  };
  const cards = (b: Bucket, title: string) => (data && show(b) && data[b].length ? (
    <Shelf title={title} seeAll={filter === 'all' && data[b].length > PREVIEW ? `/media/search?q=${encodeURIComponent(q)}&type=${b}` : undefined}>
      {filter === 'all'
        ? <ShelfRow>{cap(data[b]).map(card)}</ShelfRow>
        : <div className="flex flex-wrap gap-3 sm:gap-4">{data[b].map(card)}</div>}
    </Shelf>
  ) : null);
  const rows = (b: Bucket, title: string) => (data && show(b) && data[b].length ? (
    <Shelf title={title} seeAll={filter === 'all' && data[b].length > PREVIEW ? `/media/search?q=${encodeURIComponent(q)}&type=${b}` : undefined}>
      <ul className="space-y-1">{cap(data[b]).map((item) => <TrackRow key={item.uri} item={item} />)}</ul>
    </Shelf>
  ) : null);

  const top = data?.top && filter === 'all' ? data.top : null;
  const topReq = top ? playRequestFor(top) : null;
  const topPath = top ? detailPath(top) : null;
  const total = data ? countFor(data, filter) : 0;

  return (
    <>
      <header className="space-y-3">
        <h1 className={`text-2xl font-bold sm:text-3xl ${text}`}>Search</h1>
        <div className="relative">
          <Search size={18} className={`pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 ${muted}`} aria-hidden />
          <input
            ref={inputRef}
            type="search"
            data-media-search
            value={q}
            onChange={(e) => update({ q: e.target.value })}
            onKeyDown={onInputKey}
            placeholder="Songs, artists, albums, books, podcasts"
            aria-label="Search music and books"
            aria-controls="media-search-results"
            className="glass-input h-12 w-full rounded-xl pl-10 pr-12 text-base"
          />
          {q ? (
            <button type="button" onClick={() => { update({ q: '' }); inputRef.current?.focus(); }} aria-label="Clear search" className="absolute right-0 top-0 flex h-12 w-12 items-center justify-center">
              <X size={18} className={muted} aria-hidden />
            </button>
          ) : null}
        </div>
        <div className="flex gap-2 overflow-x-auto pb-1" role="group" aria-label="Show">
          {FILTERS.map((f) => (
            <button key={f.id} type="button" aria-pressed={filter === f.id} onClick={() => update({ type: f.id })} className={chip(filter === f.id)}>
              {f.label}
            </button>
          ))}
        </div>
      </header>

      <div
        id="media-search-results"
        ref={resultsRef}
        onKeyDown={onResultsKey}
        onClickCapture={() => { if (searching) addRecentSearch(q); }}
        aria-busy={search.isFetching}
        className="space-y-6"
      >
        <p className="sr-only" role="status">{data ? `${total} result${total === 1 ? '' : 's'}` : ''}</p>
        {!searching ? (
          recent.length ? (
            <section aria-labelledby="recent-searches" className="space-y-2">
              <div className="flex items-center justify-between">
                <h2 id="recent-searches" className={`text-lg font-semibold ${text}`}>Recent searches</h2>
                <button type="button" onClick={clearRecentSearches} className={`min-h-11 rounded-xl px-3 text-sm ${muted} hover:bg-[var(--site-card-bg-hover)]`}>Clear</button>
              </div>
              <ul className="space-y-1">
                {recent.map((r) => (
                  <li key={r}>
                    <button type="button" data-result onClick={() => update({ q: r })} className={row}>
                      <History size={18} className={muted} aria-hidden />
                      <span className={`truncate text-sm ${text}`}>{r}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          ) : (
            <p className={`text-sm ${muted}`}>Search your music library and audiobooks.</p>
          )
        ) : search.isError ? (
          <ErrorRetry title="Couldn’t search" detail="The media service did not answer." onRetry={() => void search.refetch()} />
        ) : !data ? (
          <RowSkeletons label="Searching" />
        ) : (
          <>
            {serviceProblem('Music Assistant', data.errors.ma) ? <PartialNotice>{serviceProblem('Music Assistant', data.errors.ma)}</PartialNotice> : null}
            {serviceProblem('Audiobookshelf', data.errors.abs) ? <PartialNotice>{serviceProblem('Audiobookshelf', data.errors.abs)}</PartialNotice> : null}
            {total === 0 ? <EmptyState title={`No results for “${search.debouncedQuery}”`} detail="Try another spelling, or fewer words." /> : null}
            {top ? (
              <section aria-labelledby="top-result" className="space-y-3">
                <h2 id="top-result" className={`text-lg font-semibold ${text}`}>Top result</h2>
                <div className="glass-panel flex items-center gap-4 rounded-[var(--radius-panel)] p-4">
                  <Artwork image={top.image} kind={itemKind(top)} size={96} />
                  <div className="min-w-0 flex-1">
                    <p className={`truncate text-xl font-bold ${text}`}>{top.name}</p>
                    <p className={`truncate text-sm ${muted}`}>{KIND_LABEL[itemKind(top)]}{top.artist ? ` · ${top.artist}` : ''}</p>
                    <div className="mt-3 flex items-center gap-2">
                      {topReq ? (
                        <button type="button" data-result onClick={() => void playback.play(topReq, `play:${top.uri}`)} className={primaryButton} aria-label={`Play ${top.name}`}>
                          Play
                        </button>
                      ) : topPath ? (
                        <Link to={topPath} data-result className={primaryButton}>Open</Link>
                      ) : null}
                      <ItemContextMenu item={top} />
                    </div>
                  </div>
                </div>
              </section>
            ) : null}
            {rows('tracks', 'Songs')}
            {cards('artists', 'Artists')}
            {cards('albums', 'Albums')}
            {cards('playlists', 'Playlists')}
            {rows('audiobooks', 'Audiobooks')}
            {cards('podcasts', 'Podcasts')}
            {filter === 'all' && data.authors.length ? (
              <Shelf title="Authors">
                <ul className="space-y-1">
                  {data.authors.map((a) => (
                    <li key={a.uri}>
                      <button type="button" data-result onClick={() => update({ q: a.name, type: 'audiobooks' })} className={row}>
                        <Artwork kind="author" size={44} />
                        <span className="min-w-0">
                          <span className={`block truncate text-sm font-medium ${text}`}>{a.name}</span>
                          <span className={`block text-xs ${muted}`}>Their books</span>
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              </Shelf>
            ) : null}
          </>
        )}
      </div>
    </>
  );
}
