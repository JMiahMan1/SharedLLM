import { useId, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react';
import PasswordField from '../ui/PasswordField';
import { Archive, Check, ChevronDown, ChevronRight, Download, FileLock2, FolderOpen, KeyRound, Loader2, RotateCcw, TriangleAlert } from 'lucide-react';
import { createPortal } from 'react-dom';
import ChatSheet from '../chat/Sheet';
import { api } from '../../services/api';
import { saveBlob } from '../../lib/saveFile';
import { useAuth } from '../../context/AuthContext';
import { DamagedFileError, FILE_SUFFIX, WrongPasswordError, encryptStream, openExport } from '../../lib/accountExport/crypto';
import {
  IncompleteExportError,
  MIN_EXPORT_PASSWORD,
  addCounts,
  endChecker,
  exportFileName,
  exportPasswordProblem,
  passwordStrength,
  readExport,
  restoreParts,
  serviceLabel,
  type Counts,
  type OpenedExport,
} from '../../lib/accountExport/model';
import { buildEncryptedPage } from '../../lib/accountExport/page';
import { presentExport, type PresentedCollection, type PresentedItem, type PresentedService } from '../../lib/accountExport/present';

/**
 * Export my data and Open an export file (docs/ACCOUNT_EXPORT.md). The file
 * is encrypted and opened here, on this device: the server streams the data
 * and never sees the password.
 */

function errorText(err: unknown, fallback: string): string {
  if (err instanceof WrongPasswordError || err instanceof DamagedFileError || err instanceof IncompleteExportError) return err.message;
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (err instanceof Error && err.message) return err.message;
  return fallback;
}

function megabytes(n: number): string {
  return n < 1024 * 1024 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * The chat's sheet, put on document.body: inside the page it sat in the
 * page's stacking context, so on a tablet the bottom bar covered its buttons
 * and page controls showed through it.
 */
function Sheet(props: Parameters<typeof ChatSheet>[0]) {
  return createPortal(<ChatSheet {...props} />, document.body);
}

const row = 'w-full min-h-14 flex items-center justify-between gap-3 p-3 rounded-xl bg-white/5 hover:bg-white/10 transition-colors text-left';

export default function AccountExportPanel() {
  const [sheet, setSheet] = useState<'export' | 'open' | null>(null);
  return (
    <>
      <button type="button" className={row} onClick={() => setSheet('export')} data-testid="account-export-open">
        <div className="flex min-w-0 items-center gap-3">
          <Archive size={18} className="shrink-0 text-slate-400" />
          <div className="min-w-0">
            <p className="text-white text-sm font-medium">Export my data</p>
            <p className="text-xs text-slate-400">An encrypted copy of everything Jarvis holds for you</p>
          </div>
        </div>
        <ChevronRight size={16} className="shrink-0 text-slate-500" />
      </button>
      <button type="button" className={row} onClick={() => setSheet('open')} data-testid="account-export-open-file">
        <div className="flex min-w-0 items-center gap-3">
          <FolderOpen size={18} className="shrink-0 text-slate-400" />
          <div className="min-w-0">
            <p className="text-white text-sm font-medium">Open an export file</p>
            <p className="text-xs text-slate-400">Read it, save it as an encrypted page, or restore it</p>
          </div>
        </div>
        <ChevronRight size={16} className="shrink-0 text-slate-500" />
      </button>
      {sheet === 'export' && <ExportSheet onClose={() => setSheet(null)} />}
      {sheet === 'open' && <OpenExportSheet onClose={() => setSheet(null)} />}
    </>
  );
}

/** Choose a password and save the encrypted file. `person` is set when an admin exports someone else. */
export function ExportSheet({ onClose, person }: { onClose: () => void; person?: { username: string; name: string } }) {
  const { user } = useAuth();
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const exportPasswordId = useId();
  const exportConfirmId = useId();
  const [understood, setUnderstood] = useState(false);
  const [phase, setPhase] = useState<'form' | 'working' | 'done'>('form');
  const [bytes, setBytes] = useState(0);
  const [error, setError] = useState('');
  const [failed, setFailed] = useState<string[]>([]);
  const abort = useRef<AbortController | null>(null);

  const problem = exportPasswordProblem(password, confirm);
  const strength = passwordStrength(password);
  const forSomeoneElse = Boolean(person && person.username !== user?.username);
  const username = person?.username || user?.username || 'account';

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (problem || !understood || phase === 'working') return;
    setPhase('working');
    setError('');
    setBytes(0);
    const controller = new AbortController();
    abort.current = controller;
    try {
      const source = await api.streamAccountExport(forSomeoneElse ? person?.username : undefined, controller.signal);
      const checker = endChecker();
      const file = await encryptStream(source.pipeThrough(checker.stream), password, { onProgress: setBytes });
      setPassword('');
      setConfirm('');
      const saved = await saveBlob(file, exportFileName(username));
      if (!saved) {
        setPhase('form');
        setError('The file was not saved. Export again to choose where it goes.');
        return;
      }
      setFailed(checker.failed());
      setPhase('done');
    } catch (err) {
      setPhase('form');
      setError(controller.signal.aborted ? 'The export was stopped. Nothing was saved.' : errorText(err, 'The export did not finish. Nothing was saved.'));
    } finally {
      abort.current = null;
    }
  };

  const close = () => {
    abort.current?.abort();
    onClose();
  };

  const title = forSomeoneElse ? `Export ${person?.name}'s data` : 'Export my data';
  return (
    <Sheet title={title} onClose={close} testId="account-export-sheet">
      {phase === 'done' ? (
        <div className="space-y-3" data-testid="account-export-done">
          <p className="flex items-center gap-2 text-sm font-semibold text-emerald-300">
            <Check size={16} aria-hidden /> The encrypted file is saved
          </p>
          <p className="text-sm leading-relaxed text-slate-300">
            {forSomeoneElse
              ? `Give the file and its password to ${person?.name}, the password in person. Without the password nobody can open it.`
              : 'Keep it somewhere safe. Open it from Settings with the password to read it or restore it.'}
          </p>
          {failed.length > 0 && (
            <p className="rounded-xl border border-amber-400/25 bg-amber-500/10 p-3 text-sm text-amber-100" role="status" data-testid="account-export-partial">
              Some parts could not be read just now and are not in this file: {failed.map((f) => serviceLabel(f.split(':')[0])).join(', ')}. Export again later for a complete copy.
            </p>
          )}
          <button type="button" onClick={close} className="min-h-12 w-full rounded-xl bg-white/10 text-sm font-semibold text-white">
            Done
          </button>
        </div>
      ) : (
        <form onSubmit={(event) => void submit(event)} className="space-y-3">
          <p className="text-sm leading-relaxed text-slate-300">
            {forSomeoneElse
              ? `A copy of everything Jarvis holds for ${person?.name}, encrypted on this device with a password you choose and will give them.`
              : 'A copy of everything Jarvis holds for you: profile and settings, health, Bible, places, trips and steps. It is encrypted on this device with a password you choose; the server never sees it.'}
          </p>
          <details className="rounded-xl bg-white/5 p-3 text-xs text-slate-300">
            <summary className="flex min-h-11 cursor-pointer items-center font-semibold text-slate-200">What is left out</summary>
            <ul className="mt-1 list-disc space-y-1 pl-4">
              <li>Passwords, API keys and saved service logins: never exported, not even encrypted.</li>
              <li>Your voice signature: enrol again after a restore.</li>
              <li>Notes, recipes, calendar, files and family chat: they are in your Nextcloud account.</li>
              <li>The private chat: it has its own backup, opened with the chat password.</li>
              <li>Other people's data, even on shared lists, places and trips.</li>
            </ul>
          </details>
          <div>
            <label htmlFor={exportPasswordId} className="mb-1 block text-xs font-semibold text-slate-300">Password for the file</label>
            <PasswordField
              id={exportPasswordId}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="new-password"
              autoCapitalize="off"
              spellCheck={false}
              disabled={phase === 'working'}
              data-testid="account-export-password"
              className="glass-input min-h-11 w-full text-base sm:text-sm"
            />
          </div>
          {password && (
            <div className="space-y-1" data-testid="account-export-strength">
              <div className="flex gap-1" aria-hidden>
                {[1, 2, 3].map((n) => (
                  <span
                    key={n}
                    className={`h-1.5 flex-1 rounded-full ${strength.score >= n ? (strength.score === 3 ? 'bg-emerald-400' : strength.score === 2 ? 'bg-sky-400' : 'bg-amber-400') : 'bg-white/10'}`}
                  />
                ))}
              </div>
              <p className="text-xs text-slate-400">{strength.label}</p>
            </div>
          )}
          <div>
            <label htmlFor={exportConfirmId} className="mb-1 block text-xs font-semibold text-slate-300">Type it again</label>
            <PasswordField
              id={exportConfirmId}
              value={confirm}
              onChange={(event) => setConfirm(event.target.value)}
              autoComplete="new-password"
              autoCapitalize="off"
              spellCheck={false}
              disabled={phase === 'working'}
              data-testid="account-export-confirm"
              className="glass-input min-h-11 w-full text-base sm:text-sm"
            />
          </div>
          {password && confirm && problem && (
            <p className="text-xs text-amber-200" role="status">
              {problem}
            </p>
          )}
          <div className="rounded-xl border border-amber-400/25 bg-amber-500/10 p-3 text-sm text-amber-100" role="note">
            <p className="flex items-center gap-2 font-semibold">
              <TriangleAlert size={15} aria-hidden /> It cannot be recovered
            </p>
            <p className="mt-1 leading-relaxed">
              Nobody can open the file without this password, not even an admin: it never leaves this device. If it is forgotten, the
              file is useless.
            </p>
          </div>
          <CheckRow checked={understood} onChange={setUnderstood} testId="account-export-understood">
            I understand a forgotten password cannot be recovered
          </CheckRow>
          {error && (
            <p className="text-sm text-rose-200" role="alert" data-testid="account-export-error">
              {error}
            </p>
          )}
          <button
            type="submit"
            disabled={phase === 'working' || Boolean(problem) || !understood}
            data-testid="account-export-submit"
            className="flex min-h-12 w-full items-center justify-center gap-2 rounded-xl bg-purple-600 text-sm font-semibold text-white disabled:opacity-50"
          >
            {phase === 'working' ? <Loader2 size={15} className="animate-spin" /> : <Download size={15} />}
            {phase === 'working' ? `Encrypting… ${bytes ? megabytes(bytes) : ''}` : 'Export and save'}
          </button>
          <p className="text-center text-xs text-slate-500">At least {MIN_EXPORT_PASSWORD} characters. A short sentence is easy to remember and hard to guess.</p>
        </form>
      )}
    </Sheet>
  );
}

type RestoreResult = { service: string; counts: Counts; error?: string };

/** Pick a file, open it with its password, read it, and restore it. */
export function OpenExportSheet({ onClose }: { onClose: () => void }) {
  const { user } = useAuth();
  const [file, setFile] = useState<File | null>(null);
  const openPasswordId = useId();
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [opened, setOpened] = useState<OpenedExport | null>(null);
  const [view, setView] = useState<'read' | 'preview' | 'result'>('read');
  const [preview, setPreview] = useState<RestoreResult[]>([]);
  const [results, setResults] = useState<RestoreResult[]>([]);
  const [replace, setReplace] = useState(false);
  const [notice, setNotice] = useState('');

  const presented = useMemo(() => (opened ? presentExport(opened.services) : []), [opened]);
  const subject = opened?.manifest.subject.username || '';
  const own = Boolean(subject && subject === user?.username);
  const mayRestore = own || Boolean(user?.is_admin);
  const target = own ? undefined : subject;

  const open = async (event: FormEvent) => {
    event.preventDefault();
    if (!file || !password || busy) return;
    setBusy(true);
    setError('');
    try {
      const { stream } = await openExport(file, password);
      const data = await readExport(stream);
      setPassword('');
      setOpened(data);
      setView('read');
    } catch (err) {
      setError(errorText(err, 'This file could not be opened.'));
    } finally {
      setBusy(false);
    }
  };

  const savePage = async () => {
    if (!file) return;
    setBusy(true);
    setNotice('');
    try {
      const html = buildEncryptedPage(new Uint8Array(await file.arrayBuffer()), `Jarvis export of ${subject}`);
      const name = file.name.endsWith(FILE_SUFFIX) ? file.name.slice(0, -FILE_SUFFIX.length) + '.html' : `${file.name}.html`;
      const saved = await saveBlob(new Blob([html], { type: 'text/html' }), name);
      if (saved) setNotice('Saved. The page asks for the same password before it shows anything.');
    } catch (err) {
      setError(errorText(err, 'The page could not be saved.'));
    } finally {
      setBusy(false);
    }
  };

  const runRestore = async (dryRun: boolean) => {
    if (!opened) return;
    setBusy(true);
    setError('');
    const out: RestoreResult[] = [];
    for (const part of restoreParts(opened)) {
      try {
        const answer = await api.importAccountPart({
          service: part.service,
          collections: part.collections,
          dry_run: dryRun,
          replace_conflicts: dryRun ? false : replace,
          user: target,
        });
        out.push({ service: part.service, counts: addCounts(answer.collections as Record<string, Partial<Counts>>) });
      } catch (err) {
        out.push({ service: part.service, counts: addCounts({}), error: errorText(err, 'Could not be restored') });
      }
    }
    setBusy(false);
    if (dryRun) {
      setPreview(out);
      setView('preview');
    } else {
      setResults(out);
      setView('result');
    }
  };

  const conflicts = preview.reduce((n, p) => n + p.counts.conflict, 0);

  return (
    <Sheet title={opened ? `Export of ${subject}` : 'Open an export file'} onClose={onClose} testId="account-open-sheet" wide={Boolean(opened)}>
      {!opened && (
        <form onSubmit={(event) => void open(event)} className="space-y-3">
          <p className="text-sm leading-relaxed text-slate-300">
            Choose a <span className="font-mono text-xs">{FILE_SUFFIX}</span> file and enter its password. It is opened on this device only.
          </p>
          <label className="flex min-h-12 cursor-pointer items-center gap-3 rounded-xl border border-dashed border-white/20 px-3 text-sm text-slate-200">
            <FileLock2 size={18} className="shrink-0 text-slate-400" aria-hidden />
            <span className="min-w-0 flex-1 truncate">{file ? file.name : 'Choose the export file'}</span>
            <input
              type="file"
              className="sr-only"
              data-testid="account-open-file"
              onChange={(event) => {
                setFile(event.target.files?.[0] ?? null);
                setError('');
              }}
            />
          </label>
          <div>
            <label htmlFor={openPasswordId} className="mb-1 block text-xs font-semibold text-slate-300">Password</label>
            <PasswordField
              id={openPasswordId}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="current-password"
              autoCapitalize="off"
              spellCheck={false}
              data-testid="account-open-password"
              className="glass-input min-h-11 w-full text-base sm:text-sm"
            />
          </div>
          {error && (
            <p className="text-sm text-rose-200" role="alert" data-testid="account-open-error">
              {error}
            </p>
          )}
          <button
            type="submit"
            disabled={!file || !password || busy}
            data-testid="account-open-submit"
            className="flex min-h-12 w-full items-center justify-center gap-2 rounded-xl bg-purple-600 text-sm font-semibold text-white disabled:opacity-50"
          >
            {busy ? <Loader2 size={15} className="animate-spin" /> : <KeyRound size={15} />}
            {busy ? 'Opening…' : 'Open'}
          </button>
        </form>
      )}

      {opened && view === 'read' && (
        <div className="space-y-3" data-testid="account-viewer">
          <p className="text-xs text-slate-400">
            Made {new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(opened.manifest.exported_at))}. Read only: nothing here changes your account.
          </p>
          {opened.failed.length > 0 && (
            <p className="rounded-xl border border-amber-400/25 bg-amber-500/10 p-3 text-xs text-amber-100">
              Not in this file (could not be read when it was made): {opened.failed.map((f) => serviceLabel(f.split(':')[0])).join(', ')}.
            </p>
          )}
          {presented.map((service) => (
            <ServiceSection key={service.service} service={service} />
          ))}
          {notice && <p className="text-sm text-emerald-300" role="status">{notice}</p>}
          {error && <p className="text-sm text-rose-200" role="alert">{error}</p>}
          <div className="grid gap-2 sm:grid-cols-2">
            <button
              type="button"
              onClick={() => void savePage()}
              disabled={busy}
              data-testid="account-save-page"
              className="flex min-h-12 items-center justify-center gap-2 rounded-xl bg-white/10 px-3 text-sm font-semibold text-white disabled:opacity-50"
            >
              <FileLock2 size={15} /> Save as an encrypted page
            </button>
            {mayRestore ? (
              <button
                type="button"
                onClick={() => void runRestore(true)}
                disabled={busy}
                data-testid="account-restore-preview"
                className="flex min-h-12 items-center justify-center gap-2 rounded-xl bg-purple-600 px-3 text-sm font-semibold text-white disabled:opacity-50"
              >
                {busy ? <Loader2 size={15} className="animate-spin" /> : <RotateCcw size={15} />}
                {own ? 'Restore into my account' : `Restore into ${subject}'s account`}
              </button>
            ) : (
              <p className="text-xs text-slate-400 sm:self-center" data-testid="account-restore-not-yours">
                This export belongs to {subject}. Only {subject} or an admin can restore it.
              </p>
            )}
          </div>
        </div>
      )}

      {opened && view === 'preview' && (
        <div className="space-y-3" data-testid="account-restore-summary">
          <p className="text-sm text-slate-300">
            Nothing has changed yet. This is what restoring would do{own ? '' : ` in ${subject}'s account`}. Nothing is ever deleted.
          </p>
          <CountsTable rows={preview} />
          {conflicts > 0 && (
            <CheckRow checked={replace} onChange={setReplace} testId="account-restore-replace" tone="amber">
              Also replace {conflicts} {conflicts === 1 ? 'item' : 'items'} that changed here since the export (or cannot be compared). Left
              unticked, the newer copy here is kept.
            </CheckRow>
          )}
          {error && <p className="text-sm text-rose-200" role="alert">{error}</p>}
          <div className="grid gap-2 sm:grid-cols-2">
            <button type="button" onClick={() => setView('read')} className="min-h-12 rounded-xl bg-white/10 px-3 text-sm font-semibold text-white">
              Back
            </button>
            <button
              type="button"
              onClick={() => void runRestore(false)}
              disabled={busy || preview.every((p) => p.error)}
              data-testid="account-restore-apply"
              className="flex min-h-12 items-center justify-center gap-2 rounded-xl bg-purple-600 px-3 text-sm font-semibold text-white disabled:opacity-50"
            >
              {busy ? <Loader2 size={15} className="animate-spin" /> : <RotateCcw size={15} />}
              {busy ? 'Restoring…' : 'Restore'}
            </button>
          </div>
        </div>
      )}

      {opened && view === 'result' && (
        <div className="space-y-3" data-testid="account-restore-result">
          <p className="flex items-center gap-2 text-sm font-semibold text-emerald-300">
            <Check size={16} aria-hidden /> Restore finished
          </p>
          <CountsTable rows={results} />
          <p className="text-xs text-slate-400">Sign in to your services again from Accounts & Integrations: passwords are never in an export.</p>
          <button type="button" onClick={onClose} className="min-h-12 w-full rounded-xl bg-white/10 text-sm font-semibold text-white">
            Done
          </button>
        </div>
      )}
    </Sheet>
  );
}

