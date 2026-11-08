import { useEffect, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import {
  AlignLeft, ChevronDown, Columns2, Headphones, List, Maximize2, Minus, Plus, Rows2, Search, Star, Type, X,
} from 'lucide-react';
import { useHaptics } from '../../hooks/useHaptics';
import { PHONE_QUERY, useMediaQuery } from '../../hooks/useMediaQuery';
import { PAGE_TONES, PAGE_TONE_ORDER, TEXT_STEPS, toneOf } from './readingPage';
import type {
  BibleBookInfo,
  BibleEditionInfo,
  BiblePreferences,
  BiblePosition,
  BibleVersionInfo,
} from '../../types/api';

interface ReferenceBarProps {
  books: BibleBookInfo[];
  versions: BibleVersionInfo[];
  /** Study Bibles installed for the chosen translation; more than one is normal. */
  editions?: BibleEditionInfo[];
  /** Now reading - the book, chapter and the verse the reader is on. */
  position: BiblePosition;
  preferences: BiblePreferences;
  /** Text the reader typed, kept here so a failed parse does not lose it. */
  input: string;
  onInput: (value: string) => void;
  onSubmit: () => void;
  onPosition: (patch: Partial<BiblePosition>) => void;
  onPreferences: (patch: Partial<BiblePreferences>) => void;
  /** Kept for callers that still pass it; chapters are turned by the reader's own arrows. */
  onStep?: (delta: number) => void;
  /** Open the chapter without any controls (Focus). */
  onFocus?: () => void;
  /** Whether the read-aloud player is showing, and how to show or hide it. */
  listening?: boolean;
  onListen?: () => void;
}

type Panel = 'passage' | 'version' | 'compare' | 'display';

/**
 * How the comparison is laid out: a row per verse, or two whole chapters in
 * panes that scroll together (`split_view`, saved with the reader's state).
 */
const SPLIT_VIEWS: Array<{ id: BiblePreferences['split_view']; label: string; icon: typeof Rows2 }> = [
  { id: 'compare', label: 'Rows', icon: Rows2 },
  { id: 'parallel', label: 'Panes', icon: Columns2 },
];

const PILL =
  'min-h-11 shrink-0 inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-white/[0.06] px-3.5 text-sm font-semibold text-slate-100 hover:bg-white/10 aria-expanded:bg-white/15 aria-expanded:border-white/25';
// No display utility here: each icon says whether it shows (`inline-flex`) or
// only from a tablet up (`hidden sm:inline-flex`), and two display classes on
// one element let the stylesheet's order decide instead of the breakpoint.
const ICON =
  'min-h-11 min-w-11 shrink-0 items-center justify-center rounded-full text-slate-300 hover:bg-white/10 hover:text-white aria-pressed:bg-amber-500/15 aria-pressed:text-amber-200 aria-expanded:bg-white/15 aria-expanded:text-white';
const FIELD = 'min-h-11 w-full min-w-0 rounded-xl bg-black/30 border border-white/10 text-sm text-slate-100 px-3 py-2';
const LABEL = 'text-[10px] uppercase tracking-wider text-slate-500';

/** Old Testament books end at Malachi (39) in every canon this reader carries. */
function testaments(books: BibleBookInfo[]) {
  const ordered = [...books].sort((a, b) => a.order - b.order);
  return [
    { name: 'Old Testament', books: ordered.filter((b) => b.order <= 39) },
    { name: 'New Testament', books: ordered.filter((b) => b.order > 39) },
  ];
}

/**
 * The reader's header, after the Bible App: the passage ("John 3") and the
 * translation ("NKJV") as two pills, then listen, compare, text settings and
 * focus as icons. Each opens a panel -- a dropdown under the header on a
 * larger screen, a sheet from the bottom on a phone -- so the text keeps the
 * whole page and nothing ever spills off a narrow screen.
 *
 * The passage panel works the way the Bible App's does: type a reference or a
 * word, or pick a book and then a chapter from a grid. Every control is a
 * thumb-sized target (`min-h-11`), and nothing is hover-only.
 */
export default function ReferenceBar({
  books,
  versions,
  editions = [],
  position,
  preferences,
  input,
  onInput,
  onSubmit,
  onPosition,
  onPreferences,
  onFocus,
  listening = false,
  onListen,
}: ReferenceBarProps) {
  const { trigger } = useHaptics();
  const isPhone = useMediaQuery(PHONE_QUERY);
  const [panel, setPanel] = useState<Panel | null>(null);
  const [openBook, setOpenBook] = useState<string | null>(null);

  const book = books.find((b) => b.osis === position.book) ?? books[0];
  const selectedVersion = versions.find((v) => v.code === preferences.default_version);
  const favorite = versions.find((v) => v.code === preferences.favorite_version);
  const isFavorite = Boolean(favorite) && favorite?.code === preferences.default_version;
  const installedEditions = editions.filter((entry) => entry.installed);
  const selectedEdition = installedEditions.some((entry) => entry.code === preferences.default_edition)
    ? preferences.default_edition
    : (installedEditions[0]?.code ?? '');
  const selectedEditionEntry = installedEditions.find((entry) => entry.code === selectedEdition);
  const chapterCount = book?.chapters ?? 1;
  const textIndex = Math.max(0, TEXT_STEPS.indexOf(preferences.font_scale));
  const comparing = Boolean(preferences.compare_version);
  const comparableVersions = versions.filter((v) => v.installed && v.code !== preferences.default_version);
  const passageLabel = `${book?.name ?? position.book} ${position.chapter}:${position.verse}`;
  const versionLabel = (preferences.default_version || '').toUpperCase() || 'Version';
  const layout = preferences.layout ?? 'flow';

  const open = (next: Panel) => {
    void trigger('light');
    setPanel((current) => (current === next ? null : next));
    if (next === 'passage') setOpenBook(position.book);
  };
  const close = () => setPanel(null);

  // Escape closes whatever panel is open.
  useEffect(() => {
    if (!panel) return undefined;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setPanel(null);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [panel]);

  // The chooser opens on the book in hand, its chapters showing, rather than
  // at Genesis with forty books to scroll past.
  useEffect(() => {
    if (panel !== 'passage') return;
    const row = document.querySelector(`[data-testid="bible-pick-${CSS.escape(position.book)}"]`);
    row?.scrollIntoView?.({ block: 'center' });
  }, [panel, position.book]);

  const choosePosition = (patch: Partial<BiblePosition>) => {
    onPosition(patch);
    close();
  };

  const bumpFont = (direction: 1 | -1) => {
    void trigger('light');
    const next = Math.min(TEXT_STEPS.length - 1, Math.max(0, textIndex + direction));
    onPreferences({ font_scale: TEXT_STEPS[next] });
  };

  // Typing a book name narrows the list, as the Bible App's search box does;
  // a reference ("John 3:16") or a word to search leaves it whole.
  const typed = input.trim().toLowerCase();
  const filtering = typed.length > 0 && !/\d/.test(typed) && books.some((b) => b.name.toLowerCase().startsWith(typed));
  const shownBooks = (list: BibleBookInfo[]) =>
    filtering ? list.filter((b) => b.name.toLowerCase().startsWith(typed) || b.osis.toLowerCase().startsWith(typed)) : list;

  const passagePanel = (
    <div className="space-y-4">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          onSubmit();
          close();
        }}
        className="flex gap-2"
        role="search"
      >
        <label className="relative min-w-0 flex-1">
          <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" aria-hidden />
          <input
            type="text"
            value={input}
            onChange={(e) => onInput(e.target.value)}
            placeholder="John 3:16, Psalm 23, or a word"
            aria-label="Passage or search"
            data-testid="bible-ref-input"
            enterKeyHint="go"
            className={`${FIELD} pl-9 text-base sm:text-sm`}
          />
        </label>
        <button type="submit" data-testid="bible-ref-go" className="glass-button min-h-11 shrink-0 px-4 text-sm font-semibold">
          Go
        </button>
      </form>

      <div className="grid grid-cols-[minmax(0,1fr)_auto] items-end gap-2">
        <label className="flex min-w-0 flex-col gap-1">
          <span className={LABEL}>Book</span>
          <select
            value={position.book}
            onChange={(e) => choosePosition({ book: e.target.value, chapter: 1, verse: 1 })}
            aria-label="Book"
            data-testid="bible-book-select"
            className={FIELD}
          >
            {books.map((b) => (
              <option key={b.osis} value={b.osis}>
                {b.name}
              </option>
            ))}
          </select>
        </label>
        <div className="flex flex-col gap-1">
          <span className={LABEL}>Chapter</span>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => choosePosition({ chapter: Math.max(1, position.chapter - 1) })}
              disabled={position.chapter <= 1}
              aria-label="Previous chapter"
              data-testid="bible-chapter-down"
              className="glass-button flex min-h-11 min-w-11 items-center justify-center rounded-xl disabled:opacity-30"
            >
              <Minus size={14} />
            </button>
            <input
              type="number"
              inputMode="numeric"
              min={1}
              max={chapterCount}
              value={position.chapter}
              onChange={(e) => {
                const value = Number(e.target.value);
                if (Number.isFinite(value) && value >= 1) {
                  choosePosition({ chapter: Math.min(value, chapterCount), verse: 1 });
                }
              }}
              aria-label="Chapter number"
              data-testid="bible-chapter-input"
              className={`${FIELD} w-16 px-1 text-center`}
            />
            <button
              type="button"
              onClick={() => choosePosition({ chapter: Math.min(chapterCount, position.chapter + 1) })}
              disabled={position.chapter >= chapterCount}
              aria-label="Next chapter"
              data-testid="bible-chapter-up"
              className="glass-button flex min-h-11 min-w-11 items-center justify-center rounded-xl disabled:opacity-30"
            >
              <Plus size={14} />
            </button>
          </div>
        </div>
      </div>

      <div className="space-y-4" data-testid="bible-book-list">
        {testaments(books).map((t) => {
          const list = shownBooks(t.books);
          if (!list.length) return null;
          return (
            <section key={t.name}>
              <h3 className={`${LABEL} mb-1`}>{t.name}</h3>
              <ul className="divide-y divide-white/5">
                {list.map((b) => {
                  const expanded = openBook === b.osis || (filtering && list.length === 1);
                  return (
                    <li key={b.osis}>
                      <button
                        type="button"
                        onClick={() => (b.chapters === 1 ? choosePosition({ book: b.osis, chapter: 1, verse: 1 }) : setOpenBook(expanded ? null : b.osis))}
                        aria-expanded={b.chapters === 1 ? undefined : expanded}
                        data-testid={`bible-pick-${b.osis}`}
                        className={`flex min-h-11 w-full items-center justify-between gap-2 px-1 text-left text-sm ${
                          b.osis === position.book ? 'font-semibold text-amber-200' : 'text-slate-200'
                        }`}
                      >
                        <span className="truncate">{b.name}</span>
                        {b.chapters > 1 && <ChevronDown size={15} className={`shrink-0 text-slate-500 transition ${expanded ? 'rotate-180' : ''}`} aria-hidden />}
                      </button>
                      {expanded && (
                        <div className="grid grid-cols-5 gap-1.5 pb-3 pt-1 sm:grid-cols-8" data-testid="bible-chapter-grid">
                          {Array.from({ length: b.chapters }, (_, i) => i + 1).map((n) => {
                            const current = b.osis === position.book && n === position.chapter;
                            return (
                              <button
                                key={n}
                                type="button"
                                onClick={() => choosePosition({ book: b.osis, chapter: n, verse: 1 })}
                                aria-label={`${b.name} ${n}`}
                                aria-current={current || undefined}
                                data-testid={`bible-pick-${b.osis}-${n}`}
                                className={`aspect-square min-h-11 rounded-lg text-sm font-semibold tabular-nums ${
                                  current ? 'bg-slate-100 text-slate-900' : 'bg-white/[0.07] text-slate-100 hover:bg-white/15'
                                }`}
                              >
                                {n}
                              </button>
                            );
                          })}
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
            </section>
          );
        })}
      </div>
    </div>
  );

  const versionPanel = (
    <div className="space-y-4" data-testid="bible-version-panel">
      <label className="flex flex-col gap-1">
        <span className={LABEL}>Translation</span>
        <select
          value={preferences.default_version}
          onChange={(e) => onPreferences({ default_version: e.target.value })}
          aria-label="Translation"
          data-testid="bible-version-select"
          className={FIELD}
        >
          {versions.map((v) => (
            <option key={v.code} value={v.code}>
              {v.name}
              {v.installed ? '' : ' (not on this server)'}
            </option>
          ))}
        </select>
      </label>
      {selectedVersion && !selectedVersion.installed && (
        <p data-testid="bible-version-unavailable" className="text-[11px] leading-snug text-amber-300/90">
          {selectedVersion.note}
        </p>
      )}
      {selectedVersion?.installed && selectedVersion.license_class === 'licensed' && (
        <p data-testid="bible-version-licensed" className="text-[11px] leading-snug text-slate-400">
          {selectedVersion.note || 'Licensed text. Do not redistribute.'}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => onPreferences({ favorite_version: isFavorite ? '' : (preferences.default_version ?? '') })}
          aria-pressed={isFavorite}
          aria-label={isFavorite ? 'Remove this translation from favourites' : 'Make this translation a favourite'}
          data-testid="bible-favorite-toggle"
          className="flex min-h-11 items-center gap-1.5 rounded-xl border border-white/10 bg-black/25 px-3 text-xs text-slate-200"
        >
          <Star size={14} className={isFavorite ? 'fill-amber-300 text-amber-300' : 'text-slate-400'} />
          {isFavorite ? 'Favourite' : 'Make favourite'}
        </button>
        {favorite && !isFavorite && favorite.installed && (
          <button
            type="button"
            onClick={() => onPreferences({ default_version: favorite.code })}
            data-testid="bible-favorite-switch"
            className="flex min-h-11 items-center gap-1.5 rounded-xl border border-amber-400/30 bg-black/25 px-3 text-xs text-amber-200"
          >
            <Star size={14} className="fill-amber-300 text-amber-300" />
            Back to {favorite.name}
          </button>
        )}
      </div>
      {favorite && isFavorite && (
        <p data-testid="bible-favorite-note" className="text-[11px] leading-snug text-slate-400">
          {favorite.name} is your favourite.
        </p>
      )}
      {installedEditions.length > 0 && (
        <label className="flex flex-col gap-1">
          <span className={LABEL}>Study Bible</span>
          <select
            value={selectedEdition}
            onChange={(e) => onPreferences({ default_edition: e.target.value })}
            aria-label="Study Bible"
            data-testid="bible-edition-select"
            className={FIELD}
          >
            {installedEditions.map((entry) => (
              <option key={entry.code} value={entry.code}>
                {entry.name}
                {entry.note_count ? ` · ${entry.note_count.toLocaleString()} notes` : ' · no notes'}
              </option>
            ))}
          </select>
          {selectedEditionEntry?.license_class === 'licensed' && (
            <p data-testid="bible-edition-licensed" className="text-[11px] leading-snug text-slate-400">
              {selectedEditionEntry.note || 'Licensed notes. Do not redistribute.'}
            </p>
          )}
        </label>
      )}
    </div>
  );

  const comparePanel = (
    <div className="space-y-3" data-testid="bible-compare-panel">
      <button
        type="button"
        onClick={() => {
          void trigger('light');
          onPreferences({ compare_version: comparing ? '' : (comparableVersions[0]?.code ?? '') });
        }}
        disabled={!comparableVersions.length}
        aria-pressed={comparing}
        aria-label={comparing ? 'Stop comparing translations' : 'Compare with another translation'}
        data-testid="bible-compare-toggle"
        className={`flex min-h-11 w-full items-center justify-center gap-2 rounded-xl border px-3 text-sm disabled:opacity-40 ${
          comparing ? 'border-amber-400/50 bg-amber-500/10 text-amber-200' : 'border-white/10 bg-black/25 text-slate-200'
        }`}
      >
        <Columns2 size={14} />
        {comparing ? 'Comparing: turn off' : 'Compare with another translation'}
      </button>
      {!comparableVersions.length && <p className="text-[11px] text-slate-500">Only one translation is installed.</p>}
      {comparing && (
        <>
          <label className="flex flex-col gap-1">
            <span className={LABEL}>Alongside</span>
            <select
              value={preferences.compare_version}
              onChange={(e) => onPreferences({ compare_version: e.target.value })}
              aria-label="Translation to compare with"
              data-testid="bible-compare-select"
              className={FIELD}
            >
              {comparableVersions.map((v) => (
                <option key={v.code} value={v.code}>
                  {v.name}
                </option>
              ))}
            </select>
          </label>
          <div role="group" aria-label="How to lay out the comparison" className="flex gap-1 rounded-xl bg-black/25 p-0.5">
            {SPLIT_VIEWS.map(({ id, label, icon: Icon }) => (
              <button
                key={id}
                type="button"
                onClick={() => {
                  void trigger('light');
                  onPreferences({ split_view: id });
                }}
                aria-pressed={(preferences.split_view ?? 'compare') === id}
                data-testid={`bible-split-view-${id}`}
                className={`flex min-h-11 flex-1 items-center justify-center gap-1 rounded-lg px-2 text-[12px] ${
                  (preferences.split_view ?? 'compare') === id ? 'bg-amber-500/15 text-amber-200' : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                <Icon size={13} />
                {label}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );

  const segment = (on: boolean) =>
    `min-h-11 flex-1 rounded-lg px-2 text-sm ${on ? 'bg-slate-100 text-slate-900 font-semibold' : 'text-slate-300 hover:bg-white/5'}`;

  const displayPanel = (
    <div className="space-y-4" data-testid="bible-display-panel">
      <div className="flex items-center justify-between gap-3">
        <span className="text-xs text-slate-400">Text size</span>
        <div className="flex items-center gap-1 rounded-xl bg-black/30 p-0.5">
          <button type="button" onClick={() => bumpFont(-1)} disabled={textIndex === 0} aria-label="Smaller text" className="flex min-h-11 min-w-11 items-center justify-center rounded-lg text-xs text-slate-200 disabled:opacity-30">
            A
          </button>
          <span className="w-14 text-center text-xs text-slate-300 tabular-nums" data-testid="bible-font-scale">
            {preferences.font_scale.toFixed(2)}x
          </span>
          <button type="button" onClick={() => bumpFont(1)} disabled={textIndex === TEXT_STEPS.length - 1} aria-label="Larger text" className="flex min-h-11 min-w-11 items-center justify-center rounded-lg text-lg text-slate-200 disabled:opacity-30">
            A
          </button>
        </div>
      </div>

      <div className="flex items-center justify-between gap-3">
        <span className="text-xs text-slate-400">Line spacing</span>
        <div className="flex items-center gap-1 rounded-xl bg-black/30 p-0.5">
          <button
            type="button"
            onClick={() => onPreferences({ line_height: Math.max(1, preferences.line_height - 0.2) })}
            disabled={preferences.line_height <= 1}
            aria-label="Tighter line spacing"
            className="flex min-h-11 min-w-11 items-center justify-center rounded-lg disabled:opacity-30"
          >
            <Minus size={14} />
          </button>
          <span className="w-10 text-center text-xs text-slate-300 tabular-nums">{preferences.line_height.toFixed(1)}</span>
          <button
            type="button"
            onClick={() => onPreferences({ line_height: Math.min(3, preferences.line_height + 0.2) })}
            disabled={preferences.line_height >= 3}
            aria-label="Looser line spacing"
            className="flex min-h-11 min-w-11 items-center justify-center rounded-lg disabled:opacity-30"
          >
            <Plus size={14} />
          </button>
        </div>
      </div>

      <div className="space-y-1">
        <span className="text-xs text-slate-400">Typeface</span>
        <div className="flex gap-1 rounded-xl bg-black/30 p-0.5">
          <button type="button" onClick={() => onPreferences({ theme: 'serif' })} aria-pressed={preferences.theme === 'serif'} data-testid="bible-theme-serif" className={`${segment(preferences.theme === 'serif')} font-serif`}>
            Serif
          </button>
          <button type="button" onClick={() => onPreferences({ theme: 'sans' })} aria-pressed={preferences.theme === 'sans'} data-testid="bible-theme-sans" className={segment(preferences.theme === 'sans')}>
            Sans
          </button>
        </div>
      </div>

      <div className="space-y-1">
        <span className="text-xs text-slate-400">Layout</span>
        <div className="flex gap-1 rounded-xl bg-black/30 p-0.5" role="group" aria-label="Layout">
          <button type="button" onClick={() => onPreferences({ layout: 'flow' })} aria-pressed={layout === 'flow'} data-testid="bible-layout-flow" className={`${segment(layout === 'flow')} inline-flex items-center justify-center gap-1.5`}>
            <AlignLeft size={14} /> Paragraphs
          </button>
          <button type="button" onClick={() => onPreferences({ layout: 'verses' })} aria-pressed={layout === 'verses'} data-testid="bible-layout-verses" className={`${segment(layout === 'verses')} inline-flex items-center justify-center gap-1.5`}>
            <List size={14} /> Verse by verse
          </button>
        </div>
      </div>

      <div className="space-y-1">
        <span className="text-xs text-slate-400">Page</span>
        <div className="flex gap-2" role="group" aria-label="Page colour">
          {PAGE_TONE_ORDER.map((tone) => {
            const selected = toneOf(preferences.page) === tone;
            return (
              <button
                key={tone}
                type="button"
                onClick={() => onPreferences({ page: tone })}
                aria-pressed={selected}
                data-testid={`bible-page-${tone}`}
                className={`min-h-11 flex-1 rounded-xl border font-serif text-sm ${selected ? 'border-transparent ring-2 ring-amber-400' : 'border-white/10'}`}
                style={{ backgroundColor: PAGE_TONES[tone].paper, color: PAGE_TONES[tone].ink }}
              >
                {PAGE_TONES[tone].label}
              </button>
            );
          })}
        </div>
      </div>

      <div className="flex items-center justify-between gap-3">
        <span className="text-xs text-slate-400">Commentary</span>
        <button
          type="button"
          onClick={() => {
            void trigger('light');
            onPreferences({ show_notes: !preferences.show_notes });
          }}
          aria-pressed={preferences.show_notes}
          aria-label={preferences.show_notes ? 'Hide commentary from the reading surface' : 'Show commentary alongside the text'}
          data-testid="bible-notes-toggle"
          className={`min-h-11 rounded-xl border px-3 text-sm ${
            preferences.show_notes ? 'border-amber-400/50 bg-amber-500/10 text-amber-200' : 'border-white/10 text-slate-300'
          }`}
        >
          {preferences.show_notes ? 'Alongside' : 'Hidden'}
        </button>
      </div>
    </div>
  );

  const panels: Record<Panel, { title: string; body: ReactNode; testId?: string }> = {
    passage: { title: 'Choose a passage', body: passagePanel, testId: 'bible-browse-sheet' },
    version: { title: 'Version', body: versionPanel },
    compare: { title: 'Compare translations', body: comparePanel },
    display: { title: 'Text settings', body: displayPanel },
  };
  const current = panel ? panels[panel] : null;
  const scrim = current && panel ? (
          <button
            type="button"
            aria-label={`Close ${current.title.toLowerCase()}`}
            data-testid={panel === 'passage' ? 'bible-browse-scrim' : 'bible-panel-scrim'}
            onClick={close}
            className={`fixed inset-0 z-40 cursor-default ${isPhone ? 'bg-black/60 backdrop-blur-sm' : 'bg-transparent'}`}
          />
  ) : null;
  const sheet = current && panel ? (
          <div
            role="dialog"
            aria-modal={isPhone || undefined}
            aria-label={current.title}
            data-testid={current.testId}
            className={
              isPhone
                ? 'fixed inset-x-0 bottom-0 z-50 max-h-[85dvh] overflow-y-auto overscroll-contain rounded-t-3xl border border-b-0 border-white/10 bg-slate-950/97 p-4 pb-[max(1rem,env(safe-area-inset-bottom))] backdrop-blur-xl'
                : `absolute top-full z-50 mt-2 max-h-[min(70vh,40rem)] overflow-y-auto overscroll-contain rounded-2xl border border-white/10 bg-slate-950/97 p-4 shadow-2xl backdrop-blur-xl ${
                    panel === 'passage' || panel === 'version' ? 'left-0' : 'right-0'
                  } w-[min(30rem,calc(100vw-2rem))]`
            }
          >
            {isPhone && <div className="mx-auto mb-3 h-1 w-10 rounded-full bg-white/20" aria-hidden />}
            <div className="sticky -top-4 z-10 -mx-4 -mt-4 mb-3 flex items-center justify-between gap-2 bg-slate-950/95 px-4 pb-2 pt-4 backdrop-blur">
              <h2 className="text-sm font-semibold text-slate-100">{current.title}</h2>
              <button type="button" onClick={close} aria-label={`Close ${current.title.toLowerCase()}`} className="flex min-h-11 min-w-11 items-center justify-center rounded-full text-slate-400 hover:bg-white/10 hover:text-white">
                <X size={16} />
              </button>
            </div>
            {current.body}
            {panel === 'display' && (
              // On a narrow screen the header keeps only listen and text
              // settings; compare and focus live here instead of off the edge.
              <div className="mt-4 grid grid-cols-2 gap-2 sm:hidden">
                <button
                  type="button"
                  onClick={() => open('compare')}
                  data-testid="bible-compare-open-narrow"
                  className={`flex min-h-11 items-center justify-center gap-2 rounded-xl border text-sm ${comparing ? 'border-amber-400/50 text-amber-200' : 'border-white/10 text-slate-300'}`}
                >
                  <Columns2 size={14} /> Compare
                </button>
                {onFocus && (
                  <button
                    type="button"
                    onClick={() => {
                      close();
                      onFocus();
                    }}
                    data-testid="bible-focus-open-narrow"
                    className="flex min-h-11 items-center justify-center gap-2 rounded-xl border border-white/10 text-sm text-slate-300"
                  >
                    <Maximize2 size={14} /> Focus
                  </button>
                )}
              </div>
            )}
          </div>
  ) : null;

  return (
    <div className="relative" data-testid="bible-reference-bar">
      <div className="flex min-w-0 items-center gap-1.5" data-testid={isPhone ? 'bible-phone-bar' : undefined}>
        <button
          type="button"
          onClick={() => open('passage')}
          aria-expanded={panel === 'passage'}
          aria-label={`Choose a passage. Now reading ${passageLabel}.`}
          data-testid="bible-browse-open"
          className={`${PILL} min-w-0 max-w-[50%] sm:max-w-none`}
        >
          <span className="truncate">{passageLabel}</span>
          <ChevronDown size={15} className="shrink-0 text-slate-400" aria-hidden />
        </button>
        <button
          type="button"
          onClick={() => open('version')}
          aria-expanded={panel === 'version'}
          aria-label={`Translation: ${selectedVersion?.name ?? versionLabel}. Change it`}
          data-testid="bible-version-open"
          className={PILL}
        >
          {isFavorite && <Star size={12} className="fill-amber-300 text-amber-300" aria-hidden />}
          <span className="tabular-nums">{versionLabel}</span>
          <ChevronDown size={15} className="shrink-0 text-slate-400" aria-hidden />
        </button>
        <div className="ml-auto flex shrink-0 items-center">
          {onListen && (
            <button type="button" onClick={onListen} aria-pressed={listening} aria-label={listening ? 'Hide the audio player' : 'Listen to this chapter'} data-testid="bible-listen-toggle" className={`${ICON} inline-flex`}>
              <Headphones size={17} />
            </button>
          )}
          <button
            type="button"
            onClick={() => open('compare')}
            aria-expanded={panel === 'compare'}
            aria-label="Compare translations"
            data-testid="bible-compare-open"
            className={`${ICON} ${comparing ? 'text-amber-200' : ''} hidden sm:inline-flex`}
          >
            <Columns2 size={17} />
          </button>
          <button
            type="button"
            onClick={() => open('display')}
            aria-expanded={panel === 'display'}
            aria-label="Text display options"
            data-testid="bible-display-toggle"
            className={`${ICON} inline-flex`}
          >
            <Type size={17} />
          </button>
          {onFocus && (
            <button type="button" onClick={onFocus} aria-label="Read this chapter without the controls" data-testid="bible-focus-open" className={`${ICON} hidden sm:inline-flex`}>
              <Maximize2 size={16} />
            </button>
          )}
        </div>
      </div>

      {/* A phone's scrim and sheet go to the body, above the app's own bottom
          bar. A larger screen's dropdown stays here, under the header, with
          its scrim beside it in the same stacking context: a scrim in the body
          would sit above the header's dropdown and swallow every click on it. */}
      {isPhone ? (
        <>
          {scrim && createPortal(scrim, document.body)}
          {sheet && createPortal(sheet, document.body)}
        </>
      ) : (
        <>
          {scrim}
          {sheet}
        </>
      )}
    </div>
  );
}

