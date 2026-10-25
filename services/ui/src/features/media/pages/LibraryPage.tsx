import { useMemo } from 'react';
import { Link, Navigate, useParams, useSearchParams } from 'react-router-dom';
import { ChevronLeft, ChevronRight, Library as LibraryIcon } from 'lucide-react';
import type { LibraryTab } from '../api/client';
import { useMediaFavorites, useMediaLibrary } from '../api/queries';
import type { MediaItem, MediaLibrary } from '../types';
import { serviceProblem } from '../copy';
import { ItemList } from '../components/ItemList';
import { EmptyState, ErrorRetry, PartialNotice, RowSkeletons } from '../components/StateViews';
import { OutputPicker } from '../components/OutputPicker';
import { SearchEntry } from '../components/SearchEntry';
import { chip, muted, row, text } from '../components/ui';

type Tab = LibraryTab | 'favorites';

const TABS: Array<{ id: Tab; label: string; empty: string }> = [
  { id: 'playlists', label: 'Playlists', empty: 'No playlists yet.' },
  { id: 'artists', label: 'Artists', empty: 'No artists in your library yet.' },
  { id: 'albums', label: 'Albums', empty: 'No albums in your library yet.' },
  { id: 'tracks', label: 'Songs', empty: 'No songs in your library yet.' },
  { id: 'audiobooks', label: 'Audiobooks', empty: 'No audiobook libraries found.' },
  { id: 'podcasts', label: 'Podcasts', empty: 'No podcasts in your library yet.' },
  { id: 'radio', label: 'Radio', empty: 'No radio stations yet.' },
  { id: 'favorites', label: 'Favorites', empty: 'Nothing marked as a favorite yet.' },
];

/**
 * The library (§4.1, UX-18): one tab per kind, paged 50 at a time as it
 * scrolls (no cap), and Audiobookshelf's libraries, each opening to its
 * books or podcasts. The tab is the URL (`/media/library/albums`), so it can
 * be linked to and back works.
 */
export default function LibraryPage() {
  const { tab = 'playlists' } = useParams();
  const known = TABS.find((t) => t.id === tab);
  if (!known) return <Navigate to="/media/library/playlists" replace />;
  return (
    <>
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h1 className={`flex items-center gap-2 text-2xl font-bold sm:text-3xl ${text}`}>
          <LibraryIcon size={26} className="text-[var(--site-accent-text)]" aria-hidden />Library
        </h1>
        <div className="flex min-w-0 items-center gap-2">
          <SearchEntry />
          <OutputPicker />
        </div>
      </header>
      <nav aria-label="Library sections" className="-mx-1 flex gap-2 overflow-x-auto px-1 pb-1">
        {TABS.map((t) => (
          <Link key={t.id} to={`/media/library/${t.id}`} aria-current={t.id === tab ? 'page' : undefined} className={chip(t.id === tab)}>
            {t.label}
          </Link>
        ))}
      </nav>
      {known.id === 'favorites' ? <FavoritesTab empty={known.empty} /> : known.id === 'audiobooks' ? <BooksTab empty={known.empty} /> : (
        <MaTab key={known.id} tab={known.id} label={known.label} empty={known.empty} />
      )}
    </>
  );
}

function MaTab({ tab, label, empty }: { tab: LibraryTab; label: string; empty: string }) {
  const q = useMediaLibrary(tab);
  const items = useMemo(() => q.data?.pages.flatMap((p) => p.items) ?? [], [q.data]);
  const problem = serviceProblem('Music Assistant', q.data?.pages[0]?.errors.ma);
  if (q.isLoading) return <RowSkeletons label={`Loading ${label.toLowerCase()}`} />;
  if (q.isError) return <ErrorRetry title={`Couldn’t load ${label.toLowerCase()}`} detail="The media service did not answer." onRetry={() => void q.refetch()} />;
  if (problem && !items.length) return <ErrorRetry title={`Couldn’t load ${label.toLowerCase()}`} detail={problem} onRetry={() => void q.refetch()} />;
  if (!items.length) return <EmptyState title={empty} detail="Add music in your music library and it shows up here." />;
  return (
    <ItemList
      items={items}
      label={label}
      hasMore={q.hasNextPage}
      loadingMore={q.isFetchingNextPage}
      onLoadMore={() => void q.fetchNextPage()}
    />
  );
}

