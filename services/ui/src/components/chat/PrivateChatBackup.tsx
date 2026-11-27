import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react';
import PasswordField from '../ui/PasswordField';
import { ArrowLeft, Download, FileLock2, FolderOpen, Heart, Images, Loader2, Lock, MessageSquare, Play, RotateCcw, ShieldCheck, TriangleAlert } from 'lucide-react';
import toast from 'react-hot-toast';
import { api, privateChatRestoreClient } from '../../services/api';
import { saveBlob } from '../../lib/saveFile';
import { pickerDone, pickerOpening } from '../../lib/privateChatSession';
import { ensureBackupKey } from '../../lib/privateChatBackupKey';
import type { PrivateVault } from '../../lib/privateChatCrypto';
import {
  BELOVED_SECTIONS,
  decryptBlob,
  decryptClip,
  describeBeloved,
  openBackup,
  pictureType,
  readKeepsake,
  type BackupKeyRecord,
  type Keepsake,
  type KeepsakeMessage,
  type OpenedBackup,
} from '../../lib/privateChatBackupCore.js';
import { backupFileName, backupFromFile, buildKeepsakePage, pageFileName, restoreBackup, type RestoreProgress } from '../../lib/privateChatBackupFile';
import Sheet from './Sheet';
import { dayLabel, timeLabel } from './chatModel';
import { errorText } from './privateChatModel';
import { noSave } from './usePrivatePicture';

const primary = 'flex min-h-12 w-full items-center justify-center gap-2 rounded-xl bg-rose-600 px-3 text-sm font-semibold text-white disabled:opacity-50';
const secondary = 'flex min-h-12 w-full items-center justify-center gap-2 rounded-xl border border-white/10 px-3 text-sm font-semibold text-slate-200 disabled:opacity-50';

function Notice({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.04] p-3 text-sm leading-relaxed text-slate-300" role="note">
      {children}
    </div>
  );
}

/** Save a file the person asked for; the chat must not lock while the phone's "Save to" picker is open. */
async function saveFile(blob: Blob, name: string, token?: string): Promise<boolean> {
  if (token) pickerOpening(token);
  try {
    return await saveBlob(blob, name);
  } finally {
    if (token) pickerDone(token);
  }
}

// --- Export -----------------------------------------------------------------------

/**
 * "Back up and export": the gateway makes the chat's encrypted backup file and
 * this device saves it (Android: the "Save to" picker; a browser: a download).
 * Or the same file wrapped in a page that asks for the password. Either way
 * the file is never readable without the chat password.
 */
export function PrivateChatExportSheet({ token, vault, onBack, onClose }: { token: string; vault?: PrivateVault; onBack: () => void; onClose: () => void }) {
  const [busy, setBusy] = useState<'file' | 'page' | null>(null);
  const [error, setError] = useState('');

  const save = async (as: 'file' | 'page') => {
    if (busy) return;
    setBusy(as);
    setError('');
    try {
      // The chat gets its backup key on unlock; make sure before asking for a file encrypted to it.
      if (vault?.unlocked) await ensureBackupKey(token, vault).catch(() => undefined);
      const { blob, filename } = await api.exportPrivateChat(token);
      const name = filename || backupFileName();
      const saved = as === 'file' ? await saveFile(blob, name, token) : await saveFile(await buildKeepsakePage(blob), pageFileName(name), token);
      if (saved) toast.success(as === 'file' ? 'Backup saved. Only the chat password opens it.' : 'Encrypted page saved. It asks for the chat password.');
    } catch (e) {
      setError(errorText(e, 'Could not make the backup'));
    } finally {
      setBusy(null);
    }
  };

  return (
    <Sheet title="Back up and export" onClose={onClose} testId="private-backup">
      <div className="space-y-3">
        <p className="text-sm leading-relaxed text-slate-300">
          A copy of everything in this chat and in Beloved: messages, edits, reactions, pictures, videos and the gallery.
        </p>
        <Notice>
          <span className="flex items-center gap-2 font-semibold text-white">
            <ShieldCheck size={15} className="shrink-0 text-rose-200" aria-hidden /> Always encrypted
          </span>
          <span className="mt-1 block">
            Only the chat password opens it, on any phone or computer. Without the password nobody can read it, not even Jarvis.
          </span>
        </Notice>
        <button type="button" onClick={() => void save('file')} disabled={Boolean(busy)} className={primary} data-testid="private-backup-save">
          {busy === 'file' ? <Loader2 size={15} className="animate-spin" /> : <Download size={15} aria-hidden />}
          {busy === 'file' ? 'Making the backup…' : 'Save the backup file'}
        </button>
        <button type="button" onClick={() => void save('page')} disabled={Boolean(busy)} className={secondary} data-testid="private-backup-page">
          {busy === 'page' ? <Loader2 size={15} className="animate-spin" /> : <FileLock2 size={15} aria-hidden />}
          {busy === 'page' ? 'Making the page…' : 'Save as an encrypted page'}
        </button>
        <p className="text-xs leading-relaxed text-slate-400">
          The page is one file that opens in a web browser and asks for the password. The backup file can be put back into Jarvis from
          Lock and password, Open a backup file.
        </p>
        {error && (
          <p className="text-sm text-rose-200" role="alert" data-testid="private-backup-error">
            {error}
          </p>
        )}
        <button type="button" onClick={onBack} className={secondary}>
          Back
        </button>
      </div>
    </Sheet>
  );
}

