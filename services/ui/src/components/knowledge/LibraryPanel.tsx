import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { BookPlus, ClipboardCheck, FileCheck, Library, Loader2, Plus, Search, Tag, Upload, Wand2, X } from 'lucide-react';
import { api } from '../../services/api';
import { errorText } from '../../lib/errorText';
import type { BookInspection, LibraryAuditGroup, LibraryAuditList, LibraryBook } from '../../types/api';

/** Topics the app's features read (Fasting, Bible, Health); offered first when tagging. */
const TOPICS = ['fasting', 'prayer', 'lent', 'bible-study', 'health', 'early-church'];
const PAGE = 50;

function BookTags({ book, isAdmin }: { book: LibraryBook; isAdmin: boolean }) {
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState('');
  const change = useMutation({
    mutationFn: ({ add, remove }: { add: string[]; remove: string[] }) => api.libraryTagBook(book.id, add, remove),
    onSuccess: (out) => {
      toast.success(`Tags saved: ${out.tags.join(', ') || 'none'}`);
      setAdding('');
      void queryClient.invalidateQueries({ queryKey: ['library'] });
    },
    onError: (error) => toast.error(errorText(error, 'The tags could not be saved')),
  });
  return (
    <div className="mt-1 flex flex-wrap items-center gap-1">
      {book.tags.map((tag) => (
        <span key={tag} className="inline-flex items-center gap-1 rounded-full bg-indigo-500/15 px-2 py-0.5 text-[11px] text-indigo-200">
          {tag}
          {isAdmin && (
            <button
              type="button"
              onClick={() => change.mutate({ add: [], remove: [tag] })}
              disabled={change.isPending}
              aria-label={`Remove tag ${tag} from ${book.title}`}
              className="text-indigo-300/70 hover:text-white"
            >
              <X size={11} />
            </button>
          )}
        </span>
      ))}
      {isAdmin && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            const tags = adding.split(',').map((t) => t.trim()).filter(Boolean);
            if (tags.length) change.mutate({ add: tags, remove: [] });
          }}
          className="inline-flex items-center gap-1"
        >
          <input
            value={adding}
            onChange={(event) => setAdding(event.target.value)}
            list="library-tag-list"
            placeholder="+ tag"
            aria-label={`Add a tag to ${book.title}`}
            className="glass-input h-8 w-28 px-2 text-xs"
          />
          {adding.trim() && (
            <button type="submit" disabled={change.isPending} className="glass-button h-8 px-2 text-xs" aria-label="Save tag">
              {change.isPending ? <Loader2 size={12} className="animate-spin" /> : <Plus size={12} />}
            </button>
          )}
        </form>
      )}
      {change.isPending && !adding.trim() ? <Loader2 size={12} className="animate-spin text-slate-500" /> : null}
    </div>
  );
}

