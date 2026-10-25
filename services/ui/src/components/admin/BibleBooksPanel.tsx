import { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  BookOpen,
  ChevronLeft,
  FileUp,
  Folder,
  HardDrive,
  Loader2,
  Trash2,
  UploadCloud,
} from 'lucide-react';

import { api } from '../../services/api';
import type { BibleBookKind, BibleLibraryEntry } from '../../types/api';
import { errorText } from '../../lib/errorText';

type Source = 'nextcloud' | 'calibre';

const SOURCE_LABELS: Record<Source, string> = {
  nextcloud: 'Nextcloud Books',
  calibre: 'Calibre',
};

/**
 * Admin › Bible › Books: import ordinary PDF/EPUB books for devotionals and
 * study. A book is not a Bible translation, so this panel never asks for a
 * translation code and never runs the 66-book completeness check.
 */
export default function BibleBooksPanel() {
  const client = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [source, setSource] = useState<Source>('nextcloud');
  const [path, setPath] = useState('');
  const [selected, setSelected] = useState<BibleLibraryEntry | null>(null);
  const [title, setTitle] = useState('');
  const [author, setAuthor] = useState('');
  const [kind, setKind] = useState<BibleBookKind>('devotional');
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');

  const books = useQuery({
    queryKey: ['bible-admin-books'],
    queryFn: () => api.getBibleAdminBooks(),
    retry: false,
  });

  const listing = useQuery({
    queryKey: ['bible-admin-books-browse', source, path],
    queryFn: () => api.browseBibleBooks(source, path),
    retry: false,
  });

  const refresh = () => {
    void client.invalidateQueries({ queryKey: ['bible-admin-books'] });
    void client.invalidateQueries({ queryKey: ['bible-admin-books-browse'] });
    void client.invalidateQueries({ queryKey: ['bible-imported-books'] });
    void client.invalidateQueries({ queryKey: ['bible-imported-book'] });
    void client.invalidateQueries({ queryKey: ['bible-books'] });
  };

  const runImport = useMutation({
    mutationFn: () =>
      api.importBibleBook({
        source,
        path: selected?.path ?? '',
        title,
        author,
        kind,
      }),
    onSuccess: (result) => {
      setNotice(`Imported ${result.book.title} (${result.book.chapter_count} sections).`);
      setError('');
      setSelected(null);
      setTitle('');
      setAuthor('');
      refresh();
    },
    onError: (err) => {
      setNotice('');
      setError(errorText(err, 'The book could not be imported.'));
    },
  });

  const runUpload = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append('file', file);
      form.append('kind', kind);
      if (title.trim()) form.append('title', title.trim());
      if (author.trim()) form.append('author', author.trim());
      return api.uploadBibleBook(form);
    },
    onSuccess: (result) => {
      setNotice(`Imported ${result.book.title} (${result.book.chapter_count} sections).`);
      setError('');
      refresh();
    },
    onError: (err) => {
      setNotice('');
      setError(errorText(err, 'The book could not be imported.'));
    },
  });

  const remove = useMutation({
    mutationFn: (id: number) => api.deleteBibleBook(id),
    onSuccess: () => {
      setNotice('Book removed.');
      setError('');
      refresh();
    },
    onError: (err) => setError(errorText(err, 'The book could not be removed.')),
  });

  const entries = listing.data?.entries ?? [];

  return (
    <section className="space-y-3" data-testid="bible-admin-books">
      <header>
        <h3 className="text-sm font-bold text-white flex items-center gap-2">
          <BookOpen size={16} className="text-sky-300" />
          Devotionals and study books
        </h3>
        <p className="mt-1 text-xs text-slate-400">
          Import PDF or EPUB books from Nextcloud Books, the Calibre shelf, or a file on this computer.
          A book is treated as a devotional or a study book, not as a Bible translation.
        </p>
      </header>

      <div role="group" aria-label="Book source" className="flex flex-wrap gap-1">
        {(Object.keys(SOURCE_LABELS) as Source[]).map((item) => (
          <button
            key={item}
            type="button"
            aria-pressed={source === item}
            onClick={() => {
              setSource(item);
              setPath('');
              setSelected(null);
            }}
            className={`min-h-9 rounded-lg border px-2.5 text-[11px] ${
              source === item
                ? 'border-sky-400/50 bg-sky-500/15 text-sky-100'
                : 'border-white/10 bg-white/5 text-slate-400 hover:text-slate-200'
            }`}
          >
            {SOURCE_LABELS[item]}
          </button>
        ))}
      </div>

      <div className="rounded-xl border border-white/5 bg-white/[0.02] p-3 space-y-2">
        <div className="flex items-center gap-2 text-[11px] text-slate-500">
          <Folder size={12} />
          <span className="truncate">{listing.data?.root || '-'}</span>
          <span className="ml-auto">{entries.length} entries</span>
        </div>
        {path ? (
          <button
            type="button"
            onClick={() => setPath(listing.data?.parent ?? '')}
            className="min-h-9 inline-flex items-center gap-1 text-[11px] text-slate-400 hover:text-white"
          >
            <ChevronLeft size={13} /> Up one level
          </button>
        ) : null}
        {listing.isLoading ? (
          <div className="flex items-center gap-2 text-xs text-slate-500">
            <Loader2 size={14} className="animate-spin" /> Reading the shelf…
          </div>
        ) : listing.isError ? (
          <p className="text-xs text-rose-300">{errorText(listing.error, 'Could not read that shelf.')}</p>
        ) : entries.length === 0 ? (
          <p className="text-xs text-slate-500">No folders or books here yet.</p>
        ) : (
          <ul className="max-h-72 overflow-y-auto divide-y divide-white/5">
            {entries.map((entry) => (
              <li key={entry.path} className="flex items-center gap-2 py-1.5">
                <button
                  type="button"
                  onClick={() => {
                    if (entry.is_dir) {
                      setPath(entry.path);
                      setSelected(null);
                    } else if (entry.installable) {
                      setSelected(entry);
                      setTitle(entry.name.replace(/\.(pdf|epub)$/i, ''));
                      setAuthor('');
                      setNotice('');
                      setError('');
                    }
                  }}
                  className="min-w-0 flex-1 text-left"
                >
                  <span className="block truncate text-xs text-slate-200">{entry.name}</span>
                  <span className="block truncate text-[10px] text-slate-500">{entry.note}</span>
                </button>
                {entry.is_dir ? <Folder size={13} className="text-amber-300/70" /> : null}
                {entry.installable ? (
                  <button
                    type="button"
                    onClick={() => {
                      setSelected(entry);
                      setTitle(entry.name.replace(/\.(pdf|epub)$/i, ''));
                      setAuthor('');
                      setNotice('');
                      setError('');
                    }}
                    className="min-h-9 rounded-lg bg-sky-500/20 px-2 text-[11px] text-sky-200"
                  >
                    Choose
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </div>

      {selected ? (
        <div className="rounded-xl border border-sky-400/20 bg-sky-500/5 p-3 space-y-2">
          <p className="text-[11px] text-sky-200 truncate">{selected.path}</p>
          <div className="grid gap-2 sm:grid-cols-2">
            <input
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="Title"
              aria-label="Book title"
              className="min-h-10 rounded-lg bg-slate-800/80 border border-white/10 px-3 text-xs text-slate-100"
            />
            <input
              value={author}
              onChange={(event) => setAuthor(event.target.value)}
              placeholder="Author (optional)"
              aria-label="Book author"
              className="min-h-10 rounded-lg bg-slate-800/80 border border-white/10 px-3 text-xs text-slate-100"
            />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <select
              value={kind}
              onChange={(event) => setKind(event.target.value as BibleBookKind)}
              aria-label="Book kind"
              className="min-h-10 rounded-lg bg-slate-800/80 border border-white/10 px-3 text-xs text-slate-100"
            >
              <option value="devotional">Daily Devotions</option>
              <option value="study">Bible study</option>
            </select>
            <button
              type="button"
              onClick={() => runImport.mutate()}
              disabled={runImport.isPending}
              className="min-h-10 rounded-lg bg-sky-500/20 px-3 text-xs font-medium text-sky-200 disabled:opacity-50 inline-flex items-center gap-1.5"
            >
              {runImport.isPending ? <Loader2 size={13} className="animate-spin" /> : <UploadCloud size={13} />}
              Import this book
            </button>
            <button
              type="button"
              onClick={() => setSelected(null)}
              className="min-h-10 rounded-lg px-3 text-xs text-slate-400"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : null}

      <div className="rounded-xl border border-white/5 bg-white/[0.02] p-3 space-y-2">
        <p className="text-[11px] text-slate-400 flex items-center gap-1.5">
          <HardDrive size={12} /> Manual import
        </p>
        <p className="text-[11px] text-slate-500">
          Choose a PDF or EPUB from this computer, for example a folder on your Desktop.
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={kind}
            onChange={(event) => setKind(event.target.value as BibleBookKind)}
            aria-label="Manual book kind"
            className="min-h-10 rounded-lg bg-slate-800/80 border border-white/10 px-3 text-xs text-slate-100"
          >
            <option value="devotional">Daily Devotions</option>
            <option value="study">Bible study</option>
          </select>
          <input
            ref={fileRef}
            type="file"
            accept=".pdf,.epub,application/pdf,application/epub+zip"
            className="hidden"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) runUpload.mutate(file);
              event.target.value = '';
            }}
          />
          <button
            type="button"
            onClick={() => fileRef.current?.click()}
            disabled={runUpload.isPending}
            className="min-h-10 rounded-lg bg-white/5 px-3 text-xs text-slate-200 disabled:opacity-50 inline-flex items-center gap-1.5"
          >
            {runUpload.isPending ? <Loader2 size={13} className="animate-spin" /> : <FileUp size={13} />}
            Choose PDF/EPUB
          </button>
        </div>
      </div>

      {notice ? <p className="text-xs text-emerald-300">{notice}</p> : null}
      {error ? <p className="text-xs text-rose-300">{error}</p> : null}

      <div>
        <p className="mb-1 text-[11px] uppercase tracking-wider text-slate-500">Imported books</p>
        {books.isLoading ? (
          <p className="text-xs text-slate-500">Loading…</p>
        ) : (books.data?.books ?? []).length === 0 ? (
          <p className="text-xs text-slate-500">No books imported yet.</p>
        ) : (
          <ul className="space-y-1.5">
            {(books.data?.books ?? []).map((book) => (
              <li
                key={book.id}
                className="flex items-center gap-2 rounded-lg border border-white/5 bg-white/[0.02] px-3 py-2"
                data-testid={`bible-book-${book.id}`}
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate text-xs text-slate-200">{book.title}</p>
                  <p className="truncate text-[10px] text-slate-500">
                    {book.author || 'Unknown author'} · {book.kind === 'devotional' ? 'Daily Devotions' : 'Bible study'} ·{' '}
                    {book.chapter_count} sections · {book.source}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => remove.mutate(book.id)}
                  disabled={remove.isPending}
                  aria-label={`Delete ${book.title}`}
                  className="min-h-9 min-w-9 rounded-lg text-rose-300/80 hover:bg-rose-500/10"
                >
                  <Trash2 size={14} className="mx-auto" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
