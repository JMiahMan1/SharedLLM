import { useState } from 'react';
import { ChevronLeft, ChevronRight, Columns2, Minus, Plus, Settings2, Star, X } from 'lucide-react';
import { useHaptics } from '../../hooks/useHaptics';
import { PHONE_QUERY, useMediaQuery } from '../../hooks/useMediaQuery';
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
  position: BiblePosition;
  preferences: BiblePreferences;
  /** Text the reader typed, kept here so a failed parse does not lose it. */
  input: string;
  onInput: (value: string) => void;
  onSubmit: () => void;
  onPosition: (patch: Partial<BiblePosition>) => void;
  onPreferences: (patch: Partial<BiblePreferences>) => void;
  onStep: (delta: number) => void;
}

const TEXT_STEPS = [0.85, 1, 1.15, 1.3, 1.5];

/**
 * Everything needed to point the reader somewhere, and to make the text
 * comfortable once it is there.
 *
 * Two rules drive the layout. First, no control is smaller than a thumb: every
 * button carries `min-h-11` so the phone is usable one-handed, and the steppers
 * are 44px rather than the 24px a desktop density would allow. Second, nothing
 * is hover-only -- the display controls are a real popover with a real close
 * button, because a phone has no hover state to reveal them with.
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
  onStep,
}: ReferenceBarProps) {
  const { trigger } = useHaptics();
  const [displayOpen, setDisplayOpen] = useState(false);
  const [browseOpen, setBrowseOpen] = useState(false);
  const isPhone = useMediaQuery(PHONE_QUERY);

  const book = books.find((b) => b.osis === position.book) ?? books[0];
  const selectedVersion = versions.find((v) => v.code === preferences.default_version);
  const isFavorite = Boolean(preferences.favorite_version);
  const favoriteName = versions.find((v) => v.code === preferences.favorite_version)?.name ?? '';
  const installedEditions = editions.filter((entry) => entry.installed);
  const selectedEdition = installedEditions.some(
    (entry) => entry.code === preferences.default_edition,
  )
    ? preferences.default_edition
    : (installedEditions[0]?.code ?? '');
  const selectedEditionEntry = installedEditions.find((entry) => entry.code === selectedEdition);
  const chapterCount = book?.chapters ?? 1;
  const textIndex = Math.max(0, TEXT_STEPS.indexOf(preferences.font_scale));
  const comparing = Boolean(preferences.compare_version);
  // The same words twice is not a comparison, so the reader's own translation is
  // never offered as something to compare against.
  const comparableVersions = versions.filter(
    (v) => v.installed && v.code !== preferences.default_version,
  );

  const nudge = (delta: number) => {
    void trigger('light');
    onStep(delta);
  };

  const bumpFont = (direction: 1 | -1) => {
    void trigger('light');
    const next = Math.min(TEXT_STEPS.length - 1, Math.max(0, textIndex + direction));
    onPreferences({ font_scale: TEXT_STEPS[next] });
  };

  // Choosing a passage is a signal that the reader wants the text rather than
  // the chooser, so on a phone the sheet gets out of the way once something is
  // picked. The desktop bar ignores this -- nothing is covering anything there.
  const stopBrowsing = () => {
    if (isPhone) setBrowseOpen(false);
  };

  const choosePosition = (patch: Partial<BiblePosition>) => {
    onPosition(patch);
    stopBrowsing();
  };

  const chooseSubmit = () => {
    onSubmit();
    stopBrowsing();
  };

  const passageLabel = `${book?.name ?? position.book} ${position.chapter}`;

  const controls = (
    <>
      <div className="space-y-3" data-testid="bible-reference-bar">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          chooseSubmit();
        }}
        className="flex gap-2"
      >
        <input
          type="text"
          value={input}
          onChange={(e) => onInput(e.target.value)}
          placeholder="John 3:16, Psalm 23, or a keyword"
          aria-label="Passage or search"
          data-testid="bible-ref-input"
          className="flex-1 min-w-0 glass-input px-3 py-2.5 text-sm min-h-11 rounded-xl bg-black/25 border border-white/10 text-slate-100 placeholder:text-slate-600"
        />
        <button
          type="submit"
          data-testid="bible-ref-go"
          className="glass-button px-4 py-2.5 text-sm font-semibold min-h-11 shrink-0"
        >
          Go
        </button>
      </form>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <label className="col-span-2 sm:col-span-1 flex flex-col gap-1 min-w-0">
          <span className="text-[10px] uppercase tracking-wider text-slate-500">Book</span>
          <select
            value={position.book}
            onChange={(e) => choosePosition({ book: e.target.value, chapter: 1, verse: 1 })}
            aria-label="Book"
            data-testid="bible-book-select"
            className="min-h-11 rounded-xl bg-black/25 border border-white/10 text-sm text-slate-100 px-2 py-2"
          >
            {books.map((b) => (
              <option key={b.osis} value={b.osis}>
                {b.name}
              </option>
            ))}
          </select>
        </label>

        <div className="flex flex-col gap-1">
          <span className="text-[10px] uppercase tracking-wider text-slate-500">Chapter</span>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => choosePosition({ chapter: Math.max(1, position.chapter - 1) })}
              disabled={position.chapter <= 1}
              aria-label="Previous chapter"
              data-testid="bible-chapter-down"
              className="min-h-11 min-w-11 shrink-0 rounded-xl glass-button flex items-center justify-center disabled:opacity-30"
            >
              <Minus size={14} />
            </button>
            <input
              type="number"
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
              className="min-h-11 w-full min-w-0 text-center rounded-xl bg-black/25 border border-white/10 text-sm text-slate-100 px-1 py-2"
            />
            <button
              type="button"
              onClick={() => choosePosition({ chapter: Math.min(chapterCount, position.chapter + 1) })}
              disabled={position.chapter >= chapterCount}
              aria-label="Next chapter"
              data-testid="bible-chapter-up"
              className="min-h-11 min-w-11 shrink-0 rounded-xl glass-button flex items-center justify-center disabled:opacity-30"
            >
              <Plus size={14} />
            </button>
          </div>
        </div>

        <label className="flex flex-col gap-1 min-w-0">
          <span className="text-[10px] uppercase tracking-wider text-slate-500">Version</span>
          <select
            value={preferences.default_version}
            onChange={(e) => onPreferences({ default_version: e.target.value })}
            aria-label="Translation"
            data-testid="bible-version-select"
            className="min-h-11 rounded-xl bg-black/25 border border-white/10 text-sm text-slate-100 px-2 py-2"
          >
            {versions.map((v) => (
              <option key={v.code} value={v.code}>
                {v.name}
                {v.installed ? '' : ' — not on this server'}
              </option>
            ))}
          </select>
          {selectedVersion && !selectedVersion.installed && (
            <p
              data-testid="bible-version-unavailable"
              className="text-[11px] leading-snug text-amber-300/90"
            >
              {selectedVersion.note}
            </p>
          )}
          <button
            type="button"
            onClick={() => onPreferences({ favorite_version: isFavorite ? '' : (preferences.default_version ?? '') })}
            aria-pressed={isFavorite}
            aria-label={isFavorite ? 'Remove this translation from favourites' : 'Make this translation a favourite'}
            data-testid="bible-favorite-toggle"
            className="mt-1 flex min-h-11 items-center justify-center gap-1.5 rounded-xl border border-white/10 bg-black/25 px-2 py-1 text-[11px] text-slate-200"
          >
            <Star size={14} className={isFavorite ? 'fill-amber-300 text-amber-300' : 'text-slate-400'} />
            {isFavorite ? 'Favourite' : 'Make favourite'}
          </button>
          {favoriteName && (
            <p data-testid="bible-favorite-note" className="text-[11px] leading-snug text-slate-400">
              You can jump to {favoriteName} from the Home tab.
            </p>
          )}
          {selectedVersion?.installed && selectedVersion.license_class === 'licensed' && (
            <p
              data-testid="bible-version-licensed"
              className="text-[11px] leading-snug text-slate-400"
            >
              {selectedVersion.note || 'Licensed text. Do not redistribute.'}
            </p>
          )}
        </label>

        {installedEditions.length > 0 && (
          <label className="flex flex-col gap-1 min-w-0">
            <span className="text-[10px] uppercase tracking-wider text-slate-500">Study Bible</span>
            <select
              value={selectedEdition}
              onChange={(e) => onPreferences({ default_edition: e.target.value })}
              aria-label="Study Bible"
              data-testid="bible-edition-select"
              className="min-h-11 rounded-xl bg-black/25 border border-white/10 text-sm text-slate-100 px-2 py-2"
            >
              {installedEditions.map((entry) => (
                <option key={entry.code} value={entry.code}>
                  {entry.name}
                  {entry.note_count ? ` · ${entry.note_count.toLocaleString()} notes` : ' · no notes'}
                </option>
              ))}
            </select>
            {selectedEditionEntry?.license_class === 'licensed' && (
              <p
                data-testid="bible-edition-licensed"
                className="text-[11px] leading-snug text-slate-400"
              >
                {selectedEditionEntry.note || 'Licensed notes. Do not redistribute.'}
              </p>
            )}
          </label>
        )}

        <div className="flex flex-col gap-1">
          <span className="text-[10px] uppercase tracking-wider text-slate-500">Compare</span>
          <button
            type="button"
            onClick={() => {
              void trigger('light');
              onPreferences({
                compare_version: comparing ? '' : (comparableVersions[0]?.code ?? ''),
              });
            }}
            disabled={!comparableVersions.length}
            aria-pressed={comparing}
            aria-label={comparing ? 'Stop comparing translations' : 'Compare with another translation'}
            data-testid="bible-compare-toggle"
            className={`min-h-11 rounded-xl px-3 py-2 text-sm flex items-center justify-center gap-1.5 border disabled:opacity-40 ${
              comparing
                ? 'border-amber-400/50 text-amber-200 bg-amber-500/10'
                : 'border-white/10 glass-button text-slate-200'
            }`}
          >
            <Columns2 size={14} />
            {comparing ? 'On' : 'Off'}
          </button>
          {comparing && (
            <select
              value={preferences.compare_version}
              onChange={(e) => onPreferences({ compare_version: e.target.value })}
              aria-label="Translation to compare with"
              data-testid="bible-compare-select"
              className="min-h-11 rounded-xl bg-black/25 border border-white/10 text-sm text-slate-100 px-2 py-2"
            >
              {comparableVersions.map((v) => (
                <option key={v.code} value={v.code}>
                  {v.name}
                </option>
              ))}
            </select>
          )}
        </div>

        <div className="flex flex-col gap-1">
          <span className="text-[10px] uppercase tracking-wider text-slate-500">Display</span>
          <button
            type="button"
            onClick={() => {
              void trigger('light');
              setDisplayOpen((open) => !open);
            }}
            aria-expanded={displayOpen}
            aria-label="Text display options"
            data-testid="bible-display-toggle"
            className="min-h-11 rounded-xl glass-button px-3 py-2 text-sm flex items-center justify-center gap-2"
          >
            <Settings2 size={14} />
            {preferences.theme === 'sans' ? 'Sans' : 'Serif'}
          </button>
        </div>
      </div>

      {displayOpen && (
        <div
          className="glass-panel rounded-2xl p-4 space-y-4"
          data-testid="bible-display-panel"
        >
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-slate-200">Text display</h3>
            <button
              type="button"
              onClick={() => setDisplayOpen(false)}
              aria-label="Close display options"
              className="min-h-11 min-w-11 flex items-center justify-center rounded-xl text-slate-400 hover:text-white"
            >
              <X size={16} />
            </button>
          </div>

          <div className="flex items-center justify-between gap-3">
            <span className="text-xs text-slate-400">Text size</span>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => bumpFont(-1)}
                disabled={textIndex === 0}
                aria-label="Smaller text"
                className="min-h-11 min-w-11 rounded-xl glass-button flex items-center justify-center disabled:opacity-30"
              >
                <Minus size={14} />
              </button>
              <span className="text-xs text-slate-300 w-14 text-center" data-testid="bible-font-scale">
                {preferences.font_scale.toFixed(2)}x
              </span>
              <button
                type="button"
                onClick={() => bumpFont(1)}
                disabled={textIndex === TEXT_STEPS.length - 1}
                aria-label="Larger text"
                className="min-h-11 min-w-11 rounded-xl glass-button flex items-center justify-center disabled:opacity-30"
              >
                <Plus size={14} />
              </button>
            </div>
          </div>

          <div className="flex items-center justify-between gap-3">
            <span className="text-xs text-slate-400">Line spacing</span>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => onPreferences({ line_height: Math.max(1, preferences.line_height - 0.2) })}
                disabled={preferences.line_height <= 1}
                aria-label="Tighter line spacing"
                className="min-h-11 min-w-11 rounded-xl glass-button flex items-center justify-center disabled:opacity-30"
              >
                <Minus size={14} />
              </button>
              <span className="text-xs text-slate-300 w-14 text-center">{preferences.line_height.toFixed(1)}</span>
              <button
                type="button"
                onClick={() => onPreferences({ line_height: Math.min(3, preferences.line_height + 0.2) })}
                disabled={preferences.line_height >= 3}
                aria-label="Looser line spacing"
                className="min-h-11 min-w-11 rounded-xl glass-button flex items-center justify-center disabled:opacity-30"
              >
                <Plus size={14} />
              </button>
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
              aria-label={
                preferences.show_notes
                  ? 'Hide commentary from the reading surface'
                  : 'Show commentary alongside the text'
              }
              data-testid="bible-notes-toggle"
              className={`min-h-11 px-3 rounded-xl border text-sm ${
                preferences.show_notes
                  ? 'border-amber-400/50 text-amber-200 bg-amber-500/10'
                  : 'border-white/10 text-slate-300'
              }`}
            >
              {preferences.show_notes ? 'Alongside' : 'Hidden'}
            </button>
          </div>

          <div className="flex items-center justify-between gap-3">
            <span className="text-xs text-slate-400">Typeface</span>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => onPreferences({ theme: 'serif' })}
                aria-pressed={preferences.theme === 'serif'}
                data-testid="bible-theme-serif"
                className={`min-h-11 px-3 rounded-xl border text-sm font-serif ${
                  preferences.theme === 'serif'
                    ? 'border-amber-400/50 text-amber-200 bg-amber-500/10'
                    : 'border-white/10 text-slate-300'
                }`}
              >
                Serif
              </button>
              <button
                type="button"
                onClick={() => onPreferences({ theme: 'sans' })}
                aria-pressed={preferences.theme === 'sans'}
                data-testid="bible-theme-sans"
                className={`min-h-11 px-3 rounded-xl border text-sm ${
                  preferences.theme === 'sans'
                    ? 'border-amber-400/50 text-amber-200 bg-amber-500/10'
                    : 'border-white/10 text-slate-300'
                }`}
              >
                Sans
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="grid grid-cols-2 gap-2">
        <button
          type="button"
          onClick={() => nudge(-1)}
          data-testid="bible-prev-chapter"
          className="glass-button px-3 py-2.5 text-sm min-h-11 flex items-center justify-center gap-1.5"
        >
          <ChevronLeft size={15} /> Previous
        </button>
        <button
          type="button"
          onClick={() => nudge(1)}
          data-testid="bible-next-chapter"
          className="glass-button px-3 py-2.5 text-sm min-h-11 flex items-center justify-center gap-1.5"
        >
          Next <ChevronRight size={15} />
        </button>
      </div>
    </div>
    </>
  );

  if (isPhone) {
    return (
      <div className="flex items-center gap-1.5" data-testid="bible-phone-bar">
        <button
          type="button"
          onClick={() => {
            void trigger('light');
            setBrowseOpen(true);
          }}
          aria-label={`Choose a passage. Now reading ${passageLabel}.`}
          data-testid="bible-browse-open"
          className="flex-1 min-w-0 min-h-11 rounded-xl glass-button px-3 py-2 text-sm flex items-center justify-between gap-2"
        >
          <span className="truncate text-slate-100">{passageLabel}</span>
          <ChevronRight size={15} className="shrink-0 text-slate-400" />
        </button>
        <button
          type="button"
          onClick={() => nudge(-1)}
          aria-label="Previous chapter"
          data-testid="bible-prev-chapter"
          className="w-11 min-h-11 rounded-xl glass-button flex items-center justify-center"
        >
          <ChevronLeft size={16} />
        </button>
        <button
          type="button"
          onClick={() => nudge(1)}
          aria-label="Next chapter"
          data-testid="bible-next-chapter"
          className="w-11 min-h-11 rounded-xl glass-button flex items-center justify-center"
        >
          <ChevronRight size={16} />
        </button>

        {browseOpen && (
          <div
            className="fixed inset-0 z-50 flex items-end sm:items-center sm:justify-center"
            role="dialog"
            aria-modal="true"
            aria-label="Choose a passage"
          >
            <button
              type="button"
              aria-label="Close the passage chooser"
              data-testid="bible-browse-scrim"
              onClick={() => setBrowseOpen(false)}
              className="absolute inset-0 bg-black/60 backdrop-blur-sm"
            />
            <div
              data-testid="bible-browse-sheet"
              className="relative w-full sm:max-w-lg bg-slate-950/97 backdrop-blur-xl border border-white/10 border-b-0 sm:border-b sm:rounded-2xl p-4 pb-[max(1rem,env(safe-area-inset-bottom))] max-h-[85vh] overflow-y-auto"
            >
              <div className="mb-3 flex items-center justify-between">
                <h2 className="text-sm font-semibold text-slate-200">Choose a passage</h2>
                <button
                  type="button"
                  onClick={() => setBrowseOpen(false)}
                  aria-label="Close the passage chooser"
                  className="min-h-11 min-w-11 flex items-center justify-center rounded-xl text-slate-400 hover:text-white"
                >
                  <X size={16} />
                </button>
              </div>
              {controls}
            </div>
          </div>
        )}
      </div>
    );
  }

  // The desktop bar is the controls themselves: one element, unchanged from
  // what this component has always rendered.
  return controls;
}