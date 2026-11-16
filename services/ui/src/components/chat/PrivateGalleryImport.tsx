import { useEffect, useRef, useState } from 'react';
import { CheckCircle2, FolderArchive, ImagePlus, Info, Loader2, Square, X } from 'lucide-react';
import { api } from '../../services/api';
import type { PrivateVault } from '../../lib/privateChatCrypto';
import { pickerDone, pickerOpening } from '../../lib/privateChatSession';
import { importPictures, sortPicked, zipSources, type ImportProgress, type ImportResult, type ImportSource, type Known } from '../../lib/galleryImport';
import { errorText } from './privateChatModel';

type Mode = 'phone' | 'between';

interface Outcome extends ImportResult {
  videos: number;
  other: number;
}

/**
 * Add pictures to the private gallery without sending them in the chat: from
 * the phone, or a whole export from the Between couples app (its pictures, or
 * the zip of a Between backup). Each picture is encrypted on this device like a
 * sent one, its original bytes kept as they were (EXIF and all), and recorded
 * as a sealed gallery entry for both parents. No chat message, so no unread
 * count and no notification. The originals stay on the phone; this app never
 * deletes them, and says so.
 */
export default function PrivateGalleryImport({
  token,
  vault,
  known,
  onClose,
  onAdded,
}: {
  token: string;
  vault: PrivateVault;
  /** What is already in the gallery, to skip duplicates. */
  known: Known;
  onClose: () => void;
  /** Called after each picture is added, so the gallery can show it. */
  onAdded: () => void;
}) {
  const phoneRef = useRef<HTMLInputElement | null>(null);
  const betweenRef = useRef<HTMLInputElement | null>(null);
  const stopRef = useRef(false);
  const [busy, setBusy] = useState(false);
  const [preparing, setPreparing] = useState('');
  const [progress, setProgress] = useState<ImportProgress | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [problem, setProblem] = useState('');

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !busy) onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [busy, onClose]);

  const choose = (input: HTMLInputElement | null) => {
    if (!input) return;
    // On Android the picker sends the app to the background: hold the lock until the pictures are back.
    pickerOpening(token);
    input.click();
  };

  const run = async (files: File[], mode: Mode) => {
    pickerDone(token);
    if (!files.length) return;
    setBusy(true);
    setProblem('');
    setOutcome(null);
    stopRef.current = false;
    try {
      const picked = sortPicked(files);
      const sources: ImportSource[] = [...picked.pictures];
      const videos = [...picked.videos];
      let captions = new Map<string, string>();
      for (const zip of picked.zips) {
        setPreparing(`Reading ${zip.name}…`);
        const inside = await zipSources(zip);
        sources.push(...inside.pictures);
        videos.push(...inside.videos);
        captions = new Map([...captions, ...inside.captions]);
      }
      setPreparing('');
      // Oldest first, so an import that stops half way has filled in the years in order.
      sources.sort((a, b) => (a.fileTime ?? 0) - (b.fileTime ?? 0) || a.name.localeCompare(b.name));
      let lastAdded = 0;
      const result = await importPictures(
        sources,
        known,
        {
          encrypt: (plain) => vault.encrypt(plain),
          upload: async (sealed) => (await api.uploadPrivateBlob(token, sealed)).id,
          seal: (payload) => vault.seal(payload),
          addEntry: async (sealed) => {
            await api.addPrivateGalleryEntry(token, sealed);
          },
          deleteBlob: (blob) => api.deletePrivateBlob(token, blob),
        },
        {
          source: mode === 'between' ? 'between' : picked.zips.length ? 'zip' : 'phone',
          captions,
          shouldStop: () => stopRef.current,
          onProgress: (p) => {
            setProgress(p);
            // Show new pictures as they arrive, without refetching after every single one.
            if (p.added > lastAdded && (p.added - lastAdded >= 10 || p.done === p.total)) {
              lastAdded = p.added;
              onAdded();
            }
          },
        },
      );
      if (result.added) onAdded();
      setOutcome({ ...result, videos: videos.length, other: picked.other.length });
    } catch (error) {
      setProblem(errorText(error, 'Could not read those files.'));
    } finally {
      setPreparing('');
      setBusy(false);
    }
  };

  const percent = progress && progress.total ? Math.round((progress.done / progress.total) * 100) : 0;

  return (
    <div className="fixed inset-0 z-[95] flex items-end justify-center bg-black/70 sm:items-center sm:p-4" role="dialog" aria-modal="true" aria-label="Add pictures" data-testid="private-gallery-import">
      <div className="flex max-h-[92dvh] w-full max-w-lg flex-col overflow-hidden rounded-t-3xl border border-white/10 bg-slate-900 sm:rounded-3xl">
        <div className="flex shrink-0 items-center gap-2 border-b border-white/5 px-3 py-2">
          <h2 className="min-w-0 flex-1 truncate pl-1 text-base font-bold text-white">Add pictures</h2>
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            aria-label="Close"
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-slate-200 hover:bg-white/10 disabled:opacity-50"
          >
            <X size={20} />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 pb-[max(1rem,env(safe-area-inset-bottom))] pt-3">
          <p className="text-sm leading-relaxed text-slate-300">
            Pictures added here go into this gallery only: encrypted on this phone, seen by the two of you, and never posted in the chat. They keep their
            original file, with the date and details the camera saved.
          </p>

          <input
            ref={phoneRef}
            type="file"
            accept="image/*"
            multiple
            className="hidden"
            data-testid="private-import-phone-input"
            onChange={(event) => {
              const files = Array.from(event.target.files ?? []);
              event.target.value = '';
              void run(files, 'phone');
            }}
          />
          <input
            ref={betweenRef}
            type="file"
            accept="image/*,.zip,application/zip"
            multiple
            className="hidden"
            data-testid="private-import-between-input"
            onChange={(event) => {
              const files = Array.from(event.target.files ?? []);
              event.target.value = '';
              void run(files, 'between');
            }}
          />

          {!busy && (
            <div className="mt-4 grid gap-2">
              <button
                type="button"
                onClick={() => choose(phoneRef.current)}
                data-testid="private-import-phone"
                className="flex min-h-12 items-center justify-center gap-2 rounded-xl bg-rose-600 px-3 text-sm font-semibold text-white"
              >
                <ImagePlus size={17} aria-hidden /> Choose pictures
              </button>
              <section className="rounded-2xl border border-white/10 bg-white/[0.03] p-3" aria-label="Import from Between">
                <h3 className="text-sm font-semibold text-white">Import from Between</h3>
                <ol className="mt-1.5 list-decimal space-y-1 pl-5 text-xs leading-relaxed text-slate-300">
                  <li>In Between, save your pictures to the phone: open the album and download all photos (Between Plus), or save the ones you want from the chat.</li>
                  <li>Or, with Between Plus, make a full backup and save the zip it gives you.</li>
                  <li>Choose the saved pictures, or the zip, here. Each lands on the day it was taken.</li>
                </ol>
                <button
                  type="button"
                  onClick={() => choose(betweenRef.current)}
                  data-testid="private-import-between"
                  className="mt-2 flex min-h-11 w-full items-center justify-center gap-2 rounded-xl bg-white/[0.08] px-3 text-sm font-semibold text-slate-100"
                >
                  <FolderArchive size={16} aria-hidden /> Choose Between pictures or zip
                </button>
                <p className="mt-2 text-[11px] leading-relaxed text-slate-400">
                  Photos only: videos are left out, since the private chat does not play them. Running it again skips anything already here.
                </p>
              </section>
            </div>
          )}

          {busy && (
            <div className="mt-4" role="status" aria-live="polite" data-testid="private-import-progress">
              {preparing ? (
                <p className="flex items-center gap-2 text-sm text-slate-300">
                  <Loader2 size={14} className="animate-spin" /> {preparing}
                </p>
              ) : (
                <>
                  <p className="text-sm font-semibold text-white">
                    Adding {Math.min((progress?.done ?? 0) + 1, progress?.total ?? 0)} of {progress?.total ?? 0}
                  </p>
                  <div className="mt-2 h-2 overflow-hidden rounded-full bg-white/10" aria-hidden>
                    <div className="h-full rounded-full bg-rose-500 transition-[width]" style={{ width: `${percent}%` }} />
                  </div>
                  <p className="mt-1 truncate text-xs text-slate-400">{progress?.current ?? ''}</p>
                </>
              )}
              <button
                type="button"
                onClick={() => {
                  stopRef.current = true;
                }}
                data-testid="private-import-stop"
                className="mt-3 flex min-h-11 items-center gap-1.5 rounded-xl px-3 text-sm font-semibold text-slate-200 hover:bg-white/10"
              >
                <Square size={13} aria-hidden /> Stop after this picture
              </button>
            </div>
          )}

          {problem && (
            <p className="mt-3 rounded-xl border border-amber-400/20 bg-amber-500/10 p-3 text-sm text-amber-100" role="alert">
              {problem}
            </p>
          )}

          {outcome && !busy && (
            <div className="mt-4 rounded-2xl border border-white/10 bg-white/[0.03] p-3" data-testid="private-import-result">
              <p className="flex items-center gap-2 text-sm font-semibold text-white">
                <CheckCircle2 size={16} className="text-emerald-300" aria-hidden />
                {outcome.added} added{outcome.skipped ? `, ${outcome.skipped} already in the gallery` : ''}
                {outcome.failed ? `, ${outcome.failed} not added` : ''}
                {outcome.stopped ? ' (stopped)' : ''}
              </p>
              {outcome.failures.length > 0 && (
                <ul className="mt-2 max-h-28 space-y-0.5 overflow-y-auto text-xs text-amber-100">
                  {outcome.failures.slice(0, 50).map((f, i) => (
                    <li key={`${f.name}-${i}`} className="break-words">
                      {f.name}: {f.reason}
                    </li>
                  ))}
                </ul>
              )}
              {(outcome.failed > 0 || outcome.stopped) && (
                <p className="mt-2 text-xs text-slate-300">Choose the same files again to finish: pictures already added are skipped.</p>
              )}
              {outcome.videos > 0 && (
                <p className="mt-2 text-xs text-slate-300">
                  {outcome.videos} {outcome.videos === 1 ? 'video was' : 'videos were'} left out: the private gallery holds photos only.
                </p>
              )}
              {outcome.other > 0 && (
                <p className="mt-2 text-xs text-slate-300">
                  {outcome.other} {outcome.other === 1 ? 'file was' : 'files were'} not a picture and left out.
                </p>
              )}
              {outcome.added > 0 && (
                <p className="mt-3 flex gap-2 rounded-xl bg-sky-500/10 p-2.5 text-xs leading-relaxed text-sky-100" data-testid="private-import-originals">
                  <Info size={14} className="mt-0.5 shrink-0" aria-hidden />
                  <span>
                    The originals are still on your phone (in its photos, Downloads or the Between folder), where other apps can see them. If you want
                    them only here, delete them yourself in your Photos or Files app, and empty its trash or recently deleted folder. Jarvis never
                    deletes your files; Android asks you to confirm every deletion.
                  </span>
                </p>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
