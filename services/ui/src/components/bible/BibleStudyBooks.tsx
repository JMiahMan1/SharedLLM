import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { BookOpen, Loader2 } from 'lucide-react';

import { api } from '../../services/api';
import type { BibleImportedBook, BiblePreferences } from '../../types/api';
import { errorText } from '../../lib/errorText';
import ImportedBookReader from './ImportedBookReader';

type ReadingPreferences = Pick<BiblePreferences, 'page' | 'font_scale' | 'line_height' | 'theme'> &
  Partial<Pick<BiblePreferences, 'read_aloud_voice'>>;

/**
 * A request to open a book at a place: from a link such as
 * `/bible?tab=studies&book=7&section=s-lesson-3#p-1a2b3c4d`, or a reminder.
 * `section` is the stable section id; `ordinal` the section number; `anchor` a block id.
 */
export interface StudyOpenRequest {
  bookId: number;
  section?: string;
  ordinal?: number;
  anchor?: string;
}

const DEFAULT_READING: ReadingPreferences = {
  page: 'dark',
  font_scale: 1,
  line_height: 1.6,
  theme: 'serif',
};

interface BibleStudyBooksProps {
  /** Shared with the Scripture reader so both surfaces feel like one app. */
  preferences?: ReadingPreferences;
  onPreferences?: (patch: Partial<BiblePreferences>) => void;
  /** Open a book straight away at a section or paragraph. */
  openRequest?: StudyOpenRequest | null;
  /** A scripture reference tapped in a book opens the Bible reader there. */
  onOpenRef?: (ref: string) => void;
  /** Admins see "Reformat this book" in a book's page actions. */
  canReformat?: boolean;
}

/**
 * Imported study books: a shelf, then a reading surface.
 *
 * The shelf opens a book into a page-at-a-time reader rather than printing
 * every extracted page as a chip. The reader keeps the Bible reader's paper,
 * type and size settings, remembers where the reader stopped, and can hand a
 * page to Jarvis, Notes or a family chat.
 */
export default function BibleStudyBooks({
  preferences = DEFAULT_READING,
  onPreferences = () => {},
  openRequest = null,
  onOpenRef,
  canReformat = false,
}: BibleStudyBooksProps) {
  const [picked, setPicked] = useState<BibleImportedBook | null>(null);
  // The link that was last closed, so closing a linked book returns to the shelf.
  const [closedRequest, setClosedRequest] = useState<StudyOpenRequest | null>(null);

  const books = useQuery({
    queryKey: ['bible-imported-books', 'study'],
    queryFn: () => api.getBibleImportedBooks('study'),
    retry: false,
  });

  // Show every imported study book that has readable sections. Hiding books
  // without author metadata made a perfectly good PDF look as if the import
  // had failed; the author is optional, the book is not. Admin › Bible still
  // owns deletion, so a bad import is removed there rather than silently
  // disappearing from the shelf.
  const studyBooks = (books.data?.books ?? []).filter((item) => item.chapter_count > 0);

  // A link names the book by id; it opens once the shelf has it.
  const linked =
    openRequest && openRequest !== closedRequest
      ? (books.data?.books ?? []).find((item) => item.id === openRequest.bookId) ?? null
      : null;
  const book = picked ?? linked;
  const place = !picked && linked ? openRequest : null;
  const setBook = (next: BibleImportedBook | null) => {
    if (!next && openRequest) setClosedRequest(openRequest);
    setPicked(next);
  };

  const detail = useQuery({
    queryKey: ['bible-imported-book', book?.id],
    queryFn: () => api.getBibleImportedBook(book!.id),
    enabled: Boolean(book),
    retry: false,
  });

  if (book) {
    if (detail.data) {
      return (
        <ImportedBookReader
          book={detail.data.book ?? book}
          chapters={detail.data.chapters}
          aliases={detail.data.aliases}
          startOrdinal={place?.bookId === book.id ? place.ordinal : undefined}
          startSection={place?.bookId === book.id ? place.section : undefined}
          startAnchor={place?.bookId === book.id ? place.anchor : undefined}
          preferences={preferences}
          onPreferences={onPreferences}
          onOpenRef={onOpenRef}
          canReformat={canReformat}
          onClose={() => setBook(null)}
        />
      );
    }
    return (
      <section className="space-y-3" data-testid="bible-study-book-reader">
        <button
          type="button"
          onClick={() => setBook(null)}
          className="min-h-9 text-xs text-slate-400"
        >
          ← All study books
        </button>
        {detail.isLoading ? (
          <p className="flex items-center gap-2 text-xs text-slate-500">
            <Loader2 size={13} className="animate-spin" /> Opening {book.title}…
          </p>
        ) : (
          <p className="text-xs text-rose-300">
            {errorText(detail.error, 'That book could not be opened.')}
          </p>
        )}
      </section>
    );
  }

  return (
    <section className="space-y-2" data-testid="bible-study-books">
      <h2 className="flex items-center gap-2 text-sm font-bold text-white">
        <BookOpen size={16} className="text-amber-300" />
        Study books
      </h2>
      {books.isLoading ? (
        <p className="text-xs text-slate-500">Loading…</p>
      ) : studyBooks.length === 0 ? (
        <p className="text-xs text-slate-500">No study books yet. Import one in Admin › Bible.</p>
      ) : (
        <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {studyBooks.map((item) => (
            <li key={item.id}>
              <button
                type="button"
                onClick={() => setBook(item)}
                className="flex w-full items-center gap-3 rounded-xl border border-white/5 bg-white/[0.02] p-3 text-left transition-colors hover:border-amber-400/30 hover:bg-white/[0.04]"
              >
                <span className="flex h-11 w-9 shrink-0 items-center justify-center rounded-md border border-amber-400/20 bg-gradient-to-b from-amber-500/20 to-amber-900/10 text-amber-200">
                  <BookOpen size={17} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-xs text-slate-200">{item.title}</span>
                  <span className="block truncate text-[10px] text-slate-500">
                    {item.author.trim() ? `${item.author.trim()} · ` : ''}
                    {item.chapter_count} sections
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
