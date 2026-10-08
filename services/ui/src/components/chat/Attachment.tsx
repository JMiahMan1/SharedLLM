import { useEffect, useState } from 'react';
import { Download, FileText, Loader2, Mic, Play, X } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import { formatBytes, type TalkParameter } from './chatModel';

type FileParameter = TalkParameter;

// One object URL per file for the life of the page: a chat re-renders every
// few seconds, and refetching a photo each time would flicker and waste data.
const blobCache = new Map<string, Promise<string>>();

function cachedUrl(key: string, load: () => Promise<Blob>): Promise<string> {
  let entry = blobCache.get(key);
  if (!entry) {
    entry = load().then((blob) => URL.createObjectURL(blob));
    entry.catch(() => blobCache.delete(key));
    blobCache.set(key, entry);
  }
  return entry;
}

const previewUrl = (file: FileParameter, size: number) =>
  cachedUrl(`preview:${file.id}:${size}`, () => api.getTalkFileBlob({ file_id: file.id, preview: true, size }));

const fileUrl = (file: FileParameter) =>
  cachedUrl(`file:${file.path || file.id}`, () => api.getTalkFileBlob({ path: file.path, file_id: file.id }));

/** What a shared file looks like in a bubble, chosen by its type. */
export default function Attachment({ file, voice = false }: { file: FileParameter; voice?: boolean }) {
  const mime = file.mimetype || '';
  if (mime.startsWith('image/') && file['preview-available'] !== 'no') return <ImageAttachment file={file} />;
  if (voice || mime.startsWith('audio/')) return <MediaAttachment file={file} kind="audio" voice={voice} />;
  if (mime.startsWith('video/')) return <MediaAttachment file={file} kind="video" />;
  return <FileCard file={file} />;
}

function ImageAttachment({ file }: { file: FileParameter }) {
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState(false);
  const [full, setFull] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    previewUrl(file, 640)
      .then((url) => live && setSrc(url))
      .catch(() => live && setFailed(true));
    return () => {
      live = false;
    };
  }, [file]);

  useEffect(() => {
    if (!open || full) return;
    let live = true;
    // The full image only when someone actually opens it.
    (file.path ? fileUrl(file) : previewUrl(file, 2048))
      .then((url) => live && setFull(url))
      .catch(() => live && setFull(src));
    return () => {
      live = false;
    };
  }, [open, full, file, src]);

  if (failed) return <FileCard file={file} />;
  return (
    <>
      <button
        type="button"
        onClick={(event) => {
          event.stopPropagation();
          setOpen(true);
        }}
        className="block overflow-hidden rounded-2xl bg-black/30"
        aria-label={`Open photo ${file.name || ''}`}
      >
        {src ? (
          <img src={src} alt={file.name || 'Photo'} className="max-h-72 w-auto max-w-full object-cover" loading="lazy" />
        ) : (
          <span className="flex h-40 w-56 items-center justify-center">
            <Loader2 size={20} className="animate-spin text-slate-400" />
          </span>
        )}
      </button>
      {open && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/90 p-4"
          role="dialog"
          aria-modal="true"
          aria-label={file.name || 'Photo'}
          onClick={() => setOpen(false)}
          onKeyDown={(event) => event.key === 'Escape' && setOpen(false)}
        >
          <button type="button" aria-label="Close photo" className="absolute right-4 top-4 rounded-full bg-white/10 p-2 text-white" onClick={() => setOpen(false)}>
            <X size={20} />
          </button>
          {full || src ? (
            <img src={full || src || ''} alt={file.name || 'Photo'} className="max-h-full max-w-full object-contain" />
          ) : (
            <Loader2 className="animate-spin text-white" />
          )}
          {full && (
            <a
              href={full}
              download={file.name || 'photo'}
              onClick={(event) => event.stopPropagation()}
              className="absolute bottom-6 right-6 inline-flex items-center gap-2 rounded-full bg-white/10 px-4 py-2 text-sm text-white"
            >
              <Download size={15} /> Save
            </a>
          )}
        </div>
      )}
    </>
  );
}

function MediaAttachment({ file, kind, voice = false }: { file: FileParameter; kind: 'audio' | 'video'; voice?: boolean }) {
  const [src, setSrc] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // Loaded on demand: a long thread of voice notes should not download them all.
  const load = async () => {
    setLoading(true);
    try {
      setSrc(await fileUrl(file));
    } catch {
      toast.error('Could not load that recording');
    } finally {
      setLoading(false);
    }
  };

  if (src) {
    return kind === 'audio' ? (
      <audio controls autoPlay src={src} className="h-10 w-64 max-w-full" onClick={(e) => e.stopPropagation()} />
    ) : (
      <video controls autoPlay src={src} className="max-h-72 max-w-full rounded-2xl" onClick={(e) => e.stopPropagation()} />
    );
  }
  return (
    <span
      role="button"
      tabIndex={0}
      onClick={(event) => {
        event.stopPropagation();
        void load();
      }}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          event.stopPropagation();
          void load();
        }
      }}
      className="flex min-w-48 items-center gap-3 rounded-2xl bg-black/20 px-3 py-2"
      aria-label={`Play ${voice ? 'voice message' : file.name || kind}`}
    >
      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-white/15">
        {loading ? <Loader2 size={16} className="animate-spin" /> : <Play size={16} className="ml-0.5" />}
      </span>
      <span className="min-w-0">
        <span className="flex items-center gap-1 text-sm font-medium">
          {voice && <Mic size={13} />} {voice ? 'Voice message' : file.name}
        </span>
        <span className="block text-[11px] opacity-70">{formatBytes(file.size)}</span>
      </span>
    </span>
  );
}

function FileCard({ file }: { file: FileParameter }) {
  const [busy, setBusy] = useState(false);
  const download = async () => {
    setBusy(true);
    try {
      const url = await fileUrl(file);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = file.name || 'file';
      anchor.click();
    } catch {
      toast.error('Could not download that file');
    } finally {
      setBusy(false);
    }
  };
  return (
    <span
      role="button"
      tabIndex={0}
      onClick={(event) => {
        event.stopPropagation();
        void download();
      }}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          event.stopPropagation();
          void download();
        }
      }}
      className="flex min-w-48 max-w-72 items-center gap-3 rounded-2xl bg-black/20 px-3 py-2"
      aria-label={`Download ${file.name || 'file'}`}
    >
      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-white/15">
        <FileText size={17} />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium">{file.name || 'File'}</span>
        <span className="block text-[11px] opacity-70">{formatBytes(file.size) || (file.mimetype ?? '')}</span>
      </span>
      {busy ? <Loader2 size={15} className="animate-spin" /> : <Download size={15} className="opacity-70" />}
    </span>
  );
}