/**
 * A checkbox whose whole row is the target: the real input covers the row
 * (invisible), so a thumb anywhere on it toggles it and it stays 44px tall.
 */
function CheckRow({ checked, onChange, testId, tone = 'plain', children }: {
  checked: boolean;
  onChange: (value: boolean) => void;
  testId: string;
  tone?: 'plain' | 'amber';
  children: ReactNode;
}) {
  const frame = tone === 'amber' ? 'rounded-xl border border-amber-400/25 bg-amber-500/10 p-3 text-amber-100' : 'text-slate-200';
  return (
    <label className={`relative flex min-h-11 cursor-pointer items-center gap-3 text-sm ${frame}`}>
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="peer absolute inset-0 h-full w-full cursor-pointer opacity-0"
        data-testid={testId}
      />
      <span
        aria-hidden
        className="flex h-5 w-5 shrink-0 items-center justify-center rounded border border-white/40 text-white peer-checked:border-purple-400 peer-checked:bg-purple-500 peer-focus-visible:ring-2 peer-focus-visible:ring-purple-300"
      >
        {checked && <Check size={14} />}
      </span>
      <span>{children}</span>
    </label>
  );
}

function CountsTable({ rows }: { rows: RestoreResult[] }) {
  return (
    <ul className="divide-y divide-white/5 rounded-xl bg-white/5">
      {rows.map((r) => (
        <li key={r.service} className="p-3 text-sm" data-testid={`account-counts-${r.service}`}>
          <p className="font-semibold text-white">{serviceLabel(r.service)}</p>
          {r.error ? (
            <p className="text-xs text-rose-200">{r.error}</p>
          ) : (
            <p className="text-xs text-slate-300">
              {r.counts.add} new · {r.counts.update} updated · {r.counts.conflict} newer here · {r.counts.unchanged} already the same
              {r.counts.skipped ? ` · ${r.counts.skipped} kept for reading only` : ''}
            </p>
          )}
        </li>
      ))}
    </ul>
  );
}