// --- Open -------------------------------------------------------------------------

type Step = 'pick' | 'password' | 'view' | 'restore';

/** The live chat's key record, as the backup's header carries them (for a password set after the backup). */
function liveRecord(info: { version?: number; kdf?: { salt: string; iterations: number }; wrapped_key?: { iv: string; ct: string }; verifier?: { iv: string; ct: string } }): BackupKeyRecord | null {
  if (!info.kdf || !info.verifier) return null;
  return {
    version: info.version === 2 ? 2 : 1,
    kdf: { salt: info.kdf.salt, iterations: info.kdf.iterations },
    ...(info.wrapped_key ? { wrapped_key: info.wrapped_key } : {}),
    verifier: info.verifier,
  };
}

/**
 * "Open a backup file": pick a `.jarvis-backup` (or a saved encrypted page),
 * enter the chat password, and read it here as a keepsake; from there, put it
 * back into Jarvis. Everything is decrypted on this device and dropped when
 * this closes.
 */
export function OpenPrivateBackup({
  token,
  names = {},
  me,
  onBack,
  onClose,
  onRestored,
}: {
  /** The chat this was opened from, if any: its current key record is tried too. */
  token?: string;
  /** Display names by username, where the app knows them. */
  names?: Record<string, string>;
  /** The signed-in person's username, where known (their messages on the right). */
  me?: string;
  onBack?: () => void;
  onClose: () => void;
  onRestored?: (token: string, created: boolean) => void;
}) {
  const [step, setStep] = useState<Step>('pick');
  const [file, setFile] = useState<File | null>(null);
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState('');
  const [opened, setOpened] = useState<OpenedBackup | null>(null);
  const [keepsake, setKeepsake] = useState<Keepsake | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  // True while this device's file picker or "Save to" sheet is open (which sends the app to the background).
  const holding = useRef(false);

  // Nothing decrypted outlives this screen, and nothing stays open behind the app.
  useEffect(() => {
    const hide = () => {
      if (document.visibilityState === 'hidden' && !holding.current) onClose();
    };
    document.addEventListener('visibilitychange', hide);
    return () => document.removeEventListener('visibilitychange', hide);
  }, [onClose]);

  const pick = () => {
    holding.current = true;
    if (token) pickerOpening(token);
    window.addEventListener('focus', () => window.setTimeout(() => (holding.current = false), 1500), { once: true });
    inputRef.current?.click();
  };

  const chosen = (next: File | null) => {
    holding.current = false;
    if (token) pickerDone(token);
    if (!next) return;
    setFile(next);
    setError('');
    setStep('password');
  };

  const open = async (event: FormEvent) => {
    event.preventDefault();
    if (!file || !password || busy) return;
    setBusy(true);
    setError('');
    setProgress(0);
    const typed = password;
    setPassword('');
    try {
      const source = await backupFromFile(file);
      let more: BackupKeyRecord[] = [];
      if (token) {
        const live = await api.getPrivateChat(token).catch(() => null);
        const record = live ? liveRecord(live as Parameters<typeof liveRecord>[0]) : null;
        if (record) more = [record];
      }
      const result = await openBackup(source, typed, { moreRecords: more, onProgress: (done, total) => setProgress(total ? done / total : 0) });
      setOpened(result);
      setKeepsake(await readKeepsake(result));
      setStep('view');
    } catch (e) {
      const code = (e as { code?: string }).code;
      setError(code === 'password' ? 'That password does not open this backup.' : errorText(e, 'This backup could not be opened.'));
    } finally {
      setBusy(false);
    }
  };

  if (step === 'view' && keepsake && opened) {
    return (
      <KeepsakeView
        keepsake={keepsake}
        names={names}
        me={me}
        file={file}
        token={token}
        onClose={onClose}
        onRestore={() => setStep('restore')}
        onHold={(on) => {
          holding.current = on;
        }}
      />
    );
  }

  if (step === 'restore' && opened && keepsake) {
    return (
      <RestoreSheet
        opened={opened}
        keepsake={keepsake}
        names={names}
        onBack={() => setStep('view')}
        onClose={onClose}
        onRestored={(restored, created) => {
          onRestored?.(restored, created);
          onClose();
        }}
      />
    );
  }

  return (
    <Sheet title="Open a backup file" onClose={onClose} testId="private-backup-open">
      <input
        ref={inputRef}
        type="file"
        accept=".jarvis-backup,.html,application/octet-stream,text/html"
        className="sr-only"
        tabIndex={-1}
        aria-hidden
        data-testid="private-backup-file"
        onChange={(event) => {
          chosen(event.target.files?.[0] ?? null);
          event.target.value = '';
        }}
      />
      {step === 'pick' ? (
        <div className="space-y-3">
          <p className="text-sm leading-relaxed text-slate-300">
            Choose a private chat backup (a <span className="font-semibold text-white">.jarvis-backup</span> file, or an encrypted page saved from
            here). It is opened on this device with the chat password; nothing is sent to read it.
          </p>
          <button type="button" onClick={pick} className={primary} data-testid="private-backup-choose">
            <FolderOpen size={15} aria-hidden /> Choose a backup file
          </button>
          {onBack && (
            <button type="button" onClick={onBack} className={secondary}>
              Back
            </button>
          )}
        </div>
      ) : (
        <form onSubmit={(event) => void open(event)} className="space-y-3">
          <p className="flex min-w-0 items-center gap-2 text-sm text-slate-200">
            <Lock size={15} className="shrink-0 text-rose-200" aria-hidden />
            <span className="min-w-0 truncate" data-testid="private-backup-filename">{file?.name}</span>
          </p>
          <div>
            <label htmlFor="private-backup-password-input" className="mb-1 block text-xs font-semibold text-slate-300">Chat password</label>
            <PasswordField
              id="private-backup-password-input"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="current-password"
              autoCapitalize="off"
              spellCheck={false}
              autoFocus
              data-testid="private-backup-password"
              className="glass-input min-h-11 w-full text-base sm:text-sm"
            />
          </div>
          <p className="text-xs text-slate-400">Any password the chat had when the backup was made works.</p>
          {busy && (
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/10" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(progress * 100)}>
              <div className="h-full bg-rose-500 transition-all" style={{ width: `${Math.max(4, Math.round(progress * 100))}%` }} />
            </div>
          )}
          {error && (
            <p className="text-sm text-rose-200" role="alert" data-testid="private-backup-open-error">
              {error}
            </p>
          )}
          <div className="flex flex-col-reverse gap-2 sm:flex-row">
            <button type="button" onClick={() => setStep('pick')} className={secondary}>
              Choose another
            </button>
            <button type="submit" disabled={busy || !password} className={primary} data-testid="private-backup-unlock">
              {busy ? <Loader2 size={15} className="animate-spin" /> : <Lock size={15} aria-hidden />}
              {busy ? 'Opening…' : 'Open'}
            </button>
          </div>
        </form>
      )}
    </Sheet>
  );
}