function FavoritesTab({ empty }: { empty: string }) {
  const q = useMediaFavorites();
  if (q.isLoading) return <RowSkeletons label="Loading favorites" />;
  if (q.isError) return <ErrorRetry title="Couldn’t load favorites" detail="The media service did not answer." onRetry={() => void q.refetch()} />;
  const problem = serviceProblem('Music Assistant', q.data?.errors.ma);
  const items = q.data?.items ?? [];
  return (
    <>
      {problem ? <PartialNotice>{problem}</PartialNotice> : null}
      {items.length ? <ItemList items={items} label="Favorites" /> : problem ? null : <EmptyState title={empty} detail="Tap the heart on anything you play to keep it here." />}
    </>
  );
}

/** Audiobookshelf: pick a library, then its books (or podcasts). */
function BooksTab({ empty }: { empty: string }) {
  const [params, setParams] = useSearchParams();
  const libraryId = params.get('library') ?? undefined;
  const libraries = useMediaLibrary('audiobooks');
  const books = useMediaLibrary('audiobooks', libraryId, !!libraryId);
  const list: MediaLibrary[] = libraries.data?.pages[0]?.libraries ?? [];
  const current = list.find((l) => l.id === libraryId);

  if (!libraryId) {
    if (libraries.isLoading) return <RowSkeletons label="Loading libraries" />;
    if (libraries.isError) return <ErrorRetry title="Couldn’t load your audiobook libraries" detail="The media service did not answer." onRetry={() => void libraries.refetch()} />;
    const problem = serviceProblem('Audiobookshelf', libraries.data?.pages[0]?.errors.abs);
    if (problem) return <ErrorRetry title="Couldn’t load your audiobook libraries" detail={problem} onRetry={() => void libraries.refetch()} />;
    if (!list.length) return <EmptyState title={empty} detail="Connect your audiobook server in settings." action={{ label: 'Open settings', to: '/identity' }} />;
    return (
      <ul className="space-y-1" aria-label="Audiobook libraries">
        {list.map((lib) => (
          <li key={lib.id}>
            <button type="button" onClick={() => setParams({ library: lib.id })} className={row}>
              <span className="min-w-0 flex-1">
                <span className={`block truncate text-sm font-medium ${text}`}>{lib.name}</span>
                <span className={`block text-xs capitalize ${muted}`}>{lib.media_type === 'podcast' ? 'Podcasts' : 'Audiobooks'}</span>
              </span>
              <ChevronRight size={16} className={muted} aria-hidden />
            </button>
          </li>
        ))}
      </ul>
    );
  }

  // A podcast library's items are podcasts (they open to their episodes).
  const items: MediaItem[] = (books.data?.pages.flatMap((p) => p.items) ?? []).map((item) => (
    current?.media_type === 'podcast' ? { ...item, media_type: 'podcast' } : item
  ));
  const problem = serviceProblem('Audiobookshelf', books.data?.pages[0]?.errors.abs);
  return (
    <>
      <button type="button" onClick={() => setParams({})} className={`inline-flex min-h-11 items-center gap-1 rounded-xl px-2 text-sm ${muted} hover:bg-[var(--site-card-bg-hover)]`}>
        <ChevronLeft size={16} aria-hidden />All libraries
      </button>
      {current ? <h2 className={`text-lg font-semibold ${text}`}>{current.name}</h2> : null}
      {books.isLoading ? <RowSkeletons label="Loading books" />
        : books.isError ? <ErrorRetry title="Couldn’t load this library" detail="The media service did not answer." onRetry={() => void books.refetch()} />
          : problem ? <ErrorRetry title="Couldn’t load this library" detail={problem} onRetry={() => void books.refetch()} />
            : !items.length ? <EmptyState title="This library is empty" />
              : <ItemList items={items} label={current?.name ?? 'Books'} />}
    </>
  );
}
