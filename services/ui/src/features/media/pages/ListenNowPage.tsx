import { useMemo, useState, type ReactNode } from 'react';
import { Library } from 'lucide-react';
import { useMediaHome, useBooksInProgress } from '../api/queries';
import type { MediaItem } from '../types';
import { formatTimeLeft, normalizePlayedAt } from '../format';
import { itemKind, itemSubtitle, playRequestFor } from '../items';
import { ActiveOutputCard } from '../components/ActiveOutputCard';
import { MediaCard } from '../components/MediaCard';
import { OutputPicker } from '../components/OutputPicker';
import { PlayingInYourHome } from '../components/PlayingInYourHome';
import { SearchEntry } from '../components/SearchEntry';
import { Shelf, ShelfRow } from '../components/Shelf';
import { CardSkeletons, EmptyState, ErrorRetry, PartialNotice } from '../components/StateViews';
import { serviceProblem } from '../copy';
import { secondaryButton } from '../components/ui';
import { MediaExplorer } from '../../../components/media/MediaExplorer';
import { useMediaScreen } from './MediaScreen';

/** A shelf card: what it shows and what tapping it does. */
interface CardModel {
  key: string;
  item: MediaItem;
  progress?: number;
  label?: string | null;
}

/**
 * Listen Now (§4.2): what is playing here and around the house, then shelves
 * of books in progress, recent listening, playlists, favourites and radio,
 * all from one `/api/media/home` call. Each shelf has its own loading,
 * empty and error state; one service being down leaves the other's shelves
 * up with a notice.
 */
export default function ListenNowPage() {
  const { playback } = useMediaScreen();
  const home = useMediaHome();
  const books = useBooksInProgress();
  const [showExplorer, setShowExplorer] = useState(false);

  const recent = useMemo(() => home.data?.recent ?? [], [home.data]);
  // Books in progress, with their artwork and length from the home payload.
  const continueCards = useMemo<CardModel[]>(() => {
    const byUri = new Map(recent.map((i) => [i.uri, i]));
    const cards: CardModel[] = [];
    const sorted = [...(books.data?.books ?? [])].sort(
      (a, b) => (normalizePlayedAt(b.last_played) ?? 0) - (normalizePlayedAt(a.last_played) ?? 0),
    );
    for (const book of sorted) {
      // The gateway reports progress as a percentage (older builds: a ratio).
      const raw = Number(book.progress ?? 0);
      const progress = raw > 1 ? raw / 100 : raw;
      if (book.is_complete || progress >= 1) continue;
      const uri = `abs://${book.id}`;
      const known = byUri.get(uri);
      const duration = known?.duration ?? null;
      cards.push({
        key: uri,
        item: known ?? { uri, name: book.title, media_type: 'audiobook', artist: book.author, artist_uri: '', album: '', album_uri: '', image: `/api/items/${book.id}/cover`, duration: null, version: '', favorite: null },
        progress,
        label: duration ? formatTimeLeft(progress * duration, duration) : `${Math.round(progress * 100)}% listened`,
      });
    }
    for (const item of home.data?.continue ?? []) {
      cards.push({ key: item.uri, item, label: null });
    }
    return cards;
  }, [books.data, home.data, recent]);

  const errors = home.data?.errors;
  const maProblem = serviceProblem('Music Assistant', errors?.ma);
  const absProblem = serviceProblem('Audiobookshelf', errors?.abs ?? books.data?.notice ?? (books.isError ? 'Audiobookshelf is unreachable' : null));
  const notConnected = /not connected/.test(maProblem ?? '') && (/not connected/.test(absProblem ?? '') || !absProblem);
  const nothingAtAll = home.data && continueCards.length === 0 && !recent.length && !home.data.playlists.length
    && !home.data.favorites.length && !home.data.radio.length;

  const card = ({ key, item, progress, label }: CardModel) => {
    const req = playRequestFor(item);
    const busyKey = `play:${item.uri}`;
    return (
      <MediaCard
        key={key}
        title={item.name}
        subtitle={itemSubtitle(item)}
        image={item.image}
        kind={itemKind(item)}
        progress={progress}
        label={label}
        busy={playback.busy === busyKey}
        onPlay={req ? () => void playback.play(req, busyKey) : undefined}
        menuItem={item}
      />
    );
  };
  const items = (list: MediaItem[]) => list.map((item) => card({ key: item.uri, item }));

  const shelf = (title: string, cards: ReactNode[], opts: { seeAll?: string; empty?: ReactNode } = {}) => {
    if (home.isLoading) return <Shelf title={title}><CardSkeletons label={`Loading ${title.toLowerCase()}`} /></Shelf>;
    if (!cards.length && !opts.empty) return null;
    return (
      <Shelf title={title} seeAll={cards.length ? opts.seeAll : undefined}>
        {cards.length ? <ShelfRow>{cards}</ShelfRow> : opts.empty}
      </Shelf>
    );
  };

  return (
    <>
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold text-[var(--site-text)] sm:text-3xl">Listen Now</h1>
        <div className="flex min-w-0 items-center gap-2">
          <SearchEntry />
          <OutputPicker />
        </div>
      </header>

      <ActiveOutputCard />
      <PlayingInYourHome />

      {home.isError ? (
        <ErrorRetry title="Couldn’t load your music" detail="The media service did not answer." onRetry={() => void home.refetch()} />
      ) : notConnected ? (
        <EmptyState
          title="Connect your music"
          detail="Add your music server and audiobook library in settings to see your listening here."
          action={{ label: 'Open settings', to: '/identity' }}
        />
      ) : (
        <>
          {maProblem ? <PartialNotice>{maProblem}</PartialNotice> : null}
          {absProblem ? <PartialNotice>{absProblem}</PartialNotice> : null}
          {nothingAtAll && !maProblem && !absProblem ? (
            <EmptyState title="Nothing here yet" detail="Music and books you play show up here." />
          ) : null}
          {shelf('Continue listening', continueCards.map(card), { seeAll: '/media/library/audiobooks' })}
          {shelf('Recently played', items(recent))}
          {shelf('Your playlists', items(home.data?.playlists ?? []), {
            seeAll: '/media/library/playlists',
            empty: maProblem || nothingAtAll ? null : <EmptyState title="No playlists yet" detail="Playlists you make in your music library appear here." />,
          })}
          {shelf('Favorites', items(home.data?.favorites ?? []), { seeAll: '/media/library/favorites' })}
          {shelf('Radio', items(home.data?.radio ?? []), { seeAll: '/media/library/radio' })}
        </>
      )}

      <button type="button" onClick={() => setShowExplorer(true)} className={`${secondaryButton} w-full`}>
        <Library size={18} aria-hidden />
        Browse all media
      </button>
      <MediaExplorer open={showExplorer} onClose={() => setShowExplorer(false)} onPlay={playback.play} onQueue={playback.enqueue} />
    </>
  );
}
