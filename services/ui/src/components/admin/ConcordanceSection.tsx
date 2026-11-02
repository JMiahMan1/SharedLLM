import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, Languages, Loader2, RefreshCw } from 'lucide-react';
import { api } from '../../services/api';
import type { TaggedConcordance } from '../../types/api';

const STATE_STYLE: Record<TaggedConcordance['state'], string> = {
  installed: 'text-emerald-300',
  installing: 'text-sky-300',
  ready: 'text-sky-300',
  failed: 'text-amber-300',
  'needs attention': 'text-amber-300',
};

const STATE_LABEL: Record<TaggedConcordance['state'], string> = {
  installed: 'In the Bible app',
  installing: 'Installing…',
  ready: 'Ready to install',
  failed: 'Install was refused',
  'needs attention': 'Cannot be read',
};

/**
 * Strong's concordances from the family library: every Calibre book tagged
 * "Concordance" or "Strong's". Installing one reads its Hebrew and Greek
 * dictionaries into the lexicon the reader's word study opens; it runs in the
 * background (a scanned concordance takes several minutes) and this list
 * follows it until it is done.
 */
export default function ConcordanceSection({ onFailed }: { onFailed: (error: unknown) => void }) {
  const client = useQueryClient();
  const listing = useQuery({
    queryKey: ['bible-concordances'],
    queryFn: () => api.getConcordances(),
    retry: false,
    refetchInterval: (query) => (query.state.data?.job.state === 'running' ? 5000 : false),
  });
  const install = useMutation({
    mutationFn: (id: number) => api.installConcordance(id),
    onSettled: () => {
      void client.invalidateQueries({ queryKey: ['bible-concordances'] });
      void client.invalidateQueries({ queryKey: ['bible-lexicon-status'] });
    },
    onError: onFailed,
  });

  const data = listing.data;
  const running = data?.job.state === 'running';
  return (
    <section className="space-y-2" data-testid="bible-admin-concordances">
      <div className="flex items-center justify-between gap-2">
        <h3 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-slate-300">
          <Languages size={13} className="text-sky-300" /> Strong&apos;s concordance
        </h3>
        <button
          type="button"
          onClick={() => void listing.refetch()}
          disabled={listing.isFetching}
          aria-label="Check the library for concordances again"
          className="flex min-h-11 min-w-11 items-center justify-center rounded-xl text-slate-400 hover:text-white disabled:opacity-50"
        >
          <RefreshCw size={14} className={listing.isFetching ? 'animate-spin' : ''} />
        </button>
      </div>
      <p className="text-[11px] text-slate-500">
        Tag a Strong&apos;s concordance <span className="text-slate-300">Concordance</span> or{' '}
        <span className="text-slate-300">Strong&apos;s</span> in Calibre and its Hebrew and Greek dictionaries become the
        word study in the reader (tap a verse › Word study).
        {data?.installed.installed
          ? ` Installed: ${data.installed.entries.toLocaleString()} entries, ${data.installed.occurrences.toLocaleString()} verse references.`
          : ''}
      </p>
      {running && (
        <p className="flex items-center gap-2 text-xs text-sky-300" data-testid="bible-concordance-running">
          <Loader2 size={13} className="animate-spin" /> Installing {data?.job.title}; this takes several minutes.
        </p>
      )}
      {data?.job.state === 'failed' && data.job.message && <p className="text-xs text-amber-300/90">{data.job.message}</p>}
      {listing.isLoading ? (
        <p className="flex items-center gap-2 text-xs text-slate-400"><Loader2 size={13} className="animate-spin" /> Reading the library…</p>
      ) : listing.isError ? (
        <p className="text-xs text-amber-300/90">{listing.error instanceof Error ? listing.error.message : 'The library could not be read.'}</p>
      ) : !data?.books.length ? (
        <p className="text-xs text-slate-400">No book in the library is tagged Concordance or Strong&apos;s yet.</p>
      ) : (
        <ul className="space-y-1.5">
          {data.books.map((book) => (
            <li key={book.id} className="rounded-xl border border-white/5 bg-white/[0.03] p-3" data-testid={`bible-concordance-${book.id}`}>
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-slate-100 break-words">{book.title}</p>
                  <p className={`mt-0.5 flex items-center gap-1 text-[11px] ${STATE_STYLE[book.state]}`}>
                    {book.state === 'installed' && <CheckCircle2 size={12} />}
                    {STATE_LABEL[book.state]}
                  </p>
                  {book.reason ? <p className="mt-0.5 text-[11px] text-slate-500">{book.reason}</p> : null}
                </div>
                <button
                  type="button"
                  disabled={running || install.isPending || !book.file}
                  onClick={() => install.mutate(book.id)}
                  data-testid={`bible-concordance-install-${book.id}`}
                  className="glass-button flex min-h-11 shrink-0 items-center gap-1.5 px-3 text-xs disabled:opacity-50"
                >
                  {install.isPending && install.variables === book.id ? <Loader2 size={13} className="animate-spin" /> : null}
                  {book.state === 'installed' ? 'Reinstall' : 'Install'}
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
