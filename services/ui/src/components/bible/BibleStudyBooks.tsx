import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { BookOpen, Loader2, Sparkles } from 'lucide-react';

import { api } from '../../services/api';
import type { BibleImportedBook } from '../../types/api';
import { errorText } from '../../lib/errorText';

/**
 * Imported study books: read a chapter and answer the questions printed at its
 * end from that chapter alone. The Bible reader still owns Scripture; this is
 * the shelf of books the family added for study.
 */
export default function BibleStudyBooks() {
  const [book, setBook] = useState<BibleImportedBook | null>(null);
  const [ordinal, setOrdinal] = useState<number | null>(null);

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

  const detail = useQuery({
    queryKey: ['bible-imported-book', book?.id],
    queryFn: () => api.getBibleImportedBook(book!.id),
    enabled: Boolean(book),
    retry: false,
  });

  const chapter = useQuery({
    queryKey: ['bible-imported-chapter', book?.id, ordinal],
    queryFn: () => api.getBibleImportedBookChapter(book!.id, ordinal as number),
    enabled: Boolean(book && ordinal),
    retry: false,
  });

  const answer = useMutation({
    mutationFn: (question: string) =>
      api.askBibleBookQuestion(book!.id, ordinal as number, question),
  });

  if (!book) {
    return (
      <section className="space-y-2" data-testid="bible-study-books">
        <h2 className="text-sm font-bold text-white flex items-center gap-2">
          <BookOpen size={16} className="text-amber-300" />
          Study books
        </h2>
        {books.isLoading ? (
          <p className="text-xs text-slate-500">Loading…</p>
        ) : studyBooks.length === 0 ? (
          <p className="text-xs text-slate-500">No study books yet. Import one in Admin › Bible.</p>
        ) : (
          <ul className="grid gap-2 sm:grid-cols-2">
            {studyBooks.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  onClick={() => {
                    setBook(item);
                    setOrdinal(null);
                  }}
                  className="w-full rounded-xl border border-white/5 bg-white/[0.02] p-3 text-left"
                >
                  <span className="block truncate text-xs text-slate-200">{item.title}</span>
                  <span className="block truncate text-[10px] text-slate-500">
                    {item.author.trim() ? `${item.author.trim()} · ` : ''}
                    {item.chapter_count} sections
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    );
  }

  return (
    <section className="space-y-3" data-testid="bible-study-book-reader">
      <button
        type="button"
        onClick={() => {
          setBook(null);
          setOrdinal(null);
          answer.reset();
        }}
        className="text-xs text-slate-400 min-h-9"
      >
        ← All study books
      </button>
      <h2 className="text-sm font-bold text-white">{book.title}</h2>
      {book.author.trim() ? <p className="text-[11px] text-slate-500">{book.author.trim()}</p> : null}

      {detail.isLoading ? <p className="text-xs text-slate-500">Loading chapters…</p> : null}
      {detail.data ? (
        <div className="flex flex-wrap gap-1">
          {detail.data.chapters.map((item) => (
            <button
              key={item.ordinal}
              type="button"
              onClick={() => {
                setOrdinal(item.ordinal);
                answer.reset();
              }}
              aria-pressed={ordinal === item.ordinal}
              className={`min-h-9 rounded-lg border px-2 text-[11px] ${
                ordinal === item.ordinal
                  ? 'border-amber-400/50 bg-amber-500/15 text-amber-100'
                  : 'border-white/10 bg-white/5 text-slate-400'
              }`}
            >
              {item.label || `Chapter ${item.ordinal}`}
            </button>
          ))}
        </div>
      ) : null}

      {chapter.isLoading ? <p className="text-xs text-slate-500">Loading chapter…</p> : null}
      {chapter.isError ? <p className="text-xs text-rose-300">{errorText(chapter.error, 'Could not read that chapter.')}</p> : null}
      {chapter.data ? (
        <article className="space-y-3">
          <h3 className="text-xs font-semibold text-slate-300">{chapter.data.label}</h3>
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-200">{chapter.data.text}</p>
          {chapter.data.questions.length > 0 ? (
            <div className="rounded-xl border border-amber-400/20 bg-amber-500/5 p-3">
              <p className="text-[11px] uppercase tracking-wider text-amber-300/90">Questions at the end</p>
              <ul className="mt-2 space-y-2">
                {chapter.data.questions.map((question, index) => (
                  <li key={`${index}-${question}`} className="space-y-1">
                    <p className="text-xs text-slate-200">{question}</p>
                    <button
                      type="button"
                      onClick={() => answer.mutate(question)}
                      disabled={answer.isPending}
                      className="min-h-9 rounded-lg bg-white/5 px-2 text-[11px] text-sky-200 inline-flex items-center gap-1"
                    >
                      {answer.isPending ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}
                      Answer from this chapter
                    </button>
                  </li>
                ))}
              </ul>
              {answer.data ? (
                <p className="mt-2 whitespace-pre-wrap text-xs text-slate-100" data-testid="bible-book-answer">
                  {answer.data.answer}
                </p>
              ) : null}
              {answer.isError ? <p className="mt-2 text-xs text-rose-300">{errorText(answer.error, 'Could not answer.')}</p> : null}
            </div>
          ) : null}
        </article>
      ) : null}
    </section>
  );
}
