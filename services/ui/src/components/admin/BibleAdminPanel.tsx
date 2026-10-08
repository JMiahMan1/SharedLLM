import { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  BookMarked,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  CloudDownload,
  FileText,
  FileUp,
  Folder,
  History,
  Loader2,
  Star,
} from 'lucide-react';

import { api } from '../../services/api';
import type {
  BibleImportKind,
  BibleImportProviderInfo,
  BibleImportRun,
  BibleRemoteTranslation,
  BibleLibraryEntry,
  BibleLibraryListing,} from '../../types/api';
import { useHaptics } from '../../hooks/useHaptics';

const KIND_LABELS: Record<BibleImportKind, string> = {
  json: 'Plain text (JSON)',
  pdf: 'PDF Bible',
  epub: 'e-book (EPUB)',
};

/**
 * Admin › Bible: what is installed, and how to install more.
 *
 * Three ways in, because the family's sources differ: an online provider for
 * translations we can fetch on demand, a file the admin picks in the browser,
 * and a path already on the server. Every one of them reports refusals in full
 * -- "Exodus is missing from that PDF" is the whole point of running the
 * importer rather than trusting a filename.
 *
 * The file doors prefill their fields from the file name and keep every field
 * editable: a suggestion the operator can correct beats a blank form, and a
 * value the operator typed is never overwritten by a later suggestion. The
 * known-code list comes from the catalogue so a code the manifest will refuse
 * is visible before the run, not only in the refusal log.
 */
