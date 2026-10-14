import { useState } from 'react';
import { ChevronLeft, X } from 'lucide-react';
import type { BibleBookInfo } from '../../types/api';

/**
 * The Bible as a library: every book as a tile, then every chapter as a tile.
 * Two taps from anywhere to any chapter, the way the Bible App's picker works,
 * instead of scrolling a 66-entry dropdown.
 */
export default function BookPicker({
  books,
  current,
  onPick,
  onClose,
}: {
  books: BibleBookInfo[];
  current: string;
  onPick: (book: string, chapter: number) => void;
  onClose: () => void;
}) {
  const [book, setBook] = useState<BibleBookInfo | null>(null);
  const ordered = [...books].sort((a, b) => a.order - b.order);
  const testaments = [
    { name: 'Old Testament', books: ordered.filter((b) => b.order <= 39) },
    { name: 'New Testament', books: ordered.filter((b) => b.order > 39) },
  ];

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center sm:justify-center" role="dialog" aria-modal="true" aria-label="Choose a book and chapter">
      <button type="button" aria-label="Close" onClick={onClose} className="absolute inset-0 bg-black/60 backdrop-blur-sm" />
      <div className="relative w-full sm:max-w-2xl max-h-[85vh] overflow-y-auto rounded-t-2xl sm:rounded-2xl border border-white/10 bg-slate-950 p-4 pb-[max(1rem,env(safe-area-inset-bottom))]" data-testid="bible-book-picker">
        <div className="mb-3 flex items-center gap-2">
          {book && (
            <button type="button" onClick={() => setBook(null)} aria-label="Back to books" className="min-h-11 min-w-11 flex items-center justify-center rounded-xl text-slate-300">
              <ChevronLeft size={18} />
            </button>
          )}
          <h2 className="flex-1 text-base font-bold text-white">{book ? book.name : 'Books'}</h2>
          <button type="button" onClick={onClose} aria-label="Close" className="min-h-11 min-w-11 flex items-center justify-center rounded-xl text-slate-400">
            <X size={16} />
          </button>
        </div>

        {book ? (
          <div className="grid grid-cols-5 sm:grid-cols-8 gap-2" data-testid="bible-chapter-grid">
            {Array.from({ length: book.chapters }, (_, i) => i + 1).map((n) => (
              <button
                key={n}
                type="button"
                onClick={() => onPick(book.osis, n)}
                className="min-h-12 rounded-xl border border-white/10 bg-white/5 text-sm font-medium text-slate-100 hover:border-amber-400/60"
              >
                {n}
              </button>
            ))}
          </div>
        ) : (
          testaments.map((t) => (
            <div key={t.name} className="mb-4">
              <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-slate-500">{t.name}</h3>
              <div className="grid grid-cols-3 sm:grid-cols-5 gap-2">
                {t.books.map((b) => (
                  <button
                    key={b.osis}
                    type="button"
                    onClick={() => (b.chapters === 1 ? onPick(b.osis, 1) : setBook(b))}
                    data-testid={`bible-pick-${b.osis}`}
                    className={`min-h-12 rounded-xl border px-2 text-sm leading-tight ${
                      b.osis === current ? 'border-amber-400/70 bg-amber-500/15 text-amber-100' : 'border-white/10 bg-white/5 text-slate-100 hover:border-white/30'
                    }`}
                  >
                    {b.name}
                  </button>
                ))}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
