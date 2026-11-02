import { useState } from 'react';
import { isAxiosError } from 'axios';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, Library, Loader2, RefreshCw } from 'lucide-react';
import { api } from '../../services/api';
import type { BibleImportRun, TaggedBible } from '../../types/api';

const STATE_STYLE: Record<TaggedBible['state'], string> = {
  installed: 'text-emerald-300',
  ready: 'text-sky-300',
  failed: 'text-amber-300',
  'needs attention': 'text-amber-300',
};

/**
 * Bibles from the family library: every Calibre book tagged "Bible". Each
 * shows the translation and study Bible it installs as (worked out from its
 * title), and whether it is in the Bible app yet. The service installs new
 * ones on its own every few hours; Install does one now, and a book whose
 * translation the title does not name can be given one here.
 */
export default function TaggedBiblesSection({
  knownCodes,
  onReport,
  onFailed,
}: {
  knownCodes: string[];
  onReport: (run: BibleImportRun) => void;
  onFailed: (error: unknown) => void;
}) {
  const client = useQueryClient();
  const [codes, setCodes] = useState<Record<number, string>>({});
  const tagged = useQuery({ queryKey: ['bible-tagged'], queryFn: () => api.getTaggedBibles(), retry: false });

  const install = useMutation({
    mutationFn: ({ id, code }: { id: number; code?: string }) => api.installTaggedBible(id, code),
    onSuccess: (run) => {
      onReport(run);
      void client.invalidateQueries({ queryKey: ['bible-tagged'] });
    },
    onError: (error) => {
      // A refusal comes back as the importer's own report: show that, not a status code.
      const data = isAxiosError(error) ? (error.response?.data as BibleImportRun | undefined) : undefined;
      if (data && typeof data === 'object' && 'status' in data && 'message' in data) onReport(data);
      else onFailed(error);
      void client.invalidateQueries({ queryKey: ['bible-tagged'] });
    },
  });

  const books = tagged.data?.books ?? [];
  return (
    <section className="space-y-2" data-testid="bible-admin-tagged">
      <div className="flex items-center justify-between gap-2">
        <h3 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-slate-300">
          <Library size={13} className="text-amber-300" /> In your library
        </h3>
        <button
          type="button"
          onClick={() => void tagged.refetch()}
          disabled={tagged.isFetching}
          aria-label="Check the library again"
          className="flex min-h-11 min-w-11 items-center justify-center rounded-xl text-slate-400 hover:text-white disabled:opacity-50"
        >
          <RefreshCw size={14} className={tagged.isFetching ? 'animate-spin' : ''} />
        </button>
      </div>
      <p className="text-[11px] text-slate-500">
        Tag a book <span className="text-slate-300">Bible</span> in Calibre (Knowledge → Family library) and it is installed
        here {tagged.data ? `within ${tagged.data.auto_sync_hours} hours` : 'automatically'}, with its study notes. Only that
        exact tag counts.
      </p>
      {tagged.isLoading ? (
        <p className="flex items-center gap-2 text-xs text-slate-400"><Loader2 size={13} className="animate-spin" /> Reading the library…</p>
      ) : tagged.isError ? (
        <p className="text-xs text-amber-300/90">{tagged.error instanceof Error ? tagged.error.message : 'The library could not be read.'}</p>
      ) : books.length === 0 ? (
        <p className="text-xs text-slate-400">No book in the library is tagged Bible yet.</p>
      ) : (
        <ul className="space-y-1.5">
          {books.map((book) => {
            const busy = install.isPending && install.variables?.id === book.id;
            const chosen = codes[book.id] ?? book.code ?? '';
            return (
              <li key={book.id} className="rounded-xl border border-white/5 bg-white/[0.03] p-3" data-testid={`bible-tagged-${book.id}`}>
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="text-sm font-semibold text-slate-100">{book.title}</p>
                    <p className="text-[11px] text-slate-400">
                      {book.code ? `${book.code.toUpperCase()}${book.edition && book.edition !== book.code ? ` · ${book.edition_name || book.edition}` : ' · text only'}` : 'Translation not recognised'}
                      {book.format ? ` · ${book.format}` : ''}
                    </p>
                    <p className={`mt-0.5 flex items-center gap-1 text-[11px] ${STATE_STYLE[book.state]}`}>
                      {book.state === 'installed' && <CheckCircle2 size={12} />}
                      {book.state === 'installed' ? 'In the Bible app' : book.state === 'ready' ? 'Will install at the next sync' : book.state === 'failed' ? 'Install was refused' : 'Needs a translation'}
                    </p>
                    {book.reason ? <p className="mt-0.5 text-[11px] text-slate-500">{book.reason}</p> : null}
                  </div>
                  <div className="flex shrink-0 items-center gap-1.5">
                    {!book.code && (
                      <select
                        value={chosen}
                        onChange={(event) => setCodes((current) => ({ ...current, [book.id]: event.target.value }))}
                        aria-label={`Translation of ${book.title}`}
                        className="min-h-11 rounded-xl border border-white/10 bg-black/30 px-2 text-xs text-slate-100"
                      >
                        <option value="">Which translation?</option>
                        {knownCodes.map((code) => <option key={code} value={code}>{code.toUpperCase()}</option>)}
                      </select>
                    )}
                    <button
                      type="button"
                      disabled={install.isPending || !chosen || !book.file}
                      onClick={() => install.mutate({ id: book.id, code: book.code ? undefined : chosen })}
                      data-testid={`bible-tagged-install-${book.id}`}
                      className="glass-button flex min-h-11 items-center gap-1.5 px-3 text-xs disabled:opacity-50"
                    >
                      {busy ? <Loader2 size={13} className="animate-spin" /> : null}
                      {book.state === 'installed' ? 'Reinstall' : 'Install'}
                    </button>
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
