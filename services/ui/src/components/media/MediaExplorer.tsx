import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  BookOpen, ChevronLeft, ChevronRight, Clock, Grid3X3, Library, List, ListEnd, ListStart, Loader2, Mic2, Music, Search, Users, X,
} from 'lucide-react';
import { api } from '../../services/api';
import { errorText } from '../../lib/errorText';
import { ROW_CLASS, type PlayHandler, type PlayRequest, type QueueHandler, type QueueOption } from './types';

function Loading() {
  return (
    <div className="flex items-center justify-center py-8" role="status" aria-label="Loading">
      <Loader2 size={24} className="text-slate-500 animate-spin" />
    </div>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return <div className="glass-panel rounded-2xl p-6 text-center text-slate-500 text-sm">{children}</div>;
}

function Failed({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="p-4 rounded-xl bg-red-500/10 border border-red-500/20 text-center" role="alert">
      <p className="text-sm text-red-300">{message}</p>
      {onRetry && (
        <button type="button" onClick={onRetry} className="mt-2 min-h-11 px-4 text-sm text-red-200 underline">
          Try again
        </button>
      )}
    </div>
  );
}

function SectionTitle({ icon, children }: { icon: ReactNode; children: ReactNode }) {
  return (
    <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2 flex items-center gap-1.5">
      {icon}{children}
    </h3>
  );
}

/** "12 Oct 2026 • 42 min" for an episode, from what MA knows. */
function episodeSubtitle(e: { published: number | null; duration: number | null }): string | undefined {
  const parts: string[] = [];
  if (e.published) parts.push(new Date(e.published).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }));
  if (e.duration) parts.push(`${Math.round(e.duration / 60)} min`);
  return parts.join(' • ') || undefined;
}

/** A playable row: icon (or spinner while it starts), title, subtitle, badge. */
function PlayRow({
  icon, title, subtitle, badge, busy, disabled, onClick,
}: {
  icon: ReactNode; title: string; subtitle?: string; badge?: string; busy: boolean; disabled: boolean; onClick: () => void;
}) {
  return (
    <button type="button" onClick={onClick} disabled={disabled} className={ROW_CLASS}>
      <span className="w-5 shrink-0 flex justify-center">{busy ? <Loader2 size={18} className="animate-spin text-cyan-300" /> : icon}</span>
      <span className="min-w-0 flex-1">
        <span className="block text-white text-sm font-medium truncate">{title}</span>
        {subtitle ? <span className="block text-xs text-slate-400 truncate">{subtitle}</span> : null}
      </span>
      {badge ? <span className="text-[11px] px-2 py-0.5 rounded-full bg-white/10 text-slate-300 shrink-0 capitalize">{badge}</span> : null}
    </button>
  );
}

/**
 * Full-screen (phone) / dialog (desktop) browser for everything playable:
 * Music Assistant search, playlists and recents, and Audiobookshelf libraries,
 * books and podcasts.
 */
