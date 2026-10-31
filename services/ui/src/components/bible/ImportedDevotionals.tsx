import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { BookOpen, Loader2 } from 'lucide-react';
import toast from 'react-hot-toast';

import { api } from '../../services/api';
import type { BibleImportedBook, BiblePreferences } from '../../types/api';
import { errorText } from '../../lib/errorText';
import ImportedBookReader from './ImportedBookReader';
import { todayOrdinal, type Cadence } from './devotionalCadence';

type ReadingPreferences = Pick<BiblePreferences, 'page' | 'font_scale' | 'line_height' | 'theme'>;

const DEFAULT_READING: ReadingPreferences = {
  page: 'dark',
  font_scale: 1,
  line_height: 1.6,
  theme: 'serif',
};

export default function ImportedDevotionals({
  preferences = DEFAULT_READING,
  onPreferences = () => {},
}: {
  preferences?: ReadingPreferences;
  onPreferences?: (patch: Partial<BiblePreferences>) => void;
}) {
  const queryClient = useQueryClient();
  const [book, setBook] = useState<BibleImportedBook | null>(null);
  const books = useQuery({
    queryKey: ['bible-imported-books', 'devotional'],
    queryFn: () => api.getBibleImportedBooks('devotional'),
    retry: false,
  });
  const devotionals = (books.data?.books ?? []).filter((item) => item.chapter_count > 0);
  const detail = useQuery({
    queryKey: ['bible-imported-book', book?.id],
    queryFn: () => api.getBibleImportedBook(book!.id),
    enabled: Boolean(book),
    retry: false,
  });

  const cadenceOf = (item: BibleImportedBook): Cadence => (item.cadence === 'week' ? 'week' : 'day');
  const startOrdinal = detail.data && book ? todayOrdinal(detail.data.chapters, cadenceOf(book)) : undefined;

  const chooseCadence = async (item: BibleImportedBook, next: Cadence) => {
    if (cadenceOf(item) === next) return;
    const key = ['bible-imported-books', 'devotional'] as const;
    type BooksReply = Awaited<ReturnType<typeof api.getBibleImportedBooks>>;
    const previous = queryClient.getQueryData<BooksReply>(key);
    if (previous) {
      queryClient.setQueryData<BooksReply>(key, {
        ...previous,
        books: previous.books.map((entry) => (entry.id === item.id ? { ...entry, cadence: next } : entry)),
      });
    }
    try {
      await api.setBibleDevotionalCadence(item.id, next);
      await queryClient.invalidateQueries({ queryKey: key });
      await queryClient.invalidateQueries({ queryKey: ['bible-imported-book', item.id] });
    } catch (err) {
      if (previous) queryClient.setQueryData(key, previous);
      toast.error(errorText(err, 'Could not change the reading cadence'));
    }
  };

  if (book) {
    if (detail.data) {
      return (
        <ImportedBookReader
          book={book}
          chapters={detail.data.chapters}
          startOrdinal={startOrdinal}
          preferences={preferences}
          onPreferences={onPreferences}
          onClose={() => setBook(null)}
        />
      );
    }
    return (
      <section className="space-y-3" data-testid="bible-devotional-reader">
        <button
          type="button"
          onClick={() => setBook(null)}
          className="flex min-h-11 items-center gap-2 rounded-xl border border-white/5 bg-white/[0.02] px-3 text-xs text-slate-300 transition-colors hover:bg-white/[0.05]"
        >
          ← All devotionals
        </button>
        {detail.isLoading ? (
          <p className="flex items-center gap-2 text-xs text-slate-400">
            <Loader2 size={14} className="animate-spin" /> Opening {book.title}…
          </p>
        ) : (
          <p className="text-xs text-rose-300">{errorText(detail.error, 'Could not open that book')}</p>
        )}
      </section>
    );
  }

  if (devotionals.length === 0) return null;

  return (
    <section className="space-y-2" data-testid="bible-imported-devotionals">
      <h2 className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-400">
        <BookOpen size={16} className="text-amber-300" />
        Devotional books
      </h2>
      <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {devotionals.map((item) => (
          <li
            key={item.id}
            className="rounded-xl border border-white/5 bg-white/[0.02] p-3 transition-colors hover:border-amber-400/30 hover:bg-white/[0.04]"
          >
            <button
              type="button"
              aria-label={`Read ${item.title}`}
              onClick={() => setBook(item)}
              className="flex w-full items-center gap-3 text-left"
            >
              <span className="flex h-11 w-9 shrink-0 items-center justify-center rounded-md border border-amber-400/20 bg-gradient-to-b from-amber-500/20 to-amber-900/10 text-amber-200">
                <BookOpen size={17} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-xs text-slate-200">{item.title}</span>
                <span className="block truncate text-[10px] text-slate-500">
                  {item.author.trim() ? `${item.author.trim()} · ` : ''}
                  {item.chapter_count} readings · {cadenceOf(item) === 'week' ? 'weekly' : 'daily'}
                </span>
              </span>
            </button>
            <div
              role="group"
              aria-label={`Reading cadence for ${item.title}`}
              className="mt-2 flex items-center gap-1 rounded-lg border border-white/5 bg-black/20 p-0.5"
            >
              {(['day', 'week'] as const).map((option) => (
                <button
                  key={option}
                  type="button"
                  aria-pressed={cadenceOf(item) === option}
                  onClick={() => void chooseCadence(item, option)}
                  className={`min-h-11 flex-1 rounded-md px-2 text-[11px] font-medium transition-colors ${
                    cadenceOf(item) === option
                      ? 'bg-amber-500/20 text-amber-200'
                      : 'text-slate-400 hover:bg-white/[0.04] hover:text-slate-200'
                  }`}
                >
                  {option === 'day' ? 'Daily' : 'Weekly'}
                </button>
              ))}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