// --- The keepsake -------------------------------------------------------------------

function displayName(names: Record<string, string>, username: string): string {
  return names[username] || username;
}

/** One encrypted picture from the backup, decrypted here into an object URL that is revoked when it leaves. */
function KeepsakePicture({ keepsake, blob, mime, alt }: { keepsake: Keepsake; blob: string; mime?: unknown; alt: string }) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const data = keepsake.blobs.get(blob);
  useEffect(() => {
    let live = true;
    let made: string | null = null;
    if (!data) return;
    void decryptBlob(keepsake.key, data)
      .then((plain) => {
        if (!live) return;
        made = URL.createObjectURL(new Blob([plain], { type: pictureType(mime) }));
        setUrl(made);
      })
      .catch(() => live && setFailed(true));
    return () => {
      live = false;
      if (made) URL.revokeObjectURL(made);
    };
  }, [keepsake, data, mime]);
  if (failed || !data) return <p className="text-xs italic text-slate-400">This picture is not in the backup.</p>;
  if (!url) return <div className="h-32 w-full animate-pulse rounded-lg bg-white/5" aria-label="Opening picture" />;
  return <img src={url} alt={alt} className="mt-1 max-h-80 w-auto max-w-full rounded-lg object-contain" data-testid="keepsake-picture" {...noSave} />;
}