export function MediaExplorer({ open, onClose, onPlay, onQueue }: {
  open: boolean;
  onClose: () => void;
  onPlay: PlayHandler;
  /** Queue a Music Assistant item on the selected device instead of playing it now. */
  onQueue?: QueueHandler;
}) {
  const [tab, setTab] = useState<'ma' | 'abs'>('ma');
  const [search, setSearch] = useState('');
  const [library, setLibrary] = useState<{ id: string; name: string; mediaType?: string } | null>(null);
  // An open podcast: its episodes replace the list until Back (BUG-43).
  const [podcast, setPodcast] = useState<{ id: string; title: string } | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const q = search.trim();
  const searching = q.length >= 2;

  // Close on Escape, and start fresh each time it opens.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);
  useEffect(() => {
    if (open) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- reset when closed
    setSearch(''); setLibrary(null); setPodcast(null); setTab('ma'); setBusyKey(null);
  }, [open]);

  const maSearch = useQuery({
    queryKey: ['ma-search', q],
    queryFn: () => api.searchMusicAssistant(q, undefined, 30, false),
    enabled: open && tab === 'ma' && searching,
    staleTime: 60_000,
  });
  const maPlaylists = useQuery({
    queryKey: ['ma-playlists'],
    queryFn: () => api.getMusicAssistantPlaylists(),
    enabled: open && tab === 'ma' && !searching,
    staleTime: 60_000,
  });
  const maRecent = useQuery({
    queryKey: ['ma-recent'],
    queryFn: () => api.getMusicAssistantRecent(),
    enabled: open && tab === 'ma' && !searching,
    staleTime: 60_000,
  });
  const absLibraries = useQuery({
    queryKey: ['abs-libraries'],
    queryFn: () => api.getAudiobookshelfLibraries(),
    enabled: open && tab === 'abs' && !library,
    staleTime: 60_000,
  });
  const absBooks = useQuery({
    queryKey: ['abs-library-items', library?.id],
    queryFn: () => api.getAudiobookshelfLibrary(library!.id, 200),
    enabled: open && tab === 'abs' && !!library,
    staleTime: 60_000,
  });
  const absSearch = useQuery({
    queryKey: ['abs-search', q],
    queryFn: () => api.searchAudiobookshelf(q, 30),
    enabled: open && tab === 'abs' && searching && !library,
    staleTime: 60_000,
  });

  const episodes = useQuery({
    queryKey: ['podcast-episodes', podcast?.id],
    queryFn: () => api.getPodcastEpisodes(podcast!.id, podcast!.title),
    enabled: open && tab === 'abs' && !!podcast,
    staleTime: 5 * 60_000,
  });

  if (!open) return null;

  const play = (req: PlayRequest, key: string) => {
    setBusyKey(key);
    void onPlay(req, key).finally(() => setBusyKey(null));
  };
  const queue = (req: PlayRequest, key: string, option: QueueOption) => {
    setBusyKey(`${key}:${option}`);
    void onQueue!(req, option).finally(() => setBusyKey(null));
  };
  const queueButton = (req: PlayRequest, key: string, option: QueueOption) => {
    const label = option === 'next' ? `Play ${req.title} next` : `Add ${req.title} to the queue`;
    return (
      <button
        type="button"
        aria-label={label}
        title={option === 'next' ? 'Play next' : 'Add to queue'}
        disabled={busyKey !== null}
        onClick={() => queue(req, key, option)}
        className="min-h-11 min-w-11 shrink-0 flex items-center justify-center rounded-xl bg-white/5 text-slate-300 hover:bg-white/10 hover:text-white disabled:opacity-50"
      >
        {busyKey === `${key}:${option}`
          ? <Loader2 size={16} className="animate-spin text-cyan-300" />
          : option === 'next' ? <ListStart size={16} /> : <ListEnd size={16} />}
      </button>
    );
  };
  const row = (key: string, req: PlayRequest, icon: ReactNode, subtitle?: string, badge?: string) => (
    <li key={key} className="flex items-center gap-1.5">
      <div className="min-w-0 flex-1">
        <PlayRow
          icon={icon}
          title={req.title}
          subtitle={subtitle}
          badge={badge}
          busy={busyKey === key}
          disabled={busyKey !== null}
          onClick={() => play(req, key)}
        />
      </div>
      {onQueue && req.kind === 'uri' && (
        <>
          {queueButton(req, key, 'next')}
          {queueButton(req, key, 'add')}
        </>
      )}
    </li>
  );
  const podcastRow = (id: string, title: string, subtitle?: string) => (
    <li key={`p-${id}`}>
      <button type="button" onClick={() => { setPodcast({ id, title }); setSearch(''); }} className={ROW_CLASS} data-testid={`podcast-${id}`}>
        <Mic2 size={18} className="text-blue-400 shrink-0" />
        <span className="min-w-0 flex-1">
          <span className="block text-white text-sm font-medium truncate">{title}</span>
          {subtitle ? <span className="block text-xs text-slate-400 truncate">{subtitle}</span> : null}
        </span>
        <span className="text-[11px] px-2 py-0.5 rounded-full bg-white/10 text-slate-300 shrink-0">podcast</span>
        <ChevronRight size={16} className="text-slate-500 shrink-0" />
      </button>
    </li>
  );
  const absNotice = (data?: { notice?: string }) => (data?.notice ? `Audiobookshelf is unavailable (${data.notice}).` : null);

  const tabButton = (id: 'ma' | 'abs', label: string, icon: ReactNode) => (
    <button
      type="button"
      role="tab"
      aria-selected={tab === id}
      onClick={() => { setTab(id); setLibrary(null); setPodcast(null); setSearch(''); }}
      className={`flex-1 min-h-11 py-3 text-sm font-medium flex items-center justify-center gap-2 transition-colors ${
        tab === id ? 'text-cyan-300 border-b-2 border-cyan-400 bg-cyan-500/5' : 'text-slate-400 hover:text-white'
      }`}
    >
      {icon}{label}
    </button>
  );

  const bookFilter = (b: { title: string; author: string }) =>
    !q || b.title.toLowerCase().includes(q.toLowerCase()) || (b.author || '').toLowerCase().includes(q.toLowerCase());

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-end sm:items-center justify-center" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Browse media"
        className="glass-panel w-full sm:max-w-3xl h-[92dvh] sm:h-[85vh] sm:rounded-2xl rounded-t-2xl flex flex-col overflow-hidden border border-white/10 shadow-2xl"
        style={{ paddingBottom: 'env(safe-area-inset-bottom)' }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-2 border-b border-white/10 shrink-0">
          <div className="flex items-center gap-2">
            <Grid3X3 size={18} className="text-cyan-400" />
            <h2 className="text-lg font-semibold text-white">Browse media</h2>
          </div>
          <button type="button" onClick={onClose} className="min-w-11 min-h-11 flex items-center justify-center text-slate-400 hover:text-white rounded-xl hover:bg-white/10" aria-label="Close">
            <X size={20} />
          </button>
        </div>

        <div className="flex border-b border-white/10 shrink-0" role="tablist">
          {tabButton('ma', 'Music', <Music size={16} />)}
          {tabButton('abs', 'Audiobooks', <BookOpen size={16} />)}
        </div>

        <div className="p-3 border-b border-white/5 shrink-0">
          <div className="relative">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input
              type="search"
              placeholder={tab === 'abs' ? (podcast ? 'Filter episodes…' : library ? `Filter ${library.name}…` : 'Search books & podcasts') : 'Search music…'}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full glass-input pl-9 pr-11 h-11 text-base sm:text-sm rounded-xl"
              aria-label="Search"
            />
            {search && (
              <button type="button" onClick={() => setSearch('')} className="absolute right-0 top-0 min-w-11 h-11 flex items-center justify-center text-slate-400 hover:text-white" aria-label="Clear search">
                <X size={16} />
              </button>
            )}
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-4 custom-scrollbar space-y-6">
          {tab === 'ma' && searching && (
            <section>
              <SectionTitle icon={<Search size={12} />}>Results</SectionTitle>
              {maSearch.isLoading ? <Loading /> : maSearch.isError ? (
                <Failed message={`Music search failed: ${errorText(maSearch.error, 'Music Assistant is unreachable')}`} onRetry={() => void maSearch.refetch()} />
              ) : !maSearch.data?.results?.length ? <Empty>No results for “{q}”</Empty> : (
                <ul className="space-y-1.5">
                  {maSearch.data.results.map((item) =>
                    row(`s-${item.uri}`, { kind: 'uri', uri: item.uri, title: item.name },
                      <Music size={18} className="text-purple-400" />,
                      [item.artist, item.album].filter(Boolean).join(' • ') || undefined, item.type),
                  )}
                </ul>
              )}
            </section>
          )}

          {tab === 'ma' && !searching && (
            <>
              <section>
                <SectionTitle icon={<List size={12} />}>Playlists</SectionTitle>
                {maPlaylists.isLoading ? <Loading /> : maPlaylists.isError ? (
                  <Failed message="Could not load playlists from Music Assistant." onRetry={() => void maPlaylists.refetch()} />
                ) : !maPlaylists.data?.playlists?.length ? <Empty>No playlists yet</Empty> : (
                  <ul className="space-y-1.5">
                    {maPlaylists.data.playlists.map((pl) =>
                      row(`pl-${pl.uri}`, { kind: 'uri', uri: pl.uri, title: pl.name },
                        <List size={18} className="text-cyan-400" />, pl.items > 0 ? `${pl.items} tracks` : undefined),
                    )}
                  </ul>
                )}
              </section>
              <section>
                <SectionTitle icon={<Clock size={12} />}>Recently played</SectionTitle>
                {maRecent.isLoading ? <Loading /> : maRecent.isError ? (
                  <Failed message="Could not load recently played items." onRetry={() => void maRecent.refetch()} />
                ) : !maRecent.data?.recent?.length ? <Empty>Nothing played yet</Empty> : (
                  <ul className="space-y-1.5">
                    {maRecent.data.recent.map((item) =>
                      row(`r-${item.uri}`, { kind: 'uri', uri: item.uri, title: item.name },
                        item.type === 'audiobook' ? <BookOpen size={18} className="text-amber-400" /> : <Music size={18} className="text-purple-400" />,
                        item.artist || undefined, item.type),
                    )}
                  </ul>
                )}
              </section>
            </>
          )}

          {tab === 'abs' && podcast && (
            <section>
              <button type="button" onClick={() => setPodcast(null)} className="min-h-11 text-sm text-amber-300 hover:text-amber-200 mb-1 flex items-center gap-1">
                <ChevronLeft size={16} />Back
              </button>
              <SectionTitle icon={<Mic2 size={12} />}>{podcast.title}</SectionTitle>
              {episodes.isLoading ? <Loading /> : episodes.isError ? (
                <Failed message={`Could not load episodes: ${errorText(episodes.error, 'Music Assistant is unreachable')}`} onRetry={() => void episodes.refetch()} />
              ) : !episodes.data?.episodes?.length ? <Empty>No episodes yet</Empty> : (
                <ul className="space-y-1.5" aria-label="Episodes">
                  {episodes.data.episodes.filter((e) => !q || e.name.toLowerCase().includes(q.toLowerCase())).map((e) =>
                    row(`e-${e.uri}`, { kind: 'uri', uri: e.uri, title: e.name },
                      <Mic2 size={18} className="text-blue-400" />,
                      episodeSubtitle(e), e.fully_played ? 'played' : undefined),
                  )}
                </ul>
              )}
            </section>
          )}

          {tab === 'abs' && !podcast && !library && searching && (
            <section>
              <SectionTitle icon={<Search size={12} />}>Results</SectionTitle>
              {absSearch.isLoading ? <Loading /> : absSearch.isError ? (
                <Failed message={`Audiobook search failed: ${errorText(absSearch.error, 'Audiobookshelf is unreachable')}`} onRetry={() => void absSearch.refetch()} />
              ) : !absSearch.data?.books?.length && !absSearch.data?.podcasts?.length && !absSearch.data?.authors?.length ? (
                <Empty>No results for “{q}”</Empty>
              ) : (
                <ul className="space-y-1.5">
                  {absSearch.data!.books.map((b) =>
                    row(`b-${b.id}`, { kind: 'abs', id: b.id, title: b.title },
                      <BookOpen size={18} className="text-amber-400" />,
                      [b.author || b.narrator, b.duration_formatted].filter(Boolean).join(' • ') || undefined, 'book'),
                  )}
                  {absSearch.data!.podcasts.map((p) => podcastRow(p.id, p.title, p.author || undefined))}
                  {absSearch.data!.authors.map((a) => (
                    <li key={`a-${a.id}`}>
                      <button type="button" onClick={() => setSearch(a.name)} className={ROW_CLASS}>
                        <Users size={18} className="text-emerald-400 shrink-0" />
                        <span className="min-w-0 flex-1">
                          <span className="block text-white text-sm font-medium truncate">{a.name}</span>
                          <span className="block text-xs text-slate-400">Show their books</span>
                        </span>
                        <span className="text-[11px] px-2 py-0.5 rounded-full bg-white/10 text-slate-300 shrink-0">author</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )}

          {tab === 'abs' && !podcast && !library && !searching && (
            <section>
              <SectionTitle icon={<Library size={12} />}>Libraries</SectionTitle>
              {absLibraries.isLoading ? <Loading /> : absLibraries.isError || absNotice(absLibraries.data) ? (
                <Failed message={absNotice(absLibraries.data) || 'Could not load Audiobookshelf libraries.'} onRetry={() => void absLibraries.refetch()} />
              ) : !absLibraries.data?.libraries?.length ? <Empty>No libraries found</Empty> : (
                <ul className="space-y-1.5">
                  {absLibraries.data.libraries.map((lib) => (
                    <li key={lib.id}>
                      <button type="button" onClick={() => { setLibrary({ id: lib.id, name: lib.name, mediaType: lib.media_type }); setSearch(''); }} className={ROW_CLASS}>
                        <Library size={18} className="text-amber-400 shrink-0" />
                        <span className="min-w-0 flex-1">
                          <span className="block text-white text-sm font-medium truncate">{lib.name}</span>
                          <span className="block text-xs text-slate-400 capitalize">{lib.media_type}</span>
                        </span>
                        <ChevronRight size={16} className="text-slate-500 shrink-0" />
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )}

          {tab === 'abs' && !podcast && library && (
            <section>
              <button type="button" onClick={() => { setLibrary(null); setSearch(''); }} className="min-h-11 text-sm text-amber-300 hover:text-amber-200 mb-1 flex items-center gap-1">
                <ChevronLeft size={16} />All libraries
              </button>
              <SectionTitle icon={<BookOpen size={12} />}>{library.name}</SectionTitle>
              {absBooks.isLoading ? <Loading /> : absBooks.isError || absNotice(absBooks.data) ? (
                <Failed message={absNotice(absBooks.data) || 'Could not load this library.'} onRetry={() => void absBooks.refetch()} />
              ) : !absBooks.data?.books?.length ? <Empty>This library is empty</Empty> : (
                <ul className="space-y-1.5">
                  {absBooks.data.books.filter(bookFilter).map((b) =>
                    library.mediaType === 'podcast'
                      ? podcastRow(b.id, b.title, b.author || undefined)
                      : row(`b-${b.id}`, { kind: 'abs', id: b.id, title: b.title },
                        <BookOpen size={18} className="text-amber-400" />, b.author || undefined),
                  )}
                </ul>
              )}
            </section>
          )}
        </div>
      </div>
    </div>
  );
}