const PAGE = 25;
const TRANSCRIPT_PREVIEW = 6;

function ServiceSection({ service }: { service: PresentedService }) {
  const [open, setOpen] = useState(false);
  return (
    <section className="rounded-xl bg-white/5" data-testid={`account-viewer-${service.service}`}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex min-h-12 w-full items-center justify-between gap-3 px-3 text-left"
      >
        <span className="text-sm font-semibold text-white">{service.label}</span>
        <span className="flex shrink-0 items-center gap-2 text-xs text-slate-400">
          {service.total} {service.total === 1 ? 'item' : 'items'} {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </span>
      </button>
      {open && (
        <div className="space-y-2 px-3 pb-3">
          {service.collections.map((c) => (
            <CollectionList key={c.name} collection={c} />
          ))}
        </div>
      )}
    </section>
  );
}

function visibleGroups(collection: PresentedCollection, shown: number) {
  const out: PresentedCollection['groups'] = [];
  let left = shown;
  for (const g of collection.groups) {
    if (left <= 0) break;
    const items = g.items.slice(0, left);
    left -= items.length;
    out.push({ ...g, items });
  }
  return out;
}

function CollectionList({ collection }: { collection: PresentedCollection }) {
  const [shown, setShown] = useState(0);
  // Page through the items in order, keeping each month's heading with its count.
  const visible = visibleGroups(collection, shown);
  return (
    <div className="rounded-lg border border-white/5" data-testid={`account-kind-${collection.name}`}>
      <button
        type="button"
        onClick={() => setShown((v) => (v ? 0 : PAGE))}
        aria-expanded={shown > 0}
        className="flex min-h-11 w-full items-center justify-between gap-2 px-3 text-left text-sm text-slate-200"
      >
        <span className="min-w-0">
          {collection.label}
          {collection.readOnly && <span className="ml-2 text-[11px] text-slate-500">for reading</span>}
        </span>
        <span className="shrink-0 text-xs text-slate-400">{collection.count}</span>
      </button>
      {shown > 0 && (
        <div className="space-y-3 px-3 pb-3">
          {visible.map((g, gi) => (
            <div key={`${g.label}-${gi}`} className="space-y-2">
              {g.label && (
                <p className="pt-1 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                  {g.label} <span className="font-normal normal-case tracking-normal text-slate-500">· {g.count}</span>
                </p>
              )}
              {g.items.map((item, ii) => (
                <ItemCard key={ii} item={item} />
              ))}
            </div>
          ))}
          {shown < collection.count && (
            <button type="button" onClick={() => setShown((v) => v + PAGE)} className="min-h-11 w-full rounded-lg bg-white/5 text-xs font-semibold text-slate-200">
              Show more ({collection.count - shown} left)
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function ItemCard({ item }: { item: PresentedItem }) {
  const [whole, setWhole] = useState(false);
  const turns = item.transcript || [];
  const shownTurns = whole ? turns : turns.slice(0, TRANSCRIPT_PREVIEW);
  return (
    <article className="rounded-lg bg-black/20 p-3 text-sm">
      <div className="flex items-start gap-2">
        {item.swatch && <span aria-hidden className="mt-1 h-3 w-3 shrink-0 rounded-full" style={{ background: item.swatch }} />}
        <div className="min-w-0 flex-1">
          <p className="break-words font-medium text-white [overflow-wrap:anywhere]">{item.title}</p>
          {item.subtitle && <p className="mt-0.5 break-words text-xs text-slate-400 [overflow-wrap:anywhere]">{item.subtitle}</p>}
        </div>
      </div>
      {item.detail && <p className="mt-2 whitespace-pre-wrap break-words text-xs text-slate-300 [overflow-wrap:anywhere]">{item.detail}</p>}
      {item.facts && item.facts.length > 0 && (
        <dl className="mt-2 grid grid-cols-[minmax(6rem,40%)_1fr] gap-x-3 gap-y-1 text-xs">
          {item.facts.map((f) => (
            <div key={f.label} className="contents">
              <dt className="text-slate-400">{f.label}</dt>
              <dd className="min-w-0 break-words text-slate-200 [overflow-wrap:anywhere]">{f.value}</dd>
            </div>
          ))}
        </dl>
      )}
      {turns.length > 0 && (
        <div className="mt-3 space-y-2" data-testid="account-transcript">
          {shownTurns.map((t, i) => (
            <div key={i} className={`flex ${t.who === 'me' ? 'justify-end' : 'justify-start'}`}>
              <div
                className={`max-w-[85%] whitespace-pre-wrap break-words rounded-2xl px-3 py-2 text-xs [overflow-wrap:anywhere] ${
                  t.who === 'me' ? 'rounded-br-md bg-purple-600/80 text-white' : 'rounded-bl-md bg-white/10 text-slate-100'
                }`}
              >
                <span className="sr-only">{t.who === 'me' ? 'You: ' : 'Jarvis: '}</span>
                {t.text}
              </div>
            </div>
          ))}
          {turns.length > TRANSCRIPT_PREVIEW && (
            <button type="button" onClick={() => setWhole((v) => !v)} className="min-h-11 w-full rounded-lg bg-white/5 text-xs font-semibold text-slate-200">
              {whole ? 'Show less' : `Show the whole conversation (${turns.length} messages)`}
            </button>
          )}
        </div>
      )}
    </article>
  );
}