function BookRow({ book, isAdmin }: { book: LibraryBook; isAdmin: boolean }) {
  return (
    <li className="rounded-xl border border-white/5 bg-white/[0.02] px-3 py-2" data-testid={`library-book-${book.id}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-sm font-semibold text-slate-100">
          {book.title}
          <span className="font-normal text-slate-400"> · {book.authors.join(' & ') || 'Unknown'}</span>
          {book.series ? <span className="text-[11px] text-slate-500"> · {book.series}{book.series_index ? ` #${book.series_index}` : ''}</span> : null}
        </p>
        <span className={`text-[10px] ${book.readable ? 'text-slate-500' : 'text-amber-300'}`}>
          {book.formats.join(', ') || 'no file'}
          {!book.readable && book.formats.length ? ' · Jarvis sees the title only' : ''}
        </span>
      </div>
      <BookTags book={book} isAdmin={isAdmin} />
    </li>
  );
}

const FIELD_LABELS: Record<string, string> = {
  title: 'title', authors: 'authors', comments: 'description', publisher: 'publisher', published: 'date',
  isbn: 'ISBN', language: 'language', cover: 'cover',
};

/** "title and authors from the file; description and cover from Google Books". */
function sourcesLine(sources: Record<string, string>): string {
  const bySource = new Map<string, string[]>();
  for (const [field, source] of Object.entries(sources)) {
    if (!FIELD_LABELS[field]) continue;
    bySource.set(source, [...(bySource.get(source) ?? []), FIELD_LABELS[field]]);
  }
  return [...bySource.entries()].map(([source, fields]) => `${fields.join(', ')} from ${source}`).join('; ');
}

function splitTags(value: string): string[] {
  return value.split(',').map((t) => t.trim()).filter(Boolean);
}

function AddBook({ onDone }: { onDone: () => void }) {
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [autoFill, setAutoFill] = useState(true);
  const [dragging, setDragging] = useState(false);
  const [fields, setFields] = useState({
    title: '', authors: '', tags: '', series: '', comments: '', publisher: '', published: '', isbn: '', language: '',
  });
  const [found, setFound] = useState<BookInspection | null>(null);
  const set = (key: keyof typeof fields, value: string) => setFields((current) => ({ ...current, [key]: value }));

  const inspect = useMutation({
    mutationFn: (chosen: File) => api.libraryInspectBook(chosen),
    onSuccess: (out) => {
      setFound(out);
      // Everything found is filled in; every field stays editable.
      setFields((current) => ({
        title: out.title || current.title,
        authors: out.authors.join(' & ') || current.authors,
        tags: [...new Set([...splitTags(current.tags), ...out.tags.map((t) => t.name)])].join(', '),
        series: out.series || current.series,
        comments: out.comments || current.comments,
        publisher: out.publisher || current.publisher,
        published: out.published || current.published,
        isbn: out.isbn || current.isbn,
        language: out.language || current.language,
      }));
    },
    onError: (error) => toast.error(errorText(error, 'Jarvis could not look the book up; fill it in by hand')),
  });

  const add = useMutation({
    mutationFn: () => {
      const form = new FormData();
      if (file) form.append('file', file);
      for (const [key, value] of Object.entries(fields)) form.append(key, value);
      if (found?.cover_url && !found.cover_preview) form.append('cover_url', found.cover_url);
      return api.libraryAddBook(form);
    },
    onSuccess: (out) => {
      toast.success(`Added “${out.title}”${out.tags.length ? ` with ${out.tags.join(', ')}` : ''}. Jarvis is indexing it now.`);
      void queryClient.invalidateQueries({ queryKey: ['library'] });
      onDone();
    },
    onError: (error) => toast.error(errorText(error, 'The book could not be added')),
  });

  const choose = (chosen: File | null) => {
    setFile(chosen);
    setFound(null);
    if (chosen && autoFill) inspect.mutate(chosen);
  };
  const tags = splitTags(fields.tags);
  const toggleTag = (name: string) =>
    set('tags', (tags.some((t) => t.toLowerCase() === name.toLowerCase()) ? tags.filter((t) => t.toLowerCase() !== name.toLowerCase()) : [...tags, name]).join(', '));
  const cover = found?.cover_preview || found?.cover_url || '';

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        if (!file) {
          toast.error('Choose the book file first');
          return;
        }
        add.mutate();
      }}
      className="mt-3 space-y-2 rounded-xl border border-indigo-500/20 bg-indigo-950/10 p-3"
      data-testid="library-add"
    >
      <label
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          choose(event.dataTransfer.files?.[0] ?? null);
        }}
        className={`flex min-h-28 cursor-pointer flex-col items-center justify-center gap-1 rounded-xl border-2 border-dashed px-4 py-5 text-center transition focus-within:outline focus-within:outline-2 focus-within:outline-indigo-400 ${
          dragging ? 'border-indigo-400 bg-indigo-500/15' : file ? 'border-emerald-400/40 bg-emerald-500/5' : 'border-indigo-400/40 bg-white/[0.02] hover:border-indigo-400/70 hover:bg-indigo-500/10'
        }`}
        data-testid="library-dropzone"
      >
        {file ? (
          <>
            <FileCheck size={22} className="text-emerald-300" aria-hidden />
            <span className="text-sm font-semibold text-slate-100" data-testid="library-chosen-file">{file.name}</span>
            <span className="text-[11px] text-slate-400">{(file.size / 1_048_576).toFixed(1)} MB · click to choose a different file</span>
          </>
        ) : (
          <>
            <Upload size={22} className="text-indigo-300" aria-hidden />
            <span className="text-sm font-semibold text-slate-100">Choose a book file, or drop it here</span>
            <span className="text-[11px] text-slate-400">EPUB is best: Jarvis can read it, and it brings its own details and cover. PDF, MOBI, AZW3 and others work too.</span>
          </>
        )}
        <input
          type="file"
          accept=".epub,.pdf,.mobi,.azw3,.fb2,.txt,.docx,.rtf,.cbz,.djvu"
          onChange={(event) => choose(event.target.files?.[0] ?? null)}
          className="sr-only"
          aria-label="Book file"
          data-testid="library-add-file"
        />
      </label>
      <div className="flex flex-wrap items-center justify-end gap-3">
        <label className="flex min-h-11 items-center gap-2 text-xs text-slate-300">
          <input type="checkbox" checked={autoFill} onChange={(e) => setAutoFill(e.target.checked)} className="h-4 w-4 accent-indigo-400" />
          Auto-fill and tag
        </label>
        <button
          type="button"
          onClick={() => file && inspect.mutate(file)}
          disabled={!file || inspect.isPending}
          className="glass-button min-h-11 px-3 text-xs disabled:opacity-50"
          data-testid="library-autofill"
        >
          {inspect.isPending ? <Loader2 size={14} className="animate-spin" /> : <Wand2 size={14} />} Look it up
        </button>
      </div>

      {inspect.isPending && <p className="flex items-center gap-2 text-xs text-slate-400"><Loader2 size={13} className="animate-spin" /> Reading the book and looking it up…</p>}
      {found && (
        <p className="text-[11px] text-slate-400" data-testid="library-found">
          {sourcesLine(found.sources) || 'Nothing found; fill it in by hand.'} Check everything before adding.
        </p>
      )}

      <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_7rem]">
        <div className="grid gap-2 sm:grid-cols-2">
          <input value={fields.title} onChange={(e) => set('title', e.target.value)} placeholder="Title" aria-label="Title" className="glass-input min-h-11 px-3 text-base sm:text-sm" data-testid="library-add-title" />
          <input value={fields.authors} onChange={(e) => set('authors', e.target.value)} placeholder="Author(s), separated by &" aria-label="Authors" className="glass-input min-h-11 px-3 text-base sm:text-sm" data-testid="library-add-authors" />
          <input value={fields.series} onChange={(e) => set('series', e.target.value)} placeholder="Series (optional)" aria-label="Series" className="glass-input min-h-11 px-3 text-base sm:text-sm" />
          <input value={fields.publisher} onChange={(e) => set('publisher', e.target.value)} placeholder="Publisher" aria-label="Publisher" className="glass-input min-h-11 px-3 text-base sm:text-sm" />
          <input value={fields.published} onChange={(e) => set('published', e.target.value)} placeholder="Published (e.g. 1969 or 1969-05-01)" aria-label="Published" className="glass-input min-h-11 px-3 text-base sm:text-sm" />
          <div className="grid grid-cols-2 gap-2">
            <input value={fields.isbn} onChange={(e) => set('isbn', e.target.value)} placeholder="ISBN" aria-label="ISBN" className="glass-input min-h-11 px-3 text-base sm:text-sm" />
            <input value={fields.language} onChange={(e) => set('language', e.target.value)} placeholder="Language (en)" aria-label="Language" className="glass-input min-h-11 px-3 text-base sm:text-sm" />
          </div>
        </div>
        {cover ? (
          <img src={cover} alt="Cover" className="hidden aspect-[2/3] w-28 rounded-lg object-cover sm:block" data-testid="library-add-cover" />
        ) : (
          <div className="hidden aspect-[2/3] w-28 items-center justify-center rounded-lg border border-dashed border-white/10 text-[10px] text-slate-500 sm:flex">No cover</div>
        )}
      </div>
      <textarea value={fields.comments} onChange={(e) => set('comments', e.target.value)} placeholder="Description" aria-label="Description" rows={3} className="glass-input w-full resize-y px-3 py-2 text-base sm:text-sm" />
      <input value={fields.tags} onChange={(e) => set('tags', e.target.value)} list="library-tag-list" placeholder="Tags, comma separated (e.g. fasting, lent)" aria-label="Tags" className="glass-input min-h-11 w-full px-3 text-base sm:text-sm" data-testid="library-add-tags" />
      {found && found.tags.length > 0 && (
        <div className="flex flex-wrap gap-1.5" data-testid="library-suggested-tags">
          {found.tags.map((tag) => {
            const on = tags.some((t) => t.toLowerCase() === tag.name.toLowerCase());
            return (
              <button
                key={tag.name}
                type="button"
                onClick={() => toggleTag(tag.name)}
                aria-pressed={on}
                title={tag.why}
                className={`min-h-9 rounded-full border px-3 text-xs ${on ? 'border-indigo-400/60 bg-indigo-500/25 text-white' : 'border-white/10 text-slate-400'}`}
              >
                {tag.name}
                {!tag.existing ? <span className="ml-1 text-[10px] text-amber-300">new</span> : null}
                <span className="ml-1 text-[10px] text-slate-500">· {tag.why}</span>
              </button>
            );
          })}
        </div>
      )}
      <button type="submit" disabled={add.isPending || inspect.isPending} className="glass-button min-h-11 w-full px-4 text-xs font-semibold disabled:opacity-60" data-testid="library-add-submit">
        {add.isPending ? <Loader2 size={14} className="animate-spin" /> : <BookPlus size={14} />} Add to the library
      </button>
    </form>
  );
}

