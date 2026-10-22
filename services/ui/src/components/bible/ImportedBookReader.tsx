import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import {
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  List,
  Loader2,
  Minus,
  MoreHorizontal,
  NotebookPen,
  Plus,
  Share2,
  Sparkles,
  Type,
  X,
} from 'lucide-react';
import toast from 'react-hot-toast';

import { api } from '../../services/api';
import type {
  BibleImportedBook,
  BibleImportedBookDetail,
  BiblePreferences,
} from '../../types/api';
import { errorText } from '../../lib/errorText';
import { useHaptics } from '../../hooks/useHaptics';
import { useSwipe } from '../../lib/useSwipe';
import ShareToChatSheet from './ShareToChatSheet';
import { cleanBookPage, type BookPage } from './readerText';
import { PAGE_TONES, PAGE_TONE_ORDER, pageStyle, toneOf } from './readingPage';

type ReadingPreferences = Pick<BiblePreferences, 'page' | 'font_scale' | 'line_height' | 'theme'>;

interface ImportedBookReaderProps {
  book: BibleImportedBook;
  chapters: BibleImportedBookDetail['chapters'];
  preferences: ReadingPreferences;
  onPreferences: (patch: Partial<BiblePreferences>) => void;
  onClose: () => void;
}

function positionKey(bookId: number): string {
  return `jarvis.study-book.${bookId}.page`;
}

function initialOrdinal(bookId: number, chapters: BibleImportedBookDetail['chapters']): number {
  const fallback = chapters[0]?.ordinal ?? 1;
  try {
    const saved = Number(window.localStorage.getItem(positionKey(bookId)));
    if (chapters.some((entry) => entry.ordinal === saved)) return saved;
  } catch {
    /* private mode / storage disabled: read from the start */
  }
  return fallback;
}