function KeepsakeClip({ keepsake, payload }: { keepsake: Keepsake; payload: Record<string, unknown> }) {
  const [url, setUrl] = useState<string | null>(null);
  const [state, setState] = useState<'idle' | 'opening' | 'failed'>('idle');
  useEffect(() => () => {
    if (url) URL.revokeObjectURL(url);
  }, [url]);
  if (url) return <video src={url} controls playsInline className="mt-1 max-h-80 w-full rounded-lg" />;
  return (
    <div>
      {typeof payload.poster === 'string' && <KeepsakePicture keepsake={keepsake} blob={payload.poster} mime="image/jpeg" alt="Video" />}
      <button
        type="button"
        disabled={state === 'opening'}
        onClick={() => {
          setState('opening');
          void decryptClip(keepsake, payload)
            .then((clip) => setUrl(URL.createObjectURL(clip)))
            .catch(() => setState('failed'));
        }}
        className="mt-2 flex min-h-11 items-center gap-2 rounded-xl border border-white/10 px-3 text-sm font-semibold text-slate-100 disabled:opacity-50"
      >
        {state === 'opening' ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} aria-hidden />}
        {state === 'failed' ? 'This video could not be opened' : `Play video${typeof payload.duration === 'number' && payload.duration ? ` (${Math.round(payload.duration)} s)` : ''}`}
      </button>
    </div>
  );
}

function messageText(message: KeepsakeMessage): string {
  const p = message.payload as { t?: string; text?: unknown } | null;
  if (message.edit) return message.edit.text;
  return p && (p.t === 'text' || p.t === 'comment') && typeof p.text === 'string' ? p.text : '';
}

function MessageBody({ keepsake, message }: { keepsake: Keepsake; message: KeepsakeMessage }) {
  const p = (message.payload ?? {}) as Record<string, unknown> & { t?: string };
  if (message.deleted) return <p className="text-sm italic text-slate-400">Deleted</p>;
  if (message.unreadable) return <p className="text-sm italic text-slate-400">This message could not be opened.</p>;
  if (p.t === 'text') return <p className="whitespace-pre-wrap break-words text-sm text-white">{messageText(message)}</p>;
  if (p.t === 'comment') return <p className="whitespace-pre-wrap break-words text-sm text-white"><span className="text-slate-400">On a picture: </span>{messageText(message)}</p>;
  if (p.t === 'image' && typeof p.blob === 'string') {
    return (
      <div>
        <KeepsakePicture keepsake={keepsake} blob={p.blob} mime={p.mime} alt={typeof p.name === 'string' ? p.name : 'Picture'} />
        {typeof p.caption === 'string' && p.caption && <p className="mt-1 whitespace-pre-wrap break-words text-sm text-white">{p.caption}</p>}
      </div>
    );
  }
  if (p.t === 'video') return <KeepsakeClip keepsake={keepsake} payload={p} />;
  return <p className="text-sm italic text-slate-400">Something this version cannot show.</p>;
}

type Tab = 'messages' | 'gallery' | 'beloved';

/**
 * The backup, read-only, decrypted on this device: the thread with dates,
 * authors, edits and reactions, the pictures and clips, the gallery and Beloved.
 */
