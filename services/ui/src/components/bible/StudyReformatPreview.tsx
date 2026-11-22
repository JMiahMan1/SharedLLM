import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Loader2, Wand2 } from 'lucide-react';
import { useState } from 'react';

import { api } from '../../services/api';
import { errorText } from '../../lib/errorText';
import type { StudyReformatPreview as Preview } from '../../types/api';

/**
 * Preview, then apply, re-running the import normaliser on a book.
 *
 * Nothing changes until Apply: the preview shows the sections before and after,
 * a sample of the new layout, how much of the old wording is still there and
 * where each old section (and anyone's place in it) moves to. Used from
 * Admin › Bible and from a book's page actions.
 */
interface Props {
  bookId: number;
  title: string;
  onApplied?: (mapping: Preview['mapping']) => void;
}

const REPORT_LABELS: Array<[string, string]> = [
  ['running_lines_removed', 'running heads and page numbers removed'],
  ['hyphens_joined', 'split words rejoined'],
  ['two_column_pages', 'two-column pages put in order'],
  ['scripture_links', 'scripture references linked'],
  ['questions', 'discussion questions found'],
  ['note_markers_lifted', 'footnote markers lifted out of sentences'],
  ['tables', 'tables kept'],
  ['images', 'images kept'],
];

export default function StudyReformatPreview({ bookId, title, onApplied }: Props) {
  const queryClient = useQueryClient();
  const [assist, setAssist] = useState(false);
  const preview = useMutation({ mutationFn: () => api.previewBibleBookReformat(bookId, assist) });
  const apply = useMutation({
    mutationFn: (token: string) => api.applyBibleBookReformat(bookId, token),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ['bible-imported-book', bookId] });
      void queryClient.invalidateQueries({ queryKey: ['bible-imported-chapter', bookId] });
      void queryClient.invalidateQueries({ queryKey: ['bible-imported-books'] });
      void queryClient.invalidateQueries({ queryKey: ['bible-admin-books'] });
      onApplied?.(result.mapping);
    },
  });
  const data = preview.data;
  const report = (data?.after.report ?? {}) as Record<string, unknown>;

  return (
    <div className="space-y-3 text-sm text-slate-200" data-testid="study-reformat">
      <p className="text-xs leading-relaxed text-slate-400">
        Re-read <span className="text-slate-200">{title}</span> from its original file with the new import cleanup:
        headings, paragraphs, footnotes, questions and scripture links. Nothing changes until you apply it, and
        reading places move with the text.
      </p>
      {!data ? (
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex min-h-11 cursor-pointer items-center gap-2 text-xs text-slate-300">
            <input
              type="checkbox"
              checked={assist}
              onChange={(event) => setAssist(event.target.checked)}
              className="h-4 w-4 accent-amber-500"
            />
            Ask the home model about unclear headings
          </label>
          <button
            type="button"
            onClick={() => preview.mutate()}
            disabled={preview.isPending}
            className="inline-flex min-h-11 items-center gap-2 rounded-xl bg-amber-500/20 px-3 text-sm text-amber-100 hover:bg-amber-500/30 disabled:opacity-60"
            data-testid="study-reformat-preview"
          >
            {preview.isPending ? <Loader2 size={15} className="animate-spin" /> : <Wand2 size={15} />}
            {preview.isPending ? 'Reading the book…' : 'Preview'}
          </button>
        </div>
      ) : null}
      {preview.isError ? <p className="text-xs text-rose-300">{errorText(preview.error, 'The preview failed.')}</p> : null}

      {data ? (
        <div className="space-y-3" data-testid="study-reformat-result">
          <dl className="grid grid-cols-2 gap-2 text-xs">
            <div className="rounded-lg border border-white/10 p-2">
              <dt className="text-slate-500">Now</dt>
              <dd className="text-slate-200">{data.before.sections} sections</dd>
            </div>
            <div className="rounded-lg border border-amber-400/30 p-2">
              <dt className="text-slate-500">After</dt>
              <dd className="text-slate-200">{data.after.sections} sections</dd>
            </div>
          </dl>
          <p className="text-xs text-slate-400" data-testid="study-reformat-retained">
            {Math.round(data.retained * 100)}% of the current wording is kept word for word; the rest is mostly
            running heads, page numbers and split words.
          </p>
          <ul className="space-y-0.5 text-xs text-slate-400">
            {REPORT_LABELS.filter(([key]) => Number(report[key] ?? 0) > 0).map(([key, label]) => (
              <li key={key}>
                {Number(report[key])} {label}
              </li>
            ))}
            {data.assist ? <li>Home model: {data.assist.status}{data.assist.reason ? ` (${data.assist.reason})` : ''}</li> : null}
          </ul>
          <div className="grid gap-2 sm:grid-cols-2">
            <div className="min-w-0 rounded-lg border border-white/10 p-2">
              <p className="mb-1 text-[10px] uppercase tracking-wider text-slate-500">Before: {data.before.sample?.label}</p>
              <p className="line-clamp-[10] whitespace-pre-wrap break-words text-[11px] text-slate-400">{data.before.sample?.text}</p>
            </div>
            <div className="min-w-0 rounded-lg border border-amber-400/30 p-2">
              <p className="mb-1 text-[10px] uppercase tracking-wider text-slate-500">After: {data.after.sample?.label}</p>
              <div className="space-y-1 text-[11px] text-slate-300">
                {(data.after.sample?.blocks ?? []).map((block) => (
                  <p
                    key={block.id}
                    className={`break-words ${block.type === 'heading' ? 'font-semibold text-slate-100' : ''} ${
                      block.type === 'question' ? 'italic' : ''
                    }`}
                  >
                    {block.type === 'list' ? (block.items ?? []).map((item) => `- ${item}`).join('\n') : block.text}
                  </p>
                ))}
              </div>
            </div>
          </div>
          <details className="text-xs text-slate-400">
            <summary className="min-h-11 cursor-pointer py-2">Where each current section goes</summary>
            <ul className="max-h-48 space-y-0.5 overflow-y-auto">
              {data.mapping.map((entry) => (
                <li key={entry.old_ordinal} className="truncate">
                  {entry.old_label || `Section ${entry.old_ordinal}`} → {entry.label}
                </li>
              ))}
            </ul>
          </details>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => apply.mutate(data.token)}
              disabled={apply.isPending || apply.isSuccess}
              className="inline-flex min-h-11 items-center gap-2 rounded-xl bg-amber-500/25 px-3 text-sm text-amber-100 hover:bg-amber-500/35 disabled:opacity-60"
              data-testid="study-reformat-apply"
            >
              {apply.isPending ? <Loader2 size={15} className="animate-spin" /> : null}
              {apply.isSuccess ? 'Reformatted' : 'Apply'}
            </button>
            {!apply.isSuccess ? (
              <button
                type="button"
                onClick={() => preview.reset()}
                className="min-h-11 rounded-xl border border-white/10 px-3 text-sm text-slate-300"
              >
                Back
              </button>
            ) : null}
          </div>
          {apply.isError ? <p className="text-xs text-rose-300">{errorText(apply.error, 'Reformatting failed.')}</p> : null}
        </div>
      ) : null}
    </div>
  );
}