/** A small sheet used by contents, settings, page actions and Jarvis. */
function ReaderSheet({
  title,
  onClose,
  children,
  wide = false,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
  wide?: boolean;
}) {
  return (
    <div
      className="fixed inset-0 z-[60] flex items-end sm:items-center sm:justify-center"
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <button
        type="button"
        aria-label={`Close ${title}`}
        onClick={onClose}
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
      />
      <div
        className={`relative flex max-h-[86dvh] w-full flex-col border border-white/10 border-b-0 bg-slate-950/97 p-4 pb-[max(1rem,env(safe-area-inset-bottom))] shadow-2xl sm:rounded-2xl sm:border-b ${
          wide ? 'sm:max-w-lg' : 'sm:max-w-md'
        }`}
      >
        <div className="mb-3 flex items-center justify-between gap-3">
          <p className="text-sm font-semibold text-white">{title}</p>
          <button
            type="button"
            onClick={onClose}
            aria-label={`Close ${title}`}
            className="flex min-h-11 min-w-11 items-center justify-center rounded-xl text-slate-400 hover:text-white"
          >
            <X size={16} />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

/**
 * A book the family imported, read one page at a time.
 *
 * This is deliberately a reading surface rather than a list of buttons: the
 * page fills the screen, the type uses the same paper, serif and size settings
 * as the Scripture reader, and turning a page is a footer control, a swipe or
 * an arrow key. The contents list is still there, but only when asked for.
 */
export default function ImportedBookReader({
  book,
  chapters,
  preferences,
  onPreferences,
  onClose,
}: ImportedBookReaderProps) {
  const { trigger } = useHaptics();
  const [ordinal, setOrdinal] = useState(() => initialOrdinal(book.id, chapters));
  const [contentsOpen, setContentsOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [actionsOpen, setActionsOpen] = useState(false);
  const [askOpen, setAskOpen] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  const [question, setQuestion] = useState('');
  const [askedQuestion, setAskedQuestion] = useState<string | null>(null);
  const textRef = useRef<HTMLDivElement>(null);

  const index = Math.max(0, chapters.findIndex((entry) => entry.ordinal === ordinal));
  const total = chapters.length;
  const meta = chapters[index];
  const unit = useMemo(
    () => (chapters.every((entry) => /^page\b/i.test(entry.label)) ? 'Page' : 'Section'),
    [chapters],
  );

  const chapter = useQuery({
    queryKey: ['bible-imported-chapter', book.id, ordinal],
    queryFn: () => api.getBibleImportedBookChapter(book.id, ordinal),
    retry: false,
  });

  const page = useMemo(
    () => cleanBookPage(chapter.data?.text ?? ''),
    [chapter.data?.text],
  );
  const pageLabel = chapter.data?.label || meta?.label || `${unit} ${index + 1}`;
  const plainText = useMemo(
    () => [page.heading, ...page.paragraphs].filter(Boolean).join('\n\n'),
    [page],
  );

  const ask = useMutation({
    mutationFn: (wanted: string) => api.askBibleBookQuestion(book.id, ordinal, wanted),
  });

  const saveNote = useMutation({
    mutationFn: (payload: { page: BookPage; label: string }) =>
      api.createNote({
        title: `${book.title} — ${payload.label}`,
        category: 'Bible',
        content: `${book.title}\n${payload.label}\n\n${
          [payload.page.heading, ...payload.page.paragraphs].filter(Boolean).join('\n\n')
        }`,
      }),
    onSuccess: () => toast.success('Saved to Notes'),
    onError: (error) => toast.error(errorText(error, 'Could not save this page to Notes')),
  });

  const goToIndex = useCallback(
    (next: number) => {
      const target = chapters[next];
      if (!target) return;
      void trigger('light');
      // Starting a new page should not leave the previous page's answer open.
      ask.reset();
      setAskedQuestion(null);
      setAskOpen(false);
      setQuestion('');
      setOrdinal(target.ordinal);
      setContentsOpen(false);
    },
    [ask, chapters, trigger],
  );

  const step = useCallback(
    (delta: number) => goToIndex(index + delta),
    [goToIndex, index],
  );

  useEffect(() => {
    try {
      window.localStorage.setItem(positionKey(book.id), String(ordinal));
    } catch {
      /* storage disabled: this session still reads correctly */
    }
    textRef.current?.scrollTo({ top: 0 });
  }, [book.id, ordinal]);

  useEffect(() => {
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previous;
    };
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target as HTMLElement | null;

      if (event.key === 'Escape') {
        event.stopPropagation();
        if (askOpen) setAskOpen(false);
        else if (shareOpen) setShareOpen(false);
        else if (actionsOpen) setActionsOpen(false);
        else if (settingsOpen) setSettingsOpen(false);
        else if (contentsOpen) setContentsOpen(false);
        else onClose();
        return;
      }
      // Page keys must not fire while the reader is typing a question.
      if (target?.closest('input, textarea, select, [contenteditable="true"]')) return;
      if (askOpen || shareOpen || actionsOpen || settingsOpen || contentsOpen) return;
      if (event.key === 'ArrowRight' || event.key === 'PageDown') {
        event.preventDefault();
        event.stopPropagation();
        step(1);
      } else if (event.key === 'ArrowLeft' || event.key === 'PageUp') {
        event.preventDefault();
        event.stopPropagation();
        step(-1);
      }
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [actionsOpen, askOpen, contentsOpen, onClose, settingsOpen, shareOpen, step]);

  const swipe = useSwipe(() => step(1), () => step(-1));

  const askQuestion = (wanted: string) => {
    const text = wanted.trim();
    if (!text) return;
    setAskedQuestion(text);
    ask.mutate(text);
  };

  const openAsk = () => {
    setActionsOpen(false);
    setAskOpen(true);
  };

  const savePage = () => {
    setActionsOpen(false);
    saveNote.mutate({ page, label: pageLabel });
  };

  const sharePage = () => {
    setActionsOpen(false);
    setShareOpen(true);
  };

  const answer = ask.data ? (
    <div className="rounded-xl border border-amber-400/20 bg-amber-500/5 p-3" data-testid="bible-study-answer">
      {askedQuestion ? <p className="mb-1 text-[11px] text-[var(--page-muted)]">{askedQuestion}</p> : null}
      <p className="whitespace-pre-wrap text-sm leading-relaxed text-[var(--page-ink)]">{ask.data.answer}</p>
    </div>
  ) : null;

  return (
    <div
      className="fixed inset-0 z-[55] flex h-[100dvh] flex-col"
      style={pageStyle(preferences.page)}
      data-testid="bible-study-reader"
      data-page={toneOf(preferences.page)}
      role="dialog"
      aria-modal="true"
      aria-label={`Read ${book.title}`}
    >
      <header className="shrink-0 border-b border-[var(--page-rule)] bg-[var(--page-paper)] pt-[max(0.25rem,env(safe-area-inset-top))]">
        <div className="mx-auto flex max-w-4xl items-center gap-1 px-2 py-1">
          <button
            type="button"
            onClick={onClose}
            aria-label="Back to study books"
            className="flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-xl text-[var(--page-muted)] hover:bg-[var(--page-hover)]"
          >
            <ArrowLeft size={19} />
          </button>
          <div className="min-w-0 flex-1 px-1 text-center">
            <p className="truncate text-[13px] font-semibold text-[var(--page-ink)]">{book.title}</p>
            <p className="truncate text-[11px] text-[var(--page-muted)]">{pageLabel}</p>
          </div>
          <button
            type="button"
            onClick={() => setContentsOpen(true)}
            aria-label="Table of contents"
            className="flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-xl text-[var(--page-muted)] hover:bg-[var(--page-hover)]"
          >
            <List size={18} />
          </button>
          <button
            type="button"
            onClick={() => setSettingsOpen(true)}
            aria-label="Reading settings"
            className="flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-xl text-[var(--page-muted)] hover:bg-[var(--page-hover)]"
          >
            <Type size={18} />
          </button>
          <button
            type="button"
            onClick={() => setActionsOpen(true)}
            aria-label="Page actions"
            className="flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-xl text-[var(--page-muted)] hover:bg-[var(--page-hover)]"
          >
            <MoreHorizontal size={19} />
          </button>
        </div>
        <div className="h-0.5 bg-[var(--page-rule)]">
          <div
            className="h-full bg-amber-500 transition-[width] duration-200"
            style={{ width: `${total ? ((index + 1) / total) * 100 : 0}%` }}
            data-testid="bible-study-progress"
          />
        </div>
      </header>

      <div
        ref={textRef}
        className="flex-1 overflow-y-auto overscroll-contain"
        {...swipe}
        data-testid="bible-study-page"
      >
        <article className="mx-auto w-full max-w-[68ch] px-5 pb-16 pt-7 sm:px-8 sm:pt-10">
          {chapter.isLoading ? (
            <div className="space-y-3" data-testid="bible-study-loading">
              {[0, 1, 2, 3, 4, 5].map((entry) => (
                <div key={entry} className="h-4 animate-pulse rounded bg-[var(--page-hover)]" />
              ))}
            </div>
          ) : chapter.isError ? (
            <div className="space-y-3" data-testid="bible-study-error">
              <p className="text-sm text-[var(--page-ink)]">
                {errorText(chapter.error, 'That page could not be read.')}
              </p>
              <button
                type="button"
                onClick={() => void chapter.refetch()}
                className="min-h-11 rounded-xl border border-[var(--page-rule)] px-3 text-sm text-[var(--page-ink)]"
              >
                Try again
              </button>
            </div>
          ) : (
            <>
              <p className="mb-3 text-[11px] uppercase tracking-[0.16em] text-[var(--page-muted)]">
                {pageLabel}
              </p>
              {page.heading ? (
                <h2 className="mb-5 font-serif text-2xl leading-tight text-[var(--page-ink)] sm:text-3xl">
                  {page.heading}
                </h2>
              ) : null}
              <div
                className={`select-text text-[var(--page-ink)] ${
                  preferences.theme === 'serif' ? 'font-serif' : 'font-sans'
                }`}
                style={{
                  fontSize: `${1.125 * preferences.font_scale}rem`,
                  lineHeight: preferences.line_height,
                }}
                data-testid="bible-study-reader-text"
              >
                {page.paragraphs.map((paragraph, entry) => (
                  <p key={entry} className="mb-4 text-pretty hyphens-auto">
                    {paragraph}
                  </p>
                ))}
              </div>

              {chapter.data && chapter.data.questions.length > 0 ? (
                <section className="mt-10 border-t border-[var(--page-rule)] pt-5" data-testid="bible-study-questions">
                  <p className="text-[11px] uppercase tracking-[0.16em] text-[var(--page-muted)]">
                    Questions for reflection
                  </p>
                  <ul className="mt-3 space-y-3">
                    {chapter.data.questions.map((item, entry) => (
                      <li key={`${entry}-${item}`} className="space-y-1.5">
                        <p className="text-sm text-[var(--page-ink)]">{item}</p>
                        <button
                          type="button"
                          onClick={() => askQuestion(item)}
                          disabled={ask.isPending}
                          className="inline-flex min-h-9 items-center gap-1.5 rounded-lg border border-[var(--page-rule)] px-2 text-[11px] text-[var(--page-muted)] disabled:opacity-50"
                        >
                          {ask.isPending && askedQuestion === item ? (
                            <Loader2 size={12} className="animate-spin" />
                          ) : (
                            <Sparkles size={12} />
                          )}
                          Ask Jarvis
                        </button>
                      </li>
                    ))}
                  </ul>
                  {ask.isError ? (
                    <p className="mt-3 text-xs text-rose-400">
                      {errorText(ask.error, 'Jarvis could not answer that.')}
                    </p>
                  ) : null}
                  {!askOpen && !shareOpen ? <div className="mt-3">{answer}</div> : null}
                </section>
              ) : null}

              <div className="mt-10 flex flex-wrap gap-2 border-t border-[var(--page-rule)] pt-4">
                <button
                  type="button"
                  onClick={openAsk}
                  className="inline-flex min-h-10 items-center gap-1.5 rounded-xl border border-[var(--page-rule)] px-3 text-xs text-[var(--page-ink)] hover:bg-[var(--page-hover)]"
                >
                  <Sparkles size={14} /> Ask about this page
                </button>
                <button
                  type="button"
                  onClick={savePage}
                  disabled={saveNote.isPending || !chapter.data}
                  className="inline-flex min-h-10 items-center gap-1.5 rounded-xl border border-[var(--page-rule)] px-3 text-xs text-[var(--page-ink)] hover:bg-[var(--page-hover)] disabled:opacity-50"
                >
                  {saveNote.isPending ? <Loader2 size={14} className="animate-spin" /> : <NotebookPen size={14} />}
                  Save page
                </button>
                <button
                  type="button"
                  onClick={sharePage}
                  disabled={!chapter.data}
                  className="inline-flex min-h-10 items-center gap-1.5 rounded-xl border border-[var(--page-rule)] px-3 text-xs text-[var(--page-ink)] hover:bg-[var(--page-hover)] disabled:opacity-50"
                >
                  <Share2 size={14} /> Share page
                </button>
              </div>
            </>
          )}
        </article>
      </div>

      <footer className="shrink-0 border-t border-[var(--page-rule)] bg-[var(--page-paper)] pb-[max(0.25rem,env(safe-area-inset-bottom))]">
        <div className="mx-auto flex max-w-4xl items-center justify-between gap-2 px-2 py-1">
          <button
            type="button"
            onClick={() => step(-1)}
            disabled={index <= 0}
            aria-label="Previous page"
            className="inline-flex min-h-11 items-center gap-1 rounded-xl px-2 text-xs text-[var(--page-muted)] disabled:opacity-30"
          >
            <ChevronLeft size={18} />
            <span className="hidden sm:inline">Previous</span>
          </button>
          <button
            type="button"
            onClick={() => setContentsOpen(true)}
            className="min-h-11 rounded-xl px-2 text-[11px] tabular-nums text-[var(--page-muted)] hover:bg-[var(--page-hover)]"
            aria-label="Open contents"
          >
            {unit} {index + 1} of {total}
          </button>
          <button
            type="button"
            onClick={() => step(1)}
            disabled={index >= total - 1}
            aria-label="Next page"
            className="inline-flex min-h-11 items-center gap-1 rounded-xl px-2 text-xs text-[var(--page-muted)] disabled:opacity-30"
          >
            <span className="hidden sm:inline">Next</span>
            <ChevronRight size={18} />
          </button>
        </div>
      </footer>

      {contentsOpen ? (
        <div
          className="fixed inset-0 z-[60] flex items-end sm:items-center sm:justify-center"
          role="dialog"
          aria-modal="true"
          aria-label="Contents"
        >
          <button
            type="button"
            aria-label="Close contents"
            onClick={() => setContentsOpen(false)}
            className="absolute inset-0 bg-black/60 backdrop-blur-sm"
          />
          <div className="relative flex max-h-[86dvh] w-full flex-col border border-white/10 border-b-0 bg-slate-950/97 p-4 pb-[max(1rem,env(safe-area-inset-bottom))] sm:max-w-lg sm:rounded-2xl sm:border-b">
            <div className="mb-3 flex items-center justify-between gap-3">
              <div>
                <p className="text-sm font-semibold text-white">Contents</p>
                <p className="text-[11px] text-slate-400">
                  {total} {unit.toLowerCase()}s · {book.author.trim() || 'Imported study'}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setContentsOpen(false)}
                aria-label="Close contents"
                className="flex min-h-11 min-w-11 items-center justify-center rounded-xl text-slate-400 hover:text-white"
              >
                <X size={16} />
              </button>
            </div>
            <ol className="flex-1 space-y-0.5 overflow-y-auto" data-testid="bible-study-contents">
              {chapters.map((entry, entryIndex) => (
                <li key={entry.ordinal}>
                  <button
                    type="button"
                    autoFocus={entry.ordinal === ordinal}
                    onClick={() => goToIndex(entryIndex)}
                    aria-current={entry.ordinal === ordinal ? 'page' : undefined}
                    className={`flex min-h-11 w-full items-center justify-between gap-3 rounded-lg px-3 text-left text-sm ${
                      entry.ordinal === ordinal
                        ? 'bg-amber-500/15 text-amber-100'
                        : 'text-slate-300 hover:bg-white/5'
                    }`}
                  >
                    <span className="truncate">{entry.label || `${unit} ${entryIndex + 1}`}</span>
                    <span className="shrink-0 text-[10px] tabular-nums text-slate-500">
                      {entry.characters.toLocaleString()}
                    </span>
                  </button>
                </li>
              ))}
            </ol>
          </div>
        </div>
      ) : null}

      {settingsOpen ? (
        <ReaderSheet title="Reading settings" onClose={() => setSettingsOpen(false)}>
          <div className="space-y-4">
            <div className="flex items-center justify-between gap-3">
              <span className="text-xs text-slate-400">Text size</span>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => onPreferences({ font_scale: Math.max(0.8, Number((preferences.font_scale - 0.1).toFixed(2))) })}
                  disabled={preferences.font_scale <= 0.8}
                  aria-label="Smaller text"
                  className="flex min-h-11 min-w-11 items-center justify-center rounded-xl border border-white/10 text-slate-300 disabled:opacity-30"
                >
                  <Minus size={14} />
                </button>
                <span className="w-12 text-center text-xs tabular-nums text-slate-300" data-testid="bible-study-font-scale">
                  {preferences.font_scale.toFixed(2)}x
                </span>
                <button
                  type="button"
                  onClick={() => onPreferences({ font_scale: Math.min(1.6, Number((preferences.font_scale + 0.1).toFixed(2))) })}
                  disabled={preferences.font_scale >= 1.6}
                  aria-label="Larger text"
                  className="flex min-h-11 min-w-11 items-center justify-center rounded-xl border border-white/10 text-slate-300 disabled:opacity-30"
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
                  onClick={() => onPreferences({ line_height: Math.max(1.2, Number((preferences.line_height - 0.2).toFixed(1))) })}
                  disabled={preferences.line_height <= 1.2}
                  aria-label="Tighter line spacing"
                  className="flex min-h-11 min-w-11 items-center justify-center rounded-xl border border-white/10 text-slate-300 disabled:opacity-30"
                >
                  <Minus size={14} />
                </button>
                <span className="w-12 text-center text-xs tabular-nums text-slate-300">
                  {preferences.line_height.toFixed(1)}
                </span>
                <button
                  type="button"
                  onClick={() => onPreferences({ line_height: Math.min(2.4, Number((preferences.line_height + 0.2).toFixed(1))) })}
                  disabled={preferences.line_height >= 2.4}
                  aria-label="Looser line spacing"
                  className="flex min-h-11 min-w-11 items-center justify-center rounded-xl border border-white/10 text-slate-300 disabled:opacity-30"
                >
                  <Plus size={14} />
                </button>
              </div>
            </div>

            <div className="flex items-center justify-between gap-3">
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
                      aria-label={`${PAGE_TONES[tone].label} page`}
                      className={`min-h-11 rounded-xl border px-3 text-sm font-serif ${
                        selected ? 'border-amber-400 ring-2 ring-amber-400/70' : 'border-white/10'
                      }`}
                      style={{ backgroundColor: PAGE_TONES[tone].paper, color: PAGE_TONES[tone].ink }}
                    >
                      {PAGE_TONES[tone].label}
                    </button>
                  );
                })}
              </div>
            </div>

            <div className="flex items-center justify-between gap-3">
              <span className="text-xs text-slate-400">Typeface</span>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => onPreferences({ theme: 'serif' })}
                  aria-pressed={preferences.theme === 'serif'}
                  className={`min-h-11 rounded-xl border px-3 text-sm font-serif ${
                    preferences.theme === 'serif' ? 'border-amber-400 text-amber-100' : 'border-white/10 text-slate-300'
                  }`}
                >
                  Serif
                </button>
                <button
                  type="button"
                  onClick={() => onPreferences({ theme: 'sans' })}
                  aria-pressed={preferences.theme === 'sans'}
                  className={`min-h-11 rounded-xl border px-3 text-sm font-sans ${
                    preferences.theme === 'sans' ? 'border-amber-400 text-amber-100' : 'border-white/10 text-slate-300'
                  }`}
                >
                  Sans
                </button>
              </div>
            </div>
            <p className="text-[11px] leading-relaxed text-slate-500">
              These are the same reading settings as the Bible reader, so the page looks the same in both.
            </p>
          </div>
        </ReaderSheet>
      ) : null}

      {actionsOpen ? (
        <ReaderSheet title="Page actions" onClose={() => setActionsOpen(false)}>
          <div className="space-y-1">
            <button
              type="button"
              onClick={openAsk}
              className="flex min-h-12 w-full items-center gap-3 rounded-xl px-2 text-left text-sm text-slate-200 hover:bg-white/5"
            >
              <Sparkles size={17} className="text-amber-300" />
              Ask Jarvis about this page
            </button>
            <button
              type="button"
              onClick={savePage}
              disabled={saveNote.isPending || !chapter.data}
              className="flex min-h-12 w-full items-center gap-3 rounded-xl px-2 text-left text-sm text-slate-200 hover:bg-white/5 disabled:opacity-50"
            >
              {saveNote.isPending ? <Loader2 size={17} className="animate-spin" /> : <NotebookPen size={17} className="text-sky-300" />}
              Save page to Notes
            </button>
            <button
              type="button"
              onClick={sharePage}
              disabled={!chapter.data}
              className="flex min-h-12 w-full items-center gap-3 rounded-xl px-2 text-left text-sm text-slate-200 hover:bg-white/5 disabled:opacity-50"
            >
              <Share2 size={17} className="text-emerald-300" />
              Share page to chat
            </button>
          </div>
        </ReaderSheet>
      ) : null}

      {askOpen ? (
        <ReaderSheet title="Ask Jarvis" onClose={() => setAskOpen(false)} wide>
          <form
            className="space-y-3"
            onSubmit={(event) => {
              event.preventDefault();
              askQuestion(question);
            }}
          >
            <p className="text-xs leading-relaxed text-slate-400">
              Jarvis answers from {book.title}, {pageLabel.toLowerCase()} only.
            </p>
            <div className="flex gap-2">
              <input
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                aria-label="Question for Jarvis"
                placeholder="Ask about this page…"
                className="glass-input min-h-11 flex-1 text-sm"
              />
              <button
                type="submit"
                disabled={!question.trim() || ask.isPending}
                className="glass-button min-h-11 px-3 text-sm disabled:opacity-50"
              >
                {ask.isPending ? <Loader2 size={15} className="animate-spin" /> : 'Ask'}
              </button>
            </div>
            <div className="flex flex-wrap gap-2">
              {['Summarize this page', 'What should I take away?'].map((prompt) => (
                <button
                  key={prompt}
                  type="button"
                  onClick={() => {
                    setQuestion(prompt);
                    askQuestion(prompt);
                  }}
                  disabled={ask.isPending}
                  className="min-h-9 rounded-lg border border-white/10 px-2 text-[11px] text-slate-300 disabled:opacity-50"
                >
                  {prompt}
                </button>
              ))}
            </div>
          </form>
          <div className="mt-3 overflow-y-auto">
            {ask.isPending ? (
              <p className="flex items-center gap-2 text-sm text-slate-400">
                <Loader2 size={14} className="animate-spin" /> Jarvis is reading…
              </p>
            ) : null}
            {ask.isError ? (
              <p className="text-sm text-rose-300">{errorText(ask.error, 'Jarvis could not answer that.')}</p>
            ) : null}
            {answer}
          </div>
        </ReaderSheet>
      ) : null}

      {shareOpen && chapter.data ? (
        <ShareToChatSheet
          reference={`${book.title} · ${pageLabel}`}
          text={plainText}
          onClose={() => setShareOpen(false)}
        />
      ) : null}
    </div>
  );
}
