import { useCallback, useEffect, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { BookOpen, Flame, Search, Sparkles, Trophy, X } from 'lucide-react';
import { api } from '../services/api';
import { useHaptics } from '../hooks/useHaptics';
import type {
  BibleBookInfo,
  BibleMark,
  BiblePosition,
  BibleEditionInfo,
  BiblePreferences,
  BibleStateResponse,
  BibleVerse,
  BibleVersionInfo,
} from '../types/api';
import ReferenceBar from '../components/bible/ReferenceBar';
import ChapterReader from '../components/bible/ChapterReader';
import VerseActionSheet from '../components/bible/VerseActionSheet';
import BibleReadAloud from '../components/bible/BibleReadAloud';
import ChapterNotes from '../components/bible/ChapterNotes';
import BibleToday from '../components/bible/BibleToday';
import BibleProgress from '../components/bible/BibleProgress';

type Tab = 'read' | 'today' | 'progress';

const TABS: Array<{ id: Tab; label: string; icon: typeof BookOpen }> = [
  { id: 'read', label: 'Read', icon: BookOpen },
  { id: 'today', label: 'Today', icon: Sparkles },
  { id: 'progress', label: 'Progress', icon: Trophy },
];

const DEFAULT_PREFERENCES: BiblePreferences = {
  default_version: '',
  default_edition: '',
  favorite_version: '',
  compare_version: '',
  cross_version_notes: false,
  show_notes: false,
  font_scale: 1,
  line_height: 1.6,
  theme: 'serif',
  read_aloud_voice: '',
  split_view: 'compare',
};

const UPCOMING = [
  { name: 'Reading plans', detail: 'Daily readings with friends, the way the Bible App does it' },
  { name: 'Jarvis study help', detail: 'Ask a question about the passage you are reading' },
  { name: 'Memorisation', detail: 'Fill-in-the-blank drills with spaced review' },
  { name: 'Quizzes and games', detail: 'Trivia and memory verses for the whole family' },
];

/** Something with digits in it is a passage; anything else is a search. */
function looksLikeReference(value: string): boolean {
  return /\d/.test(value);
}

function chapterRef(position: BiblePosition): string {
  return `${position.book} ${position.chapter}`;
}

/**
 * The reading surface: passage box, chapter controls, search results and the
 * per-verse actions.
 *
 * Split out of the page so its state can be *initialised* from the saved
 * reading position rather than corrected into place by an effect afterwards.
 * A restore effect means the first render shows the wrong chapter and then
 * swaps it, which is exactly what makes a reader tap the wrong thing.
 */
function BibleReaderPane({
  state,
  requestedRef,
  onOpenRef,
  onRequestRef,
  marks,
  onMarksChanged,
  preferences,
  onPreferences,
  versions,
  editions,
  version,
  books,
}: {
  state: BibleStateResponse;
  requestedRef: string | null;
  onOpenRef: (ref: string) => void;
  onRequestRef: (ref: string) => void;
  marks: BibleMark[];
  onMarksChanged: () => Promise<void>;
  preferences: BiblePreferences;
  onPreferences: (patch: Partial<BiblePreferences>) => void;
  versions: BibleVersionInfo[];
  editions: BibleEditionInfo[];
  version: string;
  books: BibleBookInfo[];
}) {
  const { trigger } = useHaptics();
  const [position, setPosition] = useState<BiblePosition>(
    requestedRef ? { book: 'Gen', chapter: 1, verse: 1 } : state.position ?? { book: 'Gen', chapter: 1, verse: 1 }
  );
  const [input, setInput] = useState(requestedRef ?? chapterRef(position));
  const [searchTerm, setSearchTerm] = useState<string | null>(null);
  const [activeVerse, setActiveVerse] = useState<BibleVerse | null>(null);

  const requestRef = searchTerm ?? requestedRef ?? chapterRef(position);
  const searching = searchTerm !== null;

  const { data: passage, isFetching, error, refetch } = useQuery({
    queryKey: ['bible-passage', version, requestRef],
    queryFn: () => api.getBiblePassage(requestRef, version),
    enabled: Boolean(version) && !searching,
    retry: 0,
  });

  const { data: searchData, isFetching: searchLoading } = useQuery({
    queryKey: ['bible-search', searchTerm, version],
    queryFn: () => api.searchBible(searchTerm ?? '', { version, limit: 40 }),
    enabled: searching && (searchTerm?.length ?? 0) >= 2,
    retry: 0,
  });

  // The second translation is only fetched when the reader asked to compare, and
  // when it fails it reads as a comparison that could not load rather than as a
  // missing chapter: the primary passage is still on screen.
  const compareVersion = preferences.compare_version;
  const { data: compareData } = useQuery({
    queryKey: ['bible-passage', compareVersion, requestRef],
    queryFn: () => api.getBiblePassage(requestRef, compareVersion),
    enabled: Boolean(version) && Boolean(compareVersion) && !searching,
    retry: 0,
  });
  const compareName = versions.find((v) => v.code === compareVersion)?.name ?? compareVersion;

  // Position and chapter-complete both follow the chapter that was actually
  // shown, which is the requested ref when one came in from a link. The
  // displayed position is derived rather than copied into state, so a deep link
  // shows the right chapter on the very first paint instead of after an effect.
  const shownChapter = passage?.spans[0];
  const studyScope =
    shownChapter && !shownChapter.whole_book
      ? `${shownChapter.book_name} ${shownChapter.chapter_start}`
      : null;
  const edition = preferences.default_edition || undefined;
  const { data: studyData, isFetching: studyLoading } = useQuery({
    queryKey: ['bible-study-index', studyScope, version, edition ?? '', preferences.cross_version_notes],
    queryFn: () =>
      api.getBibleStudyNotes(studyScope as string, version, {
        edition,
        crossVersion: preferences.cross_version_notes,
      }),
    enabled: Boolean(studyScope) && Boolean(version),
    retry: false,
    staleTime: 10 * 60 * 1000,
  });
  // Which verses of the chapter carry study material. Loaded once per chapter so
  // the reader can show it, then the notes themselves are fetched only on demand.
  // Commentary sits beside the text rather than behind a tap when the reader asks
  // for it, and only for a real chapter: a whole-book span has no verses to
  // attach a note to, so an empty column there would be noise.
  const notesAlongside = preferences.show_notes && Boolean(studyScope);
  const noteUnavailable =
    studyData && !studyData.notes.length ? (studyData.note ?? null) : null;
  // Which verses the reader has written about. The note itself is in Nextcloud;
  // this only marks that one exists, so the glyph is a pointer to something real
  // rather than a copy of it.
  const notedVerses = useMemo(
    () =>
      new Set(
        marks
          .filter((m) => m.kind === 'note' && m.note_path)
          .map((m) => `${m.chapter}:${m.verse_start}`),
      ),
    [marks],
  );
  const studiedVerses = useMemo(
    () =>
      new Set(
        (studyData?.notes ?? []).map(
          (n) => `${n.chapter}:${n.verse}`,
        ),
      ),
    [studyData],
  );
  const displayPosition: BiblePosition = shownChapter && !shownChapter.whole_book
    ? { book: shownChapter.book, chapter: shownChapter.chapter_start, verse: position.verse }
    : position;
  useEffect(() => {
    if (!passage || !shownChapter || shownChapter.whole_book) return;
    void api.putBibleState({
      book: shownChapter.book,
      chapter: shownChapter.chapter_start,
      verse: position.verse,
    });
    void api.recordBibleEvent(
      'chapter_complete',
      `${shownChapter.book_name} ${shownChapter.chapter_start}`
    );
    // `position.verse` is intentionally not a dependency: it changes on every
    // verse tap, and the chapter is what this records.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [passage, shownChapter?.book, shownChapter?.chapter_start]);

  const submit = () => {
    void trigger('light');
    const value = input.trim();
    if (!value) return;
    if (looksLikeReference(value)) {
      setSearchTerm(null);
      onRequestRef(value);
      return;
    }
    setSearchTerm(value);
  };

  /**
   * Next/Previous reads as "the next chapter", so it steps the chapter and only
   * rolls into the neighbouring book at the edges -- the same thing every printed
   * Bible does, and the same thing a thumb expects at the end of a short book.
   */
  const step = (delta: number) => {
    const index = books.findIndex((b) => b.osis === displayPosition.book);
    const current = books[index];
    const nextChapter = displayPosition.chapter + delta;
    if (current && nextChapter >= 1 && nextChapter <= current.chapters) {
      applyPosition({ chapter: nextChapter });
      return;
    }
    const target = books[index + delta];
    if (!target) return;
    applyPosition({
      book: target.osis,
      chapter: delta > 0 ? 1 : target.chapters,
    });
  };

  const applyPosition = (patch: Partial<BiblePosition>) => {
    const next = { ...displayPosition, ...patch };
    setSearchTerm(null);
    setPosition(next);
    setInput(chapterRef(next));
    onRequestRef(chapterRef(next));
  };

  const toggleMark = async (kind: 'highlight' | 'bookmark', color: string) => {
    if (!activeVerse) return;
    const ref = activeVerse.reference;
    const existing = marks.find(
      (m) =>
        m.kind === kind &&
        m.chapter === activeVerse.chapter &&
        m.verse_start <= activeVerse.verse &&
        (m.verse_end ?? m.verse_start) >= activeVerse.verse
    );
    if (existing && existing.verse_end === existing.verse_start) {
      await api.deleteBibleMark(existing.id);
    } else {
      await api.putBibleMark({ ref, kind, color, version_code: version });
    }
    await api.recordBibleEvent('verse_tapped', ref);
    await onMarksChanged();
    setActiveVerse(null);
  };

  return (
    <>
      <div className="glass-panel p-4 rounded-2xl border border-white/10">
        <ReferenceBar
          books={books}
          versions={versions}
          editions={editions}
          position={displayPosition}
          preferences={preferences}
          input={input}
          onInput={setInput}
          onSubmit={submit}
          onPosition={applyPosition}
          onPreferences={onPreferences}
          onStep={step}
        />
      </div>

      {searching ? (
        <section className="glass-panel rounded-2xl p-4 border border-white/10" data-testid="bible-search-results">
          <div className="flex items-center gap-2 mb-3">
            <Search size={15} className="text-sky-300 shrink-0" />
            <h2 className="text-sm font-bold text-white truncate">Results for “{searchTerm}”</h2>
            <button
              type="button"
              onClick={() => {
                setSearchTerm(null);
                setInput('');
              }}
              aria-label="Clear search"
              className="ml-auto min-h-11 min-w-11 shrink-0 flex items-center justify-center rounded-xl text-slate-400 hover:text-white"
            >
              <X size={15} />
            </button>
          </div>
          {searchLoading ? (
            <div className="space-y-2">
              {[0, 1, 2].map((i) => (
                <div key={i} className="skeleton h-10 rounded-xl" />
              ))}
            </div>
          ) : !searchData?.results.length ? (
            <p className="text-sm text-slate-400">Nothing found. Try a shorter word, or name a book.</p>
          ) : (
            <ul className="space-y-1.5">
              {searchData.results.map((hit) => (
                <li key={`${hit.osis}-${hit.chapter}-${hit.verse}`}>
                  <button
                    type="button"
                    onClick={() => onOpenRef(hit.reference)}
                    className="w-full text-left rounded-xl px-3 py-2.5 min-h-11 pointer-coarse:min-h-11 hover:bg-white/5 transition-colors"
                  >
                    <span className="block text-xs font-semibold text-amber-300">{hit.reference}</span>
                    <span className="block text-sm text-slate-300 font-serif leading-relaxed line-clamp-2">
                      {hit.text}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      ) : (
        <div className={notesAlongside ? 'lg:grid lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)] lg:gap-4 lg:items-start' : ''}>
          <section className="glass-panel rounded-2xl p-4 sm:p-5 border border-white/10">
            <div className="flex items-baseline justify-between gap-3 mb-3">
              <h2 className="font-serif text-lg text-white truncate" data-testid="bible-chapter-heading">
                {shownChapter ? `${shownChapter.book_name} ${shownChapter.chapter_start}` : 'Reading'}
              </h2>
              <span className="text-[11px] text-slate-500 shrink-0 tabular-nums">{passage?.version}</span>
            </div>
            <ChapterReader
              verses={passage?.verses ?? []}
              marks={marks}
              studiedVerses={studiedVerses}
              notedVerses={notedVerses}
              compareVerses={compareData?.verses}
              compareName={compareName}
              fontScale={preferences.font_scale}
              lineHeight={preferences.line_height}
              theme={preferences.theme}
              loading={isFetching}
              error={error instanceof Error ? error.message : null}
              onVerseTap={setActiveVerse}
              onRetry={() => void refetch()}
            />
          </section>
          {notesAlongside && (
            <div className="mt-4 lg:mt-0">
              <ChapterNotes
                notes={studyData?.notes ?? []}
                editionName={studyData?.edition_name}
                availableKinds={studyData?.available_kinds}
                unavailable={noteUnavailable}
                loading={studyLoading}
                onOpenVerse={onRequestRef}
                onClose={() => onPreferences({ show_notes: false })}
              />
            </div>
          )}
        </div>
      )}

      {passage?.reference && !searching ? (
        <BibleReadAloud
          reference={passage.reference}
          version={version}
          voice={preferences.read_aloud_voice}
          onVoiceChange={(read_aloud_voice) => onPreferences({ read_aloud_voice })}
        />
      ) : null}

      <section className="glass-panel rounded-2xl p-4 border border-white/5">
        <div className="flex items-center gap-2 mb-2">
          <Flame size={14} className="text-orange-400" />
          <h2 className="text-xs font-semibold text-slate-300 uppercase tracking-wider">Coming next</h2>
        </div>
        <ul className="space-y-1.5">
          {UPCOMING.map((item) => (
            <li key={item.name} className="text-xs text-slate-500">
              <span className="text-slate-300 font-medium">{item.name}</span> — {item.detail}
            </li>
          ))}
        </ul>
      </section>

      {activeVerse && (
        <VerseActionSheet
          verse={activeVerse}
          marks={marks}
          version={version}
          edition={preferences.default_edition}
          crossVersion={preferences.cross_version_notes}
          onEditionChange={(code) => onPreferences({ default_edition: code })}
          onCrossVersionChange={(on) => onPreferences({ cross_version_notes: on })}
          onClose={() => setActiveVerse(null)}
          onToggleMark={(kind, color) => void toggleMark(kind, color)}
          onNoteSaved={() => {
            void trigger('success');
            // The sheet stored both the note and the pointer to it; the reader
            // only needs the marks refreshed so the verse shows the glyph.
            void onMarksChanged();
          }}
          onShared={(target) => {
            void trigger('success');
            void api.recordBibleEvent('share_tap', activeVerse.reference, target === 'system' ? 1 : 0);
          }}
        />
      )}
    </>
  );
}

/**
 * The reader. Everything else in Bible study hangs off this screen.
 *
 * Opening the app lands on whatever the reader last had open, and every chapter
 * change is written back to the server so the next device picks up where this
 * one stopped. The passage query is keyed on the requested text rather than a
 * parsed result, so a reference the server cannot parse shows that error
 * instead of silently falling back to the previous chapter.
 */
export default function Bible() {
  const { trigger } = useHaptics();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const [tab, setTab] = useState<Tab>(searchParams.get('tab') === 'devotional' ? 'today' : 'read');
  const [requestedRef, setRequestedRef] = useState<string | null>(searchParams.get('ref'));

  const { data: stateData, isLoading: stateLoading } = useQuery({
    queryKey: ['bible-state'],
    queryFn: () => api.getBibleState(),
  });
  const { data: versionData } = useQuery({ queryKey: ['bible-versions'], queryFn: () => api.getBibleVersions() });
  const { data: markData } = useQuery({ queryKey: ['bible-marks'], queryFn: () => api.getBibleMarks() });

  const versions = useMemo(() => versionData?.versions ?? [], [versionData]);
  const marks = useMemo(() => markData?.marks ?? [], [markData]);

  // Saved preferences are the base and a local change is an overlay, never a
  // replacement: spreading a local copy wholesale would blank every setting the
  // reader had already saved the moment they changed one of them.
  const [preferencePatch, setPreferencePatch] = useState<Partial<BiblePreferences>>({});
  const effectivePreferences = {
    ...DEFAULT_PREFERENCES,
    ...(stateData?.preferences ?? {}),
    ...preferencePatch,
  };

  // The picker shows every translation we know about so a missing one is a
  // visible task rather than a mystery, but only installed ones can be read.
  const installedCodes = useMemo(
    () => new Set(versions.filter((v) => v.installed).map((v) => v.code)),
    [versions]
  );
  const savedVersion = effectivePreferences.default_version;
  const noVersionsMessage =
    versionData?.message ||
    'No Bible text is installed on this server, so there is nothing to read yet. An administrator needs to load a translation in Admin > Bible.';
  const version =
    savedVersion && installedCodes.has(savedVersion)
      ? savedVersion
      : (versions.find((v) => v.installed)?.code ?? '');
  const unavailableVersion = useMemo(
    () => versions.find((v) => v.code === savedVersion && !v.installed),
    [versions, savedVersion]
  );
  const { data: editionData } = useQuery({
    queryKey: ['bible-editions', version],
    queryFn: () => api.getBibleEditions(version || undefined),
    enabled: Boolean(version),
    retry: false,
  });
  const editions = useMemo(() => editionData?.editions ?? [], [editionData]);
  const { data: bookData } = useQuery({
    queryKey: ['bible-books', version],
    queryFn: () => api.getBibleBooks(version),
    enabled: Boolean(version),
  });

  useEffect(() => {
    void api.recordBibleEvent('app_open');
  }, []);

  const openRef = useCallback(
    (ref: string) => {
      void trigger('light');
      setSearchParams({ ref });
      setRequestedRef(ref);
      setTab('read');
    },
    [setSearchParams, trigger]
  );

  const applyPreferences = (patch: Partial<BiblePreferences>) => {
    setPreferencePatch((current) => ({ ...current, ...patch }));
    void api.putBibleState(patch);
  };

  const refreshMarks = () => queryClient.invalidateQueries({ queryKey: ['bible-marks'] });

  return (
    <div className="space-y-4 sm:space-y-5 max-w-4xl mx-auto pb-28 px-1 sm:px-0" data-testid="bible-page">
      <div className="glass-panel p-4 sm:p-5 rounded-2xl border border-white/10">
        <h1 className="text-xl sm:text-2xl font-bold text-slate-100">Bible</h1>
        <p className="text-xs text-slate-400 mt-0.5">
          Read, mark and remember
          {version ? ` — ${version.toUpperCase()}` : ' — no translation loaded yet'}.
        </p>
        <div className="mt-3 sm:mt-4 grid grid-cols-3 gap-2 sm:flex sm:flex-wrap" role="tablist" aria-label="Bible sections">
          {TABS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              role="tab"
              aria-selected={tab === id}
              onClick={() => {
                void trigger('light');
                setTab(id);
              }}
              data-testid={`bible-tab-${id}`}
              className={`glass-button px-3 sm:px-4 py-2.5 text-sm min-h-11 pointer-coarse:min-h-11 justify-center ${tab === id ? 'text-purple-200 border-purple-400/50' : 'text-slate-300'}`}
            >
              <Icon size={15} /> {label}
            </button>
          ))}
        </div>
      </div>

      {tab === 'read' && (
        versions.length === 0 && !versionData ? (
          <div className="glass-panel rounded-2xl p-4 space-y-2">
            <div className="skeleton h-4 w-1/3 rounded" />
            <div className="skeleton h-4 w-full rounded" />
          </div>
        ) : installedCodes.size === 0 ? (
          <p className="glass-panel rounded-2xl p-4 text-sm text-amber-300/90 leading-relaxed" data-testid="bible-no-versions">
            {noVersionsMessage}
          </p>
        ) : stateLoading || !stateData ? (
          <div className="glass-panel rounded-2xl p-4 space-y-2">
            <div className="skeleton h-4 w-1/3 rounded" />
            <div className="skeleton h-4 w-full rounded" />
          </div>
        ) : (
          <div className="space-y-3">
            {unavailableVersion && (
              <div
                className="glass-panel rounded-2xl p-4 space-y-1"
                data-testid="bible-saved-version-missing"
              >
                <p className="text-sm text-amber-300/90 leading-relaxed">
                  Your saved translation, {unavailableVersion.name}, is not on this server,
                  so you are reading {versions.find((v) => v.installed)?.name} instead.
                </p>
                <p className="text-xs text-slate-400 leading-relaxed">
                  {unavailableVersion.note}
                </p>
              </div>
            )}
            <BibleReaderPane
              key={`${stateData.username}-${requestedRef ?? 'saved'}`}
              state={stateData}
              requestedRef={requestedRef}
              onOpenRef={openRef}
              onRequestRef={setRequestedRef}
              marks={marks}
              onMarksChanged={refreshMarks}
              preferences={effectivePreferences}
              onPreferences={applyPreferences}
              versions={versions}
              editions={editions}
              version={version}
              books={bookData?.books ?? []}
            />
          </div>
        )
      )}

      {tab === 'today' && <BibleToday onOpenRef={openRef} />}

      {tab === 'progress' && (
        <BibleProgress
          marks={marks}
          onDeleteMark={async (id) => {
            await api.deleteBibleMark(id);
            await refreshMarks();
          }}
          onOpenRef={openRef}
        />
      )}
    </div>
  );
}