function KeepsakeView({
  keepsake,
  names,
  me,
  file,
  token,
  onClose,
  onRestore,
  onHold,
}: {
  keepsake: Keepsake;
  names: Record<string, string>;
  me?: string;
  file: File | null;
  token?: string;
  onClose: () => void;
  onRestore: () => void;
  /** A save sheet is open over the app: leaving it is not leaving the backup. */
  onHold: (on: boolean) => void;
}) {
  const [tab, setTab] = useState<Tab>('messages');
  const [saving, setSaving] = useState(false);
  const right = me && keepsake.members.includes(me) ? me : keepsake.members[1];
  const byId = useMemo(() => new Map(keepsake.messages.map((m) => [m.id, m])), [keepsake]);
  /** The ids of the first message of each day, where a day heading goes. */
  const dayStarts = useMemo(() => {
    const starts = new Set<number>();
    let last = '';
    for (const m of keepsake.messages) {
      const day = dayLabel(m.at);
      if (day !== last) starts.add(m.id);
      last = day;
    }
    return starts;
  }, [keepsake]);
  const tabs: Array<[Tab, string, typeof MessageSquare, number]> = [
    ['messages', 'Messages', MessageSquare, keepsake.messages.filter((m) => !m.deleted).length],
    ['gallery', 'Gallery', Images, keepsake.gallery.length],
    ['beloved', 'Beloved', Heart, keepsake.beloved.length],
  ];

  const savePage = async () => {
    if (!file || saving) return;
    setSaving(true);
    onHold(true);
    try {
      const source = await backupFromFile(file);
      const saved = await saveFile(await buildKeepsakePage(source), pageFileName(backupFileName(new Date(keepsake.madeAt * 1000))), token);
      if (saved) toast.success('Encrypted page saved. It asks for the chat password.');
    } catch (e) {
      toast.error(errorText(e, 'Could not save the page'));
    } finally {
      onHold(false);
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[85] flex flex-col bg-slate-950" role="dialog" aria-modal="true" aria-label="Private chat backup" data-testid="private-keepsake">
      <header className="flex items-center gap-2 border-b border-white/10 px-2 pb-2 pt-[max(0.5rem,var(--safe-top,0px))]">
        <button type="button" onClick={onClose} aria-label="Close the backup" className="flex min-h-11 min-w-11 items-center justify-center rounded-xl text-slate-300" data-testid="private-keepsake-close">
          <ArrowLeft size={18} />
        </button>
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-base font-bold text-white">Backup of {new Date(keepsake.madeAt * 1000).toLocaleDateString([], { dateStyle: 'medium' })}</h2>
          <p className="truncate text-xs text-slate-400">Read-only, opened on this device. Nothing leaves it.</p>
        </div>
      </header>
      <div role="tablist" aria-label="Backup" className="grid grid-cols-3 gap-1 border-b border-white/10 p-2">
        {tabs.map(([id, label, Icon, count]) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={tab === id}
            onClick={() => setTab(id)}
            data-testid={`private-keepsake-tab-${id}`}
            className={`flex min-h-11 min-w-0 items-center justify-center gap-1.5 rounded-lg px-1 text-sm ${tab === id ? 'bg-rose-500/15 font-semibold text-white' : 'text-slate-300'}`}
          >
            <Icon size={14} className="shrink-0" aria-hidden />
            <span className="truncate">{label}</span>
            <span className="shrink-0 text-xs text-slate-400">{count}</span>
          </button>
        ))}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3" data-testid="private-keepsake-body">
        <div className="mx-auto max-w-2xl">
          {tab === 'messages' && (
            <ol className="space-y-2" aria-label="Messages">
              {keepsake.messages.length === 0 && <li className="text-sm text-slate-400">No messages in this backup.</li>}
              {keepsake.messages.map((message) => {
                const showDay = dayStarts.has(message.id);
                const mine = message.by === right;
                const quoted = message.replyTo ? byId.get(message.replyTo) : undefined;
                return (
                  <li key={message.id}>
                    {showDay && <p className="my-3 text-center text-xs font-semibold text-slate-400">{dayLabel(message.at)}</p>}
                    <div className={`flex ${mine ? 'justify-end' : 'justify-start'}`}>
                      <div
                        className={`min-w-0 max-w-[85%] rounded-2xl border px-3 py-2 ${mine ? 'border-rose-400/30 bg-rose-500/15' : 'border-white/10 bg-white/[0.05]'}`}
                        data-testid="private-keepsake-message"
                      >
                        <p className="mb-0.5 text-xs text-slate-400">
                          <span className="font-semibold text-slate-200">{displayName(names, message.by)}</span> {timeLabel(message.at)}
                          {message.edit && <span> · Edited</span>}
                        </p>
                        {quoted && !quoted.deleted && (
                          <p className="mb-1 truncate border-l-2 border-white/20 pl-2 text-xs text-slate-400">
                            {messageText(quoted) || 'A picture or video'}
                          </p>
                        )}
                        <MessageBody keepsake={keepsake} message={message} />
                        {message.reactions.length > 0 && (
                          <p className="mt-1 flex flex-wrap gap-1 text-xs text-slate-300" aria-label="Reactions">
                            {message.reactions.map((r, i) => (
                              <span key={`${r.by}-${i}`} className="rounded-full bg-white/10 px-2 py-0.5">
                                {r.emoji} {displayName(names, r.by)}
                              </span>
                            ))}
                          </p>
                        )}
                      </div>
                    </div>
                  </li>
                );
              })}
            </ol>
          )}
          {tab === 'gallery' && (
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
              {keepsake.gallery.length === 0 && <p className="col-span-full text-sm text-slate-400">No pictures added straight to the gallery.</p>}
              {keepsake.gallery.map((item) => (
                <figure key={item.id} className="min-w-0 rounded-xl border border-white/10 bg-white/[0.04] p-1.5">
                  <KeepsakePicture keepsake={keepsake} blob={item.payload.blob} mime={item.payload.mime} alt={String(item.payload.name || 'Picture')} />
                  <figcaption className="mt-1 truncate text-xs text-slate-400">
                    {dayLabel(typeof item.payload.taken_at === 'number' ? item.payload.taken_at : item.at)}
                  </figcaption>
                </figure>
              ))}
            </div>
          )}
          {tab === 'beloved' && (
            <div className="space-y-4">
              {keepsake.beloved.length === 0 && <p className="text-sm text-slate-400">Nothing from Beloved in this backup.</p>}
              {BELOVED_SECTIONS.map(([kind, heading]) => {
                const items = keepsake.beloved.filter((b) => b.kind === kind);
                if (!items.length) return null;
                return (
                  <section key={kind} aria-label={heading}>
                    <h3 className="mb-1 text-sm font-semibold text-rose-200">{heading}</h3>
                    <ul className="space-y-2">
                      {items.map((item) => {
                        const { title, lines } = describeBeloved(item.kind, item.record);
                        const cover = (item.record as { cover?: { blob?: string; mime?: string } }).cover;
                        return (
                          <li key={item.id} className="rounded-xl border border-white/10 bg-white/[0.04] p-3" data-testid="private-keepsake-beloved">
                            <p className="break-words text-sm font-semibold text-white">{title}</p>
                            <p className="text-xs text-slate-400">
                              {displayName(names, item.by)}, {dayLabel(item.at)}
                            </p>
                            {lines.map((line, i) => (
                              <p key={i} className="mt-1 whitespace-pre-wrap break-words text-sm text-slate-200">{line}</p>
                            ))}
                            {cover?.blob && <KeepsakePicture keepsake={keepsake} blob={cover.blob} mime={cover.mime} alt="Cover photo" />}
                          </li>
                        );
                      })}
                    </ul>
                  </section>
                );
              })}
            </div>
          )}
        </div>
      </div>
      <footer className="border-t border-white/10 px-3 pt-2 pb-[max(0.75rem,var(--safe-bottom,0px))]">
        <div className="mx-auto flex max-w-2xl flex-col gap-2 sm:flex-row">
          <button type="button" onClick={onRestore} className={primary} data-testid="private-keepsake-restore">
            <RotateCcw size={15} aria-hidden /> Restore into Jarvis
          </button>
          <button type="button" onClick={() => void savePage()} disabled={saving || !file} className={secondary} data-testid="private-keepsake-page">
            {saving ? <Loader2 size={15} className="animate-spin" /> : <FileLock2 size={15} aria-hidden />} Save as an encrypted page
          </button>
        </div>
      </footer>
    </div>
  );
}