export default function BibleAdminPanel() {
  const { trigger } = useHaptics();
  const client = useQueryClient();
  const [notice, setNotice] = useState('');
  const [noticeKind, setNoticeKind] = useState<'success' | 'error'>('success');
  const [log, setLog] = useState<string[]>([]);

  const catalogue = useQuery({
    queryKey: ['bible-imports'],
    queryFn: () => api.getBibleImports(),
    retry: false,
  });

  function report(run: BibleImportRun) {
    setNotice(run.message);
    setNoticeKind(run.status === 'failed' ? 'error' : 'success');
    setLog(run.log ?? []);
    void trigger(run.status === 'failed' ? 'error' : 'success');
    void client.invalidateQueries({ queryKey: ['bible-imports'] });
    void client.invalidateQueries({ queryKey: ['bible-versions'] });
  }

  function failed(error: unknown) {
    const text = error instanceof Error ? error.message : 'The import did not run.';
    setNotice(text);
    setNoticeKind('error');
    void trigger('error');
  }

  const runImport = useMutation({
    mutationFn: (payload: Parameters<typeof api.runBibleImport>[0]) => api.runBibleImport(payload),
    onSuccess: report,
    onError: failed,
  });

  const uploadImport = useMutation({
    mutationFn: (form: FormData) => api.uploadBibleImport(form),
    onSuccess: report,
    onError: failed,
  });

  const data = catalogue.data;
  const knownCodes = (data?.versions ?? []).map((version) => version.code);

  return (
    <div className="space-y-6" data-testid="bible-admin">
      <header>
        <h2 className="text-lg font-bold text-white flex items-center gap-2">
          <BookMarked size={18} className="text-amber-300" />
          Bible translations
        </h2>
        <p className="text-xs text-slate-400 mt-1">
          Reading defaults to{' '}
          <span className="text-slate-200">{data?.primary || 'nothing yet'}</span>
          {data?.default_version && data.default_version !== data.primary ? (
            <span className="text-amber-300/90">
              {' '}— not installed, so readers get {data.default_version} until it is.
            </span>
          ) : null}
        </p>
        {data?.import_dir_error ? (
          <p
            className="mt-2 text-xs text-amber-300/90 flex items-start gap-1.5"
            data-testid="bible-admin-import-dir-error"
          >
            <AlertTriangle size={13} className="shrink-0 mt-0.5" />
            {data.import_dir_error}
          </p>
        ) : null}
      </header>

      {catalogue.isError ? (
        <PanelError
          message={catalogue.error instanceof Error ? catalogue.error.message : 'Could not read the catalogue.'}
          onRetry={() => void catalogue.refetch()}
        />
      ) : null}

      <section className="space-y-2">
        <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider">Installed</h3>
        {(data?.versions ?? []).length === 0 ? (
          <p className="text-sm text-slate-500">Nothing is installed yet.</p>
        ) : (
          <ul className="space-y-1.5">
            {(data?.versions ?? []).map((version) => (
              <li
                key={version.code}
                className="rounded-xl border border-white/5 bg-white/[0.02] px-3 py-2.5"
                data-testid={`bible-admin-version-${version.code}`}
              >
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-sm text-white font-medium">{version.name}</span>
                  {version.primary ? (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-300 flex items-center gap-1">
                      <Star size={9} /> Primary
                    </span>
                  ) : null}
                  <span className="text-[11px] text-slate-500">{version.code}</span>
                  <span
                    className={
                      version.installed
                        ? 'ml-auto text-[11px] text-emerald-300 flex items-center gap-1'
                        : 'ml-auto text-[11px] text-slate-500'
                    }
                  >
                    {version.installed ? (
                      <>
                        <CheckCircle2 size={11} />
                        {(version.verse_count ?? 0).toLocaleString()} verses
                        {version.editions > 0 ? ` · ${version.editions} study ${version.editions === 1 ? 'Bible' : 'Bibles'}` : ''}
                      </>
                    ) : (
                      'Not installed'
                    )}
                  </span>
                </div>
                {version.installed && version.editions > 0 ? (
                  <ul className="mt-1 flex flex-wrap gap-1.5">
                    {(data?.editions ?? [])
                      .filter((edition) => edition.version === version.code)
                      .map((edition) => (
                        <li
                          key={edition.code}
                          className="text-[10px] px-1.5 py-0.5 rounded bg-white/5 text-slate-400"
                        >
                          {edition.name} · {edition.note_count.toLocaleString()} notes
                        </li>
                      ))}
                  </ul>
                ) : null}
                {version.note ? (
                  <p className="mt-1 text-[11px] text-slate-500">{version.note}</p>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </section>

      <ProviderSection
        providers={data?.providers ?? []}
        busy={runImport.isPending}
        onInstall={(payload) => runImport.mutate(payload)}
      />

      <LibrarySection
        root={data?.library_root ?? ''}
        setting={data?.library_setting ?? 'calibre_library_path'}
        error={data?.library_error ?? ''}
        busy={runImport.isPending}
        knownCodes={knownCodes}
        onInstall={(payload) => runImport.mutate(payload)}
      />

      <UploadSection
        kinds={data?.kinds ?? ['json', 'pdf', 'epub']}
        busy={uploadImport.isPending}
        knownCodes={knownCodes}
        onUpload={(form) => uploadImport.mutate(form)}
      />

      <PathSection
        busy={runImport.isPending}
        knownCodes={knownCodes}
        onInstall={(payload) => runImport.mutate(payload)}
      />

      {notice ? (
        <div
          data-testid="bible-admin-notice"
          data-notice={noticeKind}
          className={
            noticeKind === 'error'
              ? 'rounded-xl border border-amber-500/30 bg-amber-500/5 px-3 py-2.5 text-xs text-amber-200'
              : 'rounded-xl border border-emerald-500/30 bg-emerald-500/5 px-3 py-2.5 text-xs text-emerald-200'
          }
        >
          <p className="flex items-start gap-1.5">
            {noticeKind === 'error' ? (
              <AlertTriangle size={13} className="shrink-0 mt-0.5" />
            ) : (
              <CheckCircle2 size={13} className="shrink-0 mt-0.5" />
            )}
            {notice}
          </p>
          {log.length ? (
            <pre
              className="mt-2 max-h-48 overflow-auto text-[10px] leading-relaxed whitespace-pre-wrap text-slate-400 custom-scrollbar"
              data-testid="bible-admin-log"
            >
              {log.join('\n')}
            </pre>
          ) : null}
        </div>
      ) : null}

      <HistorySection runs={data?.runs ?? []} />
    </div>
  );
}

function ProviderSection({
  providers,
  busy,
  onInstall,
}: {
  providers: BibleImportProviderInfo[];
  busy: boolean;
  onInstall: (payload: Parameters<typeof api.runBibleImport>[0]) => void;
}) {
  if (providers.length === 0) return null;
  return (
    <section className="space-y-2">
      <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-1.5">
        <CloudDownload size={13} /> Online providers
      </h3>
      {providers.map((provider) => (
        <ProviderCard key={provider.code} provider={provider} busy={busy} onInstall={onInstall} />
      ))}
    </section>
  );
}

function ProviderRow({
  provider,
  item,
  busy,
  onInstall,
}: {
  provider: BibleImportProviderInfo;
  item: BibleRemoteTranslation;
  busy: boolean;
  onInstall: (payload: Parameters<typeof api.runBibleImport>[0]) => void;
}) {
  /**
   * The cost is asked for, not discovered afterwards.
   *
   * api.bible allows roughly 5,000 requests a month and a whole Bible is about
   * 1,189 of them, so a translation that is already cached costs nothing while an
   * uncached one can take a month with it. Nobody should have to learn that by
   * pressing the button, so the number sits in front of the button and the
   * button refuses itself when the run is over budget.
   */
  const [asking, setAsking] = useState(false);
  const cost = useQuery({
    queryKey: ['bible-provider-estimate', provider.code, item.id],
    queryFn: () => api.getBibleProviderEstimate(provider.code, item.id),
    retry: false,
    enabled: asking,
  });

  const payload = {
    code: item.id.toLowerCase().replace(/[^a-z0-9]+/g, '-'),
    kind: 'json' as const,
    name: item.name,
    provider: provider.code,
    provider_id: item.id,
  };
  const overBudget = cost.data ? !cost.data.within_budget : false;

  return (
    <li className="space-y-1.5" data-testid={`bible-admin-translation-${item.id}`}>
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs text-slate-300 truncate">{item.name}</span>
        <span className="text-[10px] text-slate-500">{item.language}</span>
        <button
          type="button"
          onClick={() => setAsking(true)}
          disabled={cost.isFetching}
          className="ml-auto min-h-11 px-2.5 rounded-xl text-[11px] bg-white/5 text-slate-300 hover:bg-white/10 disabled:opacity-60"
          data-testid={`bible-admin-cost-${item.id}`}
        >
          {cost.isFetching ? <Loader2 size={12} className="animate-spin" /> : null}
          What will this cost?
        </button>
        <button
          type="button"
          disabled={busy || overBudget}
          onClick={() => onInstall(payload)}
          className="min-h-11 px-3 rounded-xl text-[11px] bg-sky-500/15 text-sky-200 hover:bg-sky-500/25 disabled:opacity-60"
        >
          Install
        </button>
      </div>

      {cost.data ? (
        <p
          className={`text-[11px] ${cost.data.remaining === 0 ? 'text-emerald-300/90' : 'text-amber-300/90'}`}
          data-testid={`bible-admin-cost-report-${item.id}`}
        >
          {cost.data.remaining === 0
            ? `Already cached: all ${cost.data.chapters.toLocaleString()} chapters are on disk, so this costs 0 requests.`
            : `${cost.data.remaining.toLocaleString()} of ${cost.data.chapters.toLocaleString()} chapters are not cached, so this needs ${cost.data.remaining.toLocaleString()} requests.`}
          {cost.data.budget !== null
            ? overBudget
              ? ` The limit for one run is ${cost.data.budget.toLocaleString()}, so this would be refused.`
              : ` The limit for one run is ${cost.data.budget.toLocaleString()}.`
            : ' There is no request limit configured.'}
          {cost.data.cache_enabled ? null : ` ${cost.data.cache_warning ?? ''}`}
        </p>
      ) : null}

      {cost.data ? (
        <button
          type="button"
          disabled={busy}
          onClick={() => onInstall({ ...payload, dry_run: true })}
          className="min-h-11 px-2.5 rounded-xl text-[11px] bg-white/5 text-slate-300 hover:bg-white/10 disabled:opacity-60"
          data-testid={`bible-admin-dry-run-${item.id}`}
        >
          Test one book first
        </button>
      ) : null}
    </li>
  );
}

function ProviderCard({
  provider,
  busy,
  onInstall,
}: {
  provider: BibleImportProviderInfo;
  busy: boolean;
  onInstall: (payload: Parameters<typeof api.runBibleImport>[0]) => void;
}) {
  const [code, setCode] = useState('');
  const available = useQuery({
    queryKey: ['bible-provider-translations', provider.code],
    queryFn: () => api.getBibleProviderTranslations(provider.code),
    retry: false,
    enabled: provider.configured && code === 'open',
  });

  if (!provider.configured) {
    return (
      <div
        className="rounded-xl border border-amber-500/20 bg-amber-500/[0.04] px-3 py-2.5"
        data-testid={`bible-admin-provider-${provider.code}`}
      >
        <p className="text-sm text-slate-200">{provider.title}</p>
        <p className="mt-1 text-[11px] text-amber-300/90">{provider.reason}</p>
      </div>
    );
  }

  const translations = available.data?.translations ?? [];

  return (
    <div
      className="rounded-xl border border-white/5 bg-white/[0.02] px-3 py-2.5 space-y-2"
      data-testid={`bible-admin-provider-${provider.code}`}
    >
      <div className="flex items-center gap-2 flex-wrap">
        <p className="text-sm text-slate-200">{provider.title}</p>
        <button
          type="button"
          onClick={() => {
            setCode((previous) => (previous === 'open' ? '' : 'open'));
          }}
          disabled={available.isFetching}
          data-testid={`bible-admin-provider-open-${provider.code}`}
          className="ml-auto min-h-11 px-3 rounded-xl text-xs bg-white/5 text-slate-200 hover:bg-white/10 disabled:opacity-60 flex items-center gap-1.5"
        >
          {available.isFetching ? <Loader2 size={13} className="animate-spin" /> : null}
          {code === 'open' ? 'Hide translations' : 'Show translations'}
        </button>
      </div>
      {provider.note ? <p className="text-[11px] text-slate-500">{provider.note}</p> : null}

      {available.isError ? (
        <p className="text-[11px] text-amber-300/90" data-testid={`bible-admin-provider-error-${provider.code}`}>
          {available.error instanceof Error ? available.error.message : 'The provider could not be reached.'}
        </p>
      ) : null}

      {code === 'open' && !available.isError ? (
        translations.length === 0 && !available.isFetching ? (
          <p className="text-[11px] text-slate-500">That provider returned no translations.</p>
        ) : (
          <ul className="space-y-2 max-h-72 overflow-auto custom-scrollbar">
            {translations.map((item) => (
              <ProviderRow
                key={item.id}
                provider={provider}
                item={item}
                busy={busy}
                onInstall={onInstall}
              />
            ))}
          </ul>
        )
      ) : null}
    </div>
  );
}

/**
 * The Nextcloud book library: the shelf the family already keeps study Bibles on.
 *
 * Nothing is listed until this section is opened, because a shelf can hold
 * thousands of entries and an admin panel should not walk one the operator did
 * not ask about. Files in formats the importer cannot read are shown with the
 * reason rather than hidden, so a shelf holding one unusable DOCX does not look
 * like a shelf with a hole in it.
 */
function LibrarySection({
  root,
  setting,
  error,
  busy,
  knownCodes,
  onInstall,
}: {
  root: string;
  setting: string;
  error: string;
  busy: boolean;
  knownCodes: string[];
  onInstall: (payload: {
    code: string;
    kind: BibleImportKind;
    library_path: string;
    name?: string;
    edition?: string;
    edition_name?: string;
    import_notes?: boolean;
  }) => void;
}) {
  const [open, setOpen] = useState(false);
  const [path, setPath] = useState('');
  const [confirming, setConfirming] = useState<BibleLibraryEntry | null>(null);
  const [form, setForm] = useState<InstallForm>(freshForm);

  const set = (key: InstallFieldKey, value: string | boolean) =>
    setForm((previous) => setField(previous, key, value));

  const browse = useQuery({
    queryKey: ['bible-library', path],
    queryFn: () => api.getBibleLibrary(path),
    enabled: open && Boolean(root),
    retry: false,
    staleTime: 60 * 1000,
  });

  // The listing is the only place a folder path comes from, so keep what the
  // server answered rather than a second copy of it in this component.
  const shown = (browse.data as BibleLibraryListing | undefined) ?? null;
  const folders = (shown?.entries ?? []).filter((entry) => entry.is_dir);
  const files = (shown?.entries ?? []).filter((entry) => !entry.is_dir);

  /**
   * Installing from the shelf asks first instead of firing immediately.
   *
   * The old one-click install could not set the edition or the study-notes
   * flag at all, so a commentary off the shelf had no way to be installed from
   * here -- and a misclick started a multi-megabyte fetch with the file name
   * as the translation code. The form arrives prefilled from the file name
   * and every field stays editable.
   */
  function beginInstall(entry: BibleLibraryEntry) {
    setConfirming(entry);
    setForm(suggest(freshForm(), suggestionFor(entry.name)));
  }

  function confirmInstall() {
    if (!confirming?.kind || !form.code.trim()) return;
    onInstall({
      code: form.code.trim(),
      kind: confirming.kind,
      library_path: confirming.path,
      ...extrasOf(form),
    });
    setConfirming(null);
  }

  if (!open) {
    return (
      <section className="space-y-2">
        <button
          type="button"
          data-testid="bible-admin-library-open"
          onClick={() => setOpen(true)}
          disabled={!root}
          className="flex min-h-11 w-full items-center justify-between gap-2 rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-left text-xs text-slate-300 disabled:opacity-50"
        >
          <span className="flex items-center gap-1.5">
            <BookMarked size={13} className="text-slate-400" />
            Nextcloud book library
          </span>
          <span className="text-[10px] text-slate-500">
            {root ? `Browse ${root}` : `${setting} is not set`}
          </span>
        </button>
        {error ? (
          <p
            data-testid="bible-admin-library-error"
            className="px-1 text-[11px] leading-relaxed text-amber-300/90"
          >
            {error}
          </p>
        ) : null}
      </section>
    );
  }

  return (
    <section className="space-y-2" data-testid="bible-admin-library">
      <div className="flex items-center justify-between gap-2">
        <h3 className="flex items-center gap-1.5 text-xs font-semibold text-slate-200">
          <BookMarked size={13} className="text-slate-400" />
          Nextcloud book library
        </h3>
        <button
          type="button"
          data-testid="bible-admin-library-close"
          onClick={() => setOpen(false)}
          className="min-h-11 px-2 text-[11px] text-slate-400"
        >
          Close
        </button>
      </div>

      <p className="px-1 text-[11px] leading-relaxed text-slate-400">
        Files are fetched from the shelf and then installed exactly as if they had been uploaded,
        so a study Bible keeps its notes either way.
      </p>

      {browse.isError ? (
        <PanelError
          message={String((browse.error as { message?: string })?.message ?? browse.error)}
          onRetry={() => void browse.refetch()}
        />
      ) : null}

      {browse.isPending ? (
        <div className="space-y-1.5" data-testid="bible-admin-library-loading">
          {[0, 1, 2].map((row) => (
            <div
              key={row}
              className="h-9 animate-pulse rounded-lg bg-white/5"
              style={{ animationDelay: `${row * 120}ms` }}
            />
          ))}
        </div>
      ) : null}

      {shown ? (
        <div className="space-y-1.5">
          <div className="flex items-center gap-1.5 text-[10px] text-slate-500">
            {shown.parent ? (
              <button
                type="button"
                data-testid="bible-admin-library-up"
                onClick={() => setPath(shown.parent)}
                className="flex min-h-11 items-center gap-1 rounded-lg border border-white/10 px-2 text-slate-300"
              >
                <ChevronLeft size={13} />
                Up one level
              </button>
            ) : null}
            <span className="truncate">{shown.root}</span>
            <span className="ml-auto shrink-0">
              {shown.installable} of {shown.count} ready to install
            </span>
          </div>

          {folders.map((entry) => (
            <button
              key={entry.path}
              type="button"
              data-testid={`bible-admin-library-folder-${entry.name}`}
              onClick={() => setPath(entry.path)}
              className="flex min-h-11 w-full items-center gap-2 rounded-lg border border-white/10 bg-white/5 px-3 text-left text-xs text-slate-300"
            >
              <Folder size={13} className="shrink-0 text-slate-500" />
              <span className="truncate">{entry.name}</span>
              <ChevronRight size={13} className="ml-auto shrink-0 text-slate-500" />
            </button>
          ))}

          {files.map((entry) => (
            <div key={entry.path} className="space-y-1.5">
              <div
                data-testid={`bible-admin-library-file-${entry.name}`}
                className="flex min-h-11 items-center gap-2 rounded-lg border border-white/10 bg-white/5 px-3 text-xs"
              >
                <FileText size={13} className="shrink-0 text-slate-500" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-slate-300">{entry.name}</span>
                  <span className="block truncate text-[10px] text-slate-500">{entry.note}</span>
                </span>
                <button
                  type="button"
                  data-testid={`bible-admin-library-install-${entry.name}`}
                  disabled={!entry.installable || busy}
                  onClick={() => entry.kind && beginInstall(entry)}
                  className="min-h-11 shrink-0 rounded-lg border border-white/10 px-2 text-[11px] text-emerald-300 disabled:opacity-40"
                >
                  Install
                </button>
              </div>
              {confirming?.path === entry.path ? (
                <div
                  className="rounded-xl border border-amber-500/20 bg-amber-500/[0.04] px-3 py-2.5 space-y-2"
                  data-testid="bible-admin-library-confirm"
                >
                  <p className="text-[11px] leading-relaxed text-slate-400">
                    Names are suggested from the file and every field is editable. The
                    translation code must be one the manifest knows, or the run is refused
                    with the list of codes that would work.
                  </p>
                  <InstallFields
                    idPrefix="bible-admin-library"
                    form={form}
                    set={set}
                    knownCodes={knownCodes}
                  />
                  <div className="flex gap-2">
                    <button
                      type="button"
                      data-testid="bible-admin-library-confirm-go"
                      disabled={busy || !form.code.trim()}
                      onClick={confirmInstall}
                      className="flex-1 min-h-11 rounded-xl text-xs font-medium bg-amber-500/20 text-amber-200 hover:bg-amber-500/30 disabled:opacity-50"
                    >
                      Install this file
                    </button>
                    <button
                      type="button"
                      data-testid="bible-admin-library-cancel"
                      onClick={() => setConfirming(null)}
                      className="min-h-11 px-3 rounded-xl text-xs bg-white/5 text-slate-300 hover:bg-white/10"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              ) : null}
            </div>
          ))}

          {!folders.length && !files.length ? (
            <p className="px-1 text-[11px] text-slate-500">
              Nothing in this folder. Use Up one level to go back.
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function UploadSection({
  kinds,
  busy,
  knownCodes,
  onUpload,
}: {
  kinds: BibleImportKind[];
  busy: boolean;
  knownCodes: string[];
  onUpload: (form: FormData) => void;
}) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [form, setForm] = useState<InstallForm>(freshForm);

  const set = (key: InstallFieldKey, value: string | boolean) =>
    setForm((previous) => setField(previous, key, value));

  function onFile(next: File | null) {
    setFile(next);
    if (next) setForm((previous) => suggest(previous, suggestionFor(next.name)));
  }

  function submit() {
    if (!file || !form.code.trim()) return;
    const payload = new FormData();
    payload.append('file', file);
    payload.append('code', form.code.trim());
    payload.append('kind', kindOf(file.name, kinds));
    const extras = extrasOf(form);
    if (extras.name) payload.append('name', extras.name);
    if (extras.edition) payload.append('edition', extras.edition);
    if (extras.edition_name) payload.append('edition_name', extras.edition_name);
    payload.append('import_notes', form.notes ? 'true' : 'false');
    onUpload(payload);
  }

  const detected = file ? kindOf(file.name, kinds) : '';

  return (
    <section className="space-y-2">
      <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-1.5">
        <FileUp size={13} /> Upload a Bible file
      </h3>
      <div className="rounded-xl border border-white/5 bg-white/[0.02] px-3 py-2.5 space-y-2">
        <label htmlFor="bible-admin-upload" className="block text-[11px] text-slate-400">
          PDF or EPUB from this device
        </label>
        <input
          id="bible-admin-upload"
          ref={inputRef}
          type="file"
          accept=".pdf,.epub,.json"
          data-testid="bible-admin-upload-file"
          onChange={(event) => onFile(event.target.files?.[0] ?? null)}
          className="block w-full min-h-11 text-xs text-slate-300 file:mr-3 file:min-h-11 file:rounded-lg file:border-0 file:bg-white/10 file:px-3 file:text-xs file:text-slate-200"
        />
        {file && detected ? (
          <p className="text-[10px] text-slate-500" data-testid="bible-admin-upload-kind">
            {file.name} will install as {KIND_LABELS[detected] ?? detected}.
          </p>
        ) : null}
        <InstallFields
          idPrefix="bible-admin-upload"
          form={form}
          set={set}
          knownCodes={knownCodes}
        />
        <button
          type="button"
          onClick={submit}
          disabled={busy || !file || !form.code.trim()}
          data-testid="bible-admin-upload-go"
          className="w-full min-h-11 rounded-xl text-xs font-medium bg-amber-500/20 text-amber-200 hover:bg-amber-500/30 disabled:opacity-50 flex items-center justify-center gap-1.5"
        >
          {busy ? <Loader2 size={13} className="animate-spin" /> : <FileUp size={13} />}
          {busy ? 'Reading the file…' : 'Install this file'}
        </button>
        <p className="text-[10px] text-slate-500">
          {Object.entries(KIND_LABELS)
            .filter(([kind]) => kinds.includes(kind as BibleImportKind))
            .map(([, label]) => label)
            .join(' · ')}
        </p>
      </div>
    </section>
  );
}

function PathSection({
  busy,
  knownCodes,
  onInstall,
}: {
  busy: boolean;
  knownCodes: string[];
  onInstall: (payload: Parameters<typeof api.runBibleImport>[0]) => void;
}) {
  const [path, setPath] = useState('');
  const [kind, setKind] = useState<BibleImportKind>('pdf');
  const [kindTouched, setKindTouched] = useState(false);
  const [form, setForm] = useState<InstallForm>(freshForm);

  const set = (key: InstallFieldKey, value: string | boolean) =>
    setForm((previous) => setField(previous, key, value));

  /**
   * The path is the only fact here, so everything else is derived from it.
   *
   * The file name carries the translation code and, for a study Bible, the
   * notes name; the extension picks the file type. All of it is a suggestion:
   * a value the operator has typed is left alone, and the kind select stops
   * following the extension the moment it is touched by hand.
   */
  function onPath(value: string) {
    setPath(value);
    const base = value.split('/').pop() ?? '';
    if (base) setForm((previous) => suggest(previous, suggestionFor(base)));
    if (!kindTouched) {
      const suffix = base.split('.').pop()?.toLowerCase() ?? '';
      const match = ['json', 'pdf', 'epub'].find((option) => option === suffix);
      if (match) setKind(match as BibleImportKind);
    }
  }

  function submit() {
    if (!form.code.trim() || !path) return;
    onInstall({ code: form.code.trim(), kind, source_path: path, ...extrasOf(form) });
  }

  return (
    <section className="space-y-2">
      <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider">From a file on the server</h3>
      <div className="rounded-xl border border-white/5 bg-white/[0.02] px-3 py-2.5 space-y-2">
        <Field
          label="Path on the server"
          id="bible-admin-path-file"
          value={path}
          onChange={onPath}
          placeholder="/data/bible/esv.pdf"
          testId="bible-admin-path-file"
        />
        <InstallFields
          idPrefix="bible-admin-path"
          form={form}
          set={set}
          knownCodes={knownCodes}
        />
        <label htmlFor="bible-admin-path-kind" className="block text-[11px] text-slate-400">
          File type
        </label>
        <select
          id="bible-admin-path-kind"
          data-testid="bible-admin-path-kind"
          value={kind}
          onChange={(event) => {
            setKindTouched(true);
            setKind(event.target.value as BibleImportKind);
          }}
          className="w-full min-h-11 rounded-xl bg-slate-800/80 border border-white/10 px-3 text-xs text-slate-200"
        >
          <option value="pdf">PDF Bible</option>
          <option value="epub">e-book (EPUB)</option>
          <option value="json">Plain text (JSON)</option>
        </select>
        <button
          type="button"
          disabled={busy || !form.code.trim() || !path}
          data-testid="bible-admin-path-go"
          onClick={submit}
          className="w-full min-h-11 rounded-xl text-xs font-medium bg-white/5 text-slate-200 hover:bg-white/10 disabled:opacity-50"
        >
          Install from this path
        </button>
      </div>
    </section>
  );
}

function HistorySection({ runs }: { runs: BibleImportRun[] }) {
  if (runs.length === 0) return null;
  return (
    <section className="space-y-2">
      <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-1.5">
        <History size={13} /> Import history
      </h3>
      <ul className="space-y-1.5">
        {runs.map((run, index) => (
          <li
            key={`${run.created_at}-${index}`}
            className="rounded-xl border border-white/5 bg-white/[0.02] px-3 py-2"
            data-testid={`bible-admin-run-${run.status}`}
          >
            <div className="flex items-center gap-2 flex-wrap">
              <span
                className={
                  run.status === 'failed'
                    ? 'text-[10px] px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-300'
                    : 'text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-300'
                }
              >
                {run.status}
              </span>
              <span className="text-xs text-slate-200">{run.code || run.name || run.source}</span>
              <span className="text-[10px] text-slate-500 ml-auto">
                {run.verse_count ? `${run.verse_count.toLocaleString()} verses` : run.kind}
              </span>
            </div>
            <p className="mt-1 text-[11px] text-slate-500">{run.message}</p>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Field({
  label,
  id,
  value,
  onChange,
  placeholder,
  testId,
  list,
}: {
  label: string;
  id: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  testId: string;
  list?: string;
}) {
  return (
    <div>
      <label htmlFor={id} className="block text-[11px] text-slate-400">
        {label}
      </label>
      <input
        id={id}
        data-testid={testId}
        value={value}
        placeholder={placeholder}
        list={list}
        onChange={(event) => onChange(event.target.value)}
        className="mt-1 w-full min-h-11 rounded-xl bg-slate-800/80 border border-white/10 px-3 text-xs text-slate-200 placeholder:text-slate-600"
      />
    </div>
  );
}

function PanelError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="rounded-xl border border-amber-500/30 bg-amber-500/5 px-3 py-2.5 text-xs text-amber-200">
      <p>{message}</p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-2 min-h-11 px-3 rounded-xl bg-white/10 text-amber-100"
      >
        Try again
      </button>
    </div>
  );
}

function kindOf(filename: string, kinds: BibleImportKind[]): BibleImportKind {
  const suffix = filename.split('.').pop()?.toLowerCase() ?? '';
  const match = kinds.find((kind) => kind === suffix);
  return match ?? 'pdf';
}

/**
 * The shared install form: what every file door asks before installing.
 *
 * `touched` records which fields a person has edited. Suggestions only ever
 * fill untouched fields, and a suggestion keeps its field untouched so a
 * better one (a corrected file name, a re-picked file) can refine it -- the
 * rule that keeps prefills helpful without letting them stomp typing.
 */
type InstallFieldKey = 'code' | 'name' | 'edition' | 'editionName' | 'notes';

interface InstallForm {
  code: string;
  name: string;
  edition: string;
  editionName: string;
  notes: boolean;
  touched: Record<InstallFieldKey, boolean>;
}

function freshForm(): InstallForm {
  return {
    code: '',
    name: '',
    edition: '',
    editionName: '',
    notes: false,
    touched: { code: false, name: false, edition: false, editionName: false, notes: false },
  };
}

function setField(form: InstallForm, key: InstallFieldKey, value: string | boolean): InstallForm {
  if (key === 'notes') {
    return { ...form, notes: Boolean(value), touched: { ...form.touched, notes: true } };
  }
  return { ...form, [key]: String(value), touched: { ...form.touched, [key]: true } };
}

function suggest(
  form: InstallForm,
  patch: Partial<Pick<InstallForm, 'code' | 'name' | 'edition' | 'editionName' | 'notes'>>,
): InstallForm {
  const next: InstallForm = { ...form, touched: { ...form.touched } };
  (Object.keys(patch) as InstallFieldKey[]).forEach((key) => {
    if (next.touched[key]) return;
    if (key === 'notes') next.notes = Boolean(patch.notes);
    else next[key] = String(patch[key as 'code'] ?? '');
    next.touched[key] = false;
  });
  return next;
}

/** A suggested translation code: the file name, made into a code. */
function slugOf(filename: string): string {
  const base = filename.split('/').pop() ?? filename;
  const stem = base.replace(/\.[^.]+$/, '');
  return stem.replace(/[^A-Za-z0-9]+/g, '-').replace(/^-|-$/g, '').toLowerCase();
}

/** A suggested display name: the file name a person would recognise. */
function displayNameOf(filename: string): string {
  const base = filename.split('/').pop() ?? filename;
  return base.replace(/\.[^.]+$/, '').replace(/_/g, ' ').replace(/\s+/g, ' ').trim();
}

/** Names that read as a title rather than a code ("NKJV Study Bible.epub"). */
function looksLikeTitle(filename: string): boolean {
  const stem = (filename.split('/').pop() ?? filename).replace(/\.[^.]+$/, '');
  return stem.trim().length >= 8 && stem.includes(' ');
}

/**
 * Filenames that say the file is study material rather than plain scripture.
 *
 * Only used to prefill the study-notes checkbox, never to decide an import:
 * the importer still proves what a file actually contains, and the operator
 * can untick the box.
 */
function mentionsNotes(filename: string): boolean {
  return /study\s*bible|commentary|study\s*notes|sermons?/i.test(filename);
}

/** What a file name can honestly suggest, before anyone has typed anything. */
function suggestionFor(filename: string): Partial<InstallForm> {
  const notes = mentionsNotes(filename);
  // Both name slots are rewritten every time: an intermediate state while a
  // path is typed can read as a plain title, and its suggestion must not
  // survive into the payload once the real extension shows it is a commentary.
  const patch: Partial<InstallForm> = { code: slugOf(filename), notes, name: '', editionName: '' };
  if (notes) {
    // A commentary's proper name belongs to the study notes, not the translation.
    patch.editionName = displayNameOf(filename);
  } else if (looksLikeTitle(filename)) {
    patch.name = displayNameOf(filename);
  }
  return patch;
}

/** Optional fields, sent only when they carry a value. */
function extrasOf(form: InstallForm): {
  name?: string;
  edition?: string;
  edition_name?: string;
  import_notes?: boolean;
} {
  const code = (value: string) => value.trim();
  return {
    ...(code(form.name) ? { name: code(form.name) } : {}),
    ...(code(form.edition) ? { edition: code(form.edition) } : {}),
    ...(code(form.editionName) ? { edition_name: code(form.editionName) } : {}),
    ...(form.notes ? { import_notes: true } : {}),
  };
}

/**
 * The four names every file door shares, prefilled and editable.
 *
 * The translation code gets a datalist of what the catalogue knows because
 * the importer refuses codes the manifest has never heard of; seeing the
 * known codes next to the field turns that refusal from a surprise into a
 * choice.
 */
function InstallFields({
  idPrefix,
  form,
  set,
  knownCodes,
}: {
  idPrefix: string;
  form: InstallForm;
  set: (key: InstallFieldKey, value: string | boolean) => void;
  knownCodes: string[];
}) {
  const typed = form.code.trim();
  const unknown = typed !== '' && knownCodes.length > 0 && !knownCodes.includes(typed);
  return (
    <>
      <div className="grid gap-2 sm:grid-cols-2">
        <div>
          <Field
            label="Translation code"
            id={`${idPrefix}-code`}
            value={form.code}
            onChange={(value) => set('code', value)}
            placeholder="nkjv"
            testId={`${idPrefix}-code`}
            list={`${idPrefix}-codes`}
          />
          <datalist id={`${idPrefix}-codes`}>
            {knownCodes.map((code) => (
              <option key={code} value={code} />
            ))}
          </datalist>
          {unknown ? (
            <p className="mt-1 text-[10px] leading-relaxed text-amber-300/90" data-testid={`${idPrefix}-code-hint`}>
              {typed} is not a code the manifest knows, so the run would be refused. Known
              codes: {knownCodes.join(', ')}.
            </p>
          ) : null}
        </div>
        <Field
          label="Display name"
          id={`${idPrefix}-name`}
          value={form.name}
          onChange={(value) => set('name', value)}
          placeholder="New King James Version"
          testId={`${idPrefix}-name`}
        />
        <Field
          label="Study Bible code (optional)"
          id={`${idPrefix}-edition`}
          value={form.edition}
          onChange={(value) => set('edition', value)}
          placeholder="nkjv-tomholland"
          testId={`${idPrefix}-edition`}
        />
        <Field
          label="Study notes name (optional)"
          id={`${idPrefix}-editionname`}
          value={form.editionName}
          onChange={(value) => set('editionName', value)}
          placeholder="Romans: The Divine Marriage"
          testId={`${idPrefix}-editionname`}
        />
      </div>
      <label className="flex items-center gap-2 min-h-11 text-xs text-slate-300">
        <input
          type="checkbox"
          checked={form.notes}
          data-testid={`${idPrefix}-notes`}
          onChange={(event) => set('notes', event.target.checked)}
          className="h-4 w-4 accent-amber-500"
        />
        Keep the study notes in this file
      </label>
      <p className="text-[10px] leading-relaxed text-slate-500">
        Tick this for a study Bible or commentary: its notes are filed against the study Bible
        code above, and a source with no verse text installs as notes alone.
      </p>
    </>
  );
}