function AuditGroups({ title, groups }: { title: string; groups: LibraryAuditGroup[] }) {
  if (!groups.length) return null;
  return (
    <div>
      <p className="text-xs font-semibold text-slate-200">{title} ({groups.length})</p>
      <p className="text-[11px] text-slate-500">{groups[0].why}</p>
      <ul className="mt-1 space-y-1">
        {groups.map((group) => (
          <li key={group.keep.id} className="text-[12px] text-slate-300">
            Keep <span className="text-emerald-300">{group.keep.title}</span> ({group.keep.formats.join(', ') || 'no file'}, id {group.keep.id}); could remove{' '}
            {group.others.map((b) => `id ${b.id} (${b.formats.join(', ') || 'no file'})`).join(', ')}
          </li>
        ))}
      </ul>
    </div>
  );
}

function AuditList({ title, list }: { title: string; list: LibraryAuditList }) {
  if (!list.count) return null;
  return (
    <details>
      <summary className="cursor-pointer text-xs font-semibold text-slate-200">{title} ({list.count.toLocaleString()})</summary>
      <p className="text-[11px] text-slate-500">{list.why}</p>
      <ul className="mt-1 space-y-0.5 text-[12px] text-slate-300">
        {list.books.map((b) => <li key={b.id}>{b.title} · {b.authors.join(' & ')} (id {b.id})</li>)}
        {list.count > list.books.length ? <li className="text-slate-500">and {list.count - list.books.length} more</li> : null}
      </ul>
    </details>
  );
}