function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

// --- Restore ------------------------------------------------------------------------

function RestoreSheet({
  opened,
  keepsake,
  names,
  onBack,
  onClose,
  onRestored,
}: {
  opened: OpenedBackup;
  keepsake: Keepsake;
  names: Record<string, string>;
  onBack: () => void;
  onClose: () => void;
  onRestored: (token: string, created: boolean) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<RestoreProgress | null>(null);
  const [merge, setMerge] = useState<{ messages: number } | null>(null);
  const [error, setError] = useState('');
  const counts = {
    messages: keepsake.messages.filter((m) => !m.deleted).length,
    pictures: keepsake.blobs.size,
    gallery: keepsake.gallery.length,
    beloved: keepsake.beloved.length,
  };

  const run = async (withMerge: boolean) => {
    setBusy(true);
    setError('');
    try {
      const out = await restoreBackup(opened, privateChatRestoreClient, { merge: withMerge, onProgress: setProgress });
      const added = out.result.messages?.added ?? 0;
      toast.success(out.created ? 'The chat is back. Unlock it with the chat password.' : `Backup merged: ${added} message${added === 1 ? '' : 's'} added.`);
      onRestored(opened.token, out.created);
    } catch (e) {
      const data = (e as { response?: { status?: number; data?: { needs_merge?: boolean; counts?: { messages?: number } } } }).response;
      if (data?.status === 409 && data.data?.needs_merge) {
        setMerge({ messages: data.data.counts?.messages ?? 0 });
      } else {
        setError(errorText(e, 'Could not restore the backup'));
      }
    } finally {
      setBusy(false);
      setProgress(null);
    }
  };

  const status = progress
    ? progress.step === 'pictures'
      ? `Putting pictures back: ${progress.done} of ${progress.total}`
      : progress.step === 'messages'
        ? `Putting messages back: part ${progress.done} of ${progress.total}`
        : 'Starting…'
    : '';

  return (
    <Sheet title="Restore into Jarvis" onClose={onClose} testId="private-restore">
      <div className="space-y-3">
        <p className="text-sm leading-relaxed text-slate-300">
          The private chat between {keepsake.members.map((m) => displayName(names, m)).join(' and ')}, as it was on{' '}
          {new Date(keepsake.madeAt * 1000).toLocaleDateString([], { dateStyle: 'medium' })}: {plural(counts.messages, 'message')},{' '}
          {plural(counts.pictures, 'encrypted picture or video file', 'encrypted picture and video files')}, {plural(counts.gallery, 'gallery picture')} and{' '}
          {plural(counts.beloved, 'Beloved record')}.
        </p>
        <Notice>
          It goes back still encrypted: Jarvis receives the same sealed messages and pictures it kept before, never a word of them. Messages
          already in the chat stay as they are, and nothing is added twice.
        </Notice>
        {merge ? (
          <div className="rounded-xl border border-amber-400/25 bg-amber-500/10 p-3 text-sm text-amber-100" role="alert" data-testid="private-restore-merge">
            <p className="flex items-center gap-2 font-semibold">
              <TriangleAlert size={15} aria-hidden /> This chat already has {merge.messages} messages here
            </p>
            <p className="mt-1 leading-relaxed">Merge the backup into it? Only what is missing is added; nothing here is changed or duplicated.</p>
          </div>
        ) : null}
        {status && (
          <p className="text-sm text-slate-300" role="status" data-testid="private-restore-status">
            {status}
          </p>
        )}
        {error && (
          <p className="text-sm text-rose-200" role="alert" data-testid="private-restore-error">
            {error}
          </p>
        )}
        <div className="flex flex-col-reverse gap-2 sm:flex-row">
          <button type="button" onClick={onBack} disabled={busy} className={secondary}>
            Back
          </button>
          <button type="button" onClick={() => void run(Boolean(merge))} disabled={busy} className={primary} data-testid={merge ? 'private-restore-merge-confirm' : 'private-restore-start'}>
            {busy ? <Loader2 size={15} className="animate-spin" /> : <RotateCcw size={15} aria-hidden />}
            {busy ? 'Restoring…' : merge ? 'Merge the backup' : 'Restore'}
          </button>
        </div>
      </div>
    </Sheet>
  );
}