/**
 * The family's Calibre library: find books by words or tag, tag them for the
 * features that read them (Fasting, Bible, Health), add new ones, and audit
 * the shelf for copies and gaps. Adding and tagging are for admins; the
 * audit only suggests, and removing a book is done in Calibre.
 */
export default function LibraryPanel({ isAdmin }: { isAdmin: boolean }) {
  const [query, setQuery] = useState('');
  const [submitted, setSubmitted] = useState('');
  const [tag, setTag] = useState('');
  const [untagged, setUntagged] = useState(false);
  const [page, setPage] = useState(0);
  const [adding, setAdding] = useState(false);
  const [auditing, setAuditing] = useState(false);

  const tags = useQuery({ queryKey: ['library', 'tags'], queryFn: () => api.libraryTags(), staleTime: 60_000, retry: false });
  const books = useQuery({
    queryKey: ['library', 'books', submitted, tag, untagged, page],
    queryFn: () => api.libraryBooks({ q: submitted, tag, untagged, limit: PAGE, offset: page * PAGE }),
    staleTime: 30_000,
    retry: false,
  });
  const audit = useQuery({ queryKey: ['library', 'audit'], queryFn: () => api.libraryAudit(), enabled: auditing, staleTime: 5 * 60_000, retry: false });

  const allTags = (tags.data?.tags ?? []).map((t) => t.name);
  const suggestions = [...new Set([...TOPICS, ...allTags])];
  const total = books.data?.total ?? 0;

  return (
    <section className="glass-panel border-white/5 p-6" data-testid="library-panel">
      <datalist id="library-tag-list">
        {suggestions.map((t) => <option key={t} value={t} />)}
      </datalist>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Library size={16} className="text-indigo-300" />
          <h3 className="font-bold text-indigo-300">Family library</h3>
          <span className="text-[10px] uppercase tracking-widest text-slate-500">Calibre</span>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={() => setAuditing((a) => !a)} aria-pressed={auditing} className="glass-button min-h-11 px-3 text-xs" data-testid="library-audit-button">
            <ClipboardCheck size={14} /> Audit the shelf
          </button>
          {isAdmin && (
            <button type="button" onClick={() => setAdding((a) => !a)} aria-expanded={adding} className="glass-button min-h-11 px-3 text-xs" data-testid="library-add-button">
              <BookPlus size={14} /> Add a book
            </button>
          )}
        </div>
      </div>
      <p className="mt-1 text-xs text-slate-400">
        Tag a book with a topic (fasting, prayer, bible-study, health) and the app's Fasting, Bible and Health pages, and Jarvis, use it as a resource.
      </p>

      {adding && isAdmin && <AddBook onDone={() => setAdding(false)} />}

      {auditing && (
        <div className="mt-3 space-y-3 rounded-xl border border-white/5 bg-black/10 p-3" data-testid="library-audit">
          {audit.isLoading ? (
            <p className="flex items-center gap-2 text-xs text-slate-400"><Loader2 size={13} className="animate-spin" /> Reading the whole shelf…</p>
          ) : audit.isError ? (
            <p className="text-xs text-amber-300">{errorText(audit.error, 'The audit could not be run.')}</p>
          ) : audit.data ? (
            <>
              <p className="text-xs text-slate-400">
                {audit.data.books.toLocaleString()} books. These are suggestions only; nothing is removed. Delete a book in Calibre.
              </p>
              <AuditGroups title="Duplicate copies" groups={audit.data.copies} />
              <AuditGroups title="Other editions of the same book" groups={audit.data.editions} />
              <AuditList title="Records with no book file" list={audit.data.no_file} />
              <AuditList title="Only in a format Jarvis cannot read" list={audit.data.unreadable} />
              <AuditList title="No tags" list={audit.data.untagged} />
            </>
          ) : null}
        </div>
      )}

      <form
        onSubmit={(event) => {
          event.preventDefault();
          setPage(0);
          setSubmitted(query.trim());
        }}
        className="mt-3 flex gap-2"
      >
        <label className="relative min-w-0 flex-1">
          <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" aria-hidden />
          <input value={query} onChange={(e) => setQuery(e.target.value)} type="search" placeholder="Title, author, series or tag" aria-label="Search the library" className="glass-input min-h-11 w-full pl-8 pr-3 text-base sm:text-sm" />
        </label>
        <button type="submit" className="glass-button min-h-11 px-4 text-xs">Find books</button>
      </form>

      <div className="mt-2 flex flex-wrap gap-1.5" role="group" aria-label="Filter by tag">
        <button type="button" onClick={() => { setUntagged(!untagged); setTag(''); setPage(0); }} aria-pressed={untagged} className={`min-h-9 rounded-full border px-3 text-xs ${untagged ? 'border-indigo-400/60 bg-indigo-500/25 text-white' : 'border-white/10 text-slate-300'}`}>
          Untagged
        </button>
        {(tags.data?.tags ?? []).slice(0, 24).map((t) => (
          <button
            key={t.name}
            type="button"
            onClick={() => { setTag(tag === t.name ? '' : t.name); setUntagged(false); setPage(0); }}
            aria-pressed={tag === t.name}
            className={`min-h-9 rounded-full border px-3 text-xs ${tag === t.name ? 'border-indigo-400/60 bg-indigo-500/25 text-white' : 'border-white/10 text-slate-300'}`}
          >
            <Tag size={10} className="mr-1 inline" aria-hidden />{t.name} <span className="text-slate-500">{t.books}</span>
          </button>
        ))}
      </div>

      {books.isLoading ? (
        <p className="mt-3 flex items-center gap-2 text-xs text-slate-400"><Loader2 size={13} className="animate-spin" /> Opening the library…</p>
      ) : books.isError ? (
        <p className="mt-3 text-xs text-amber-300">{errorText(books.error, 'The library could not be read.')}</p>
      ) : (
        <>
          <p className="mt-3 text-[11px] text-slate-500" aria-live="polite">{total.toLocaleString()} book{total === 1 ? '' : 's'}</p>
          <ul className="mt-1 space-y-1.5">
            {(books.data?.books ?? []).map((book) => <BookRow key={book.id} book={book} isAdmin={isAdmin} />)}
          </ul>
          {total > PAGE && (
            <div className="mt-2 flex items-center gap-2 text-xs text-slate-400">
              <button type="button" disabled={page === 0} onClick={() => setPage(page - 1)} className="glass-button min-h-9 px-3 disabled:opacity-40">Previous</button>
              <span>{page * PAGE + 1}-{Math.min(total, (page + 1) * PAGE)} of {total.toLocaleString()}</span>
              <button type="button" disabled={(page + 1) * PAGE >= total} onClick={() => setPage(page + 1)} className="glass-button min-h-9 px-3 disabled:opacity-40">Next</button>
            </div>
          )}
        </>
      )}
    </section>
  );
}
