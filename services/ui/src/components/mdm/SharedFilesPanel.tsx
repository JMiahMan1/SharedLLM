import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { BookOpen, Download, FileText, GraduationCap, Loader2, X } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { api } from '../../services/api';
import { artifactKind, downloadBlobUrl } from '../../lib/artifactKinds';
import { ImageViewer } from '../workspace/viewers/ImageViewer';
import { PdfViewer } from '../workspace/viewers/PdfViewer';
import type { MdmLibraryBook, MdmSharedFile } from '../../types/api';

/**
 * The child's own view of the files a parent pushed to them (HANDOVER.md Phase
 * 12): the shared School and Library folders from `/api/mdm/files/for-me`, and
 * the Library set read-only from the Calibre shelf. A file opens in an in-app
 * viewer (reusing the workspace PDF and image viewers) or downloads, because a
 * plain link cannot carry the API key the route needs.
 *
 * Every state is explicit: loading, empty, and an error that names what could
 * not be read. Nothing here invents a file.
 */

const TOUCH = 'min-h-11 pointer-coarse:min-h-11';

const FOLDER_META: Record<string, { label: string; icon: LucideIcon }> = {
  school: { label: 'School', icon: GraduationCap },
  library: { label: 'Library', icon: BookOpen },
};

type ViewerTarget = { name: string; url: string; kind: string; text?: string };

function useViewer() {
  const [viewer, setViewer] = useState<ViewerTarget | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const replace = (next: ViewerTarget | null) => {
    setViewer((prev) => {
      if (prev) URL.revokeObjectURL(prev.url);
      return next;
    });
  };
  const open = async (key: string, name: string, fetcher: () => Promise<Blob>) => {
    setBusy(key);
    setError(null);
    try {
      const blob = await fetcher();
      const kind = artifactKind(name);
      const url = URL.createObjectURL(blob);
      const text = kind === 'text' ? await blob.text() : undefined;
      replace({ name, url, kind, text });
    } catch {
      setError(`Could not open ${name}.`);
    } finally {
      setBusy(null);
    }
  };
  const download = async (name: string, fetcher: () => Promise<Blob>) => {
    setError(null);
    try {
      downloadBlobUrl(URL.createObjectURL(await fetcher()), name);
    } catch {
      setError(`Could not download ${name}.`);
    }
  };
  return { viewer, busy, error, open, download, close: () => replace(null) };
}

function Viewer({ target, onClose }: { target: ViewerTarget; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-black/80 backdrop-blur-sm" role="dialog" aria-label={`${target.name} preview`}>
      <header className="flex items-center gap-2 border-b border-white/10 bg-slate-900/90 px-3 py-2">
        <span className="min-w-0 flex-1 truncate text-sm text-white">{target.name}</span>
        <a href={target.url} download={target.name} className={`glass-button px-3 text-xs ${TOUCH}`}>
          <Download size={14} /> Download
        </a>
        <button type="button" onClick={onClose} aria-label="Close preview" className={`rounded-lg p-2 text-slate-300 hover:text-white ${TOUCH}`}>
          <X size={18} />
        </button>
      </header>
      <div className="min-h-0 flex-1 overflow-auto bg-slate-950">
        {target.kind === 'pdf' && <PdfViewer url={target.url} />}
        {target.kind === 'image' && <ImageViewer url={target.url} />}
        {target.kind === 'text' && (
          <pre className="whitespace-pre-wrap break-words p-4 text-sm text-slate-200">{target.text}</pre>
        )}
        {target.kind === 'audio' && <audio controls src={target.url} className="m-4 w-[calc(100%-2rem)]" />}
        {target.kind === 'video' && <video controls src={target.url} className="mx-auto max-h-full" />}
        {target.kind === 'other' && (
          <p className="p-4 text-sm text-slate-400">
            This file type opens with your device&apos;s own viewer. Use Download to save it.
          </p>
        )}
      </div>
    </div>
  );
}

function FileRow({ name, busy, onOpen, onDownload }: {
  name: string;
  busy: boolean;
  onOpen: () => void;
  onDownload: () => void;
}) {
  return (
    <li className="flex items-center gap-2">
      <button type="button" onClick={onOpen} disabled={busy}
        className={`flex min-w-0 flex-1 items-center gap-2 rounded-lg px-2 py-2 text-left text-sm text-slate-200 hover:bg-white/5 ${TOUCH}`}>
        {busy ? <Loader2 size={14} className="shrink-0 animate-spin" /> : <FileText size={14} className="shrink-0 text-sky-400" />}
        <span className="min-w-0 truncate">{name}</span>
      </button>
      <button type="button" onClick={onDownload} aria-label={`Download ${name}`}
        className={`shrink-0 rounded-lg p-2 text-slate-400 hover:text-white ${TOUCH}`}>
        <Download size={14} />
      </button>
    </li>
  );
}

function BookRow({ book, busy, onOpen, onDownload }: {
  book: MdmLibraryBook;
  busy: boolean;
  onOpen: () => void;
  onDownload: () => void;
}) {
  return (
    <li className="flex items-center gap-2">
      <button type="button" onClick={onOpen} disabled={busy}
        className={`flex min-w-0 flex-1 items-center gap-2 rounded-lg px-2 py-2 text-left text-sm text-slate-200 hover:bg-white/5 ${TOUCH}`}>
        {busy ? <Loader2 size={14} className="shrink-0 animate-spin" /> : <BookOpen size={14} className="shrink-0 text-emerald-400" />}
        <span className="min-w-0 flex-1 truncate">{book.name}</span>
        <span className="shrink-0 text-[10px] uppercase tracking-wider text-slate-500">{book.format}</span>
      </button>
      <button type="button" onClick={onDownload} aria-label={`Download ${book.name}`}
        className={`shrink-0 rounded-lg p-2 text-slate-400 hover:text-white ${TOUCH}`}>
        <Download size={14} />
      </button>
    </li>
  );
}

function FolderCard({ folder, files }: { folder: string; files: MdmSharedFile[] }) {
  const meta = FOLDER_META[folder] ?? { label: folder, icon: BookOpen };
  const Icon = meta.icon;
  const { viewer, busy, error, open, download, close } = useViewer();
  const isLibrary = folder === 'library';
  const library = useQuery({
    queryKey: ['mdm-library-books'],
    queryFn: () => api.getMdmLibraryBooks(),
    enabled: isLibrary,
    retry: false,
  });

  return (
    <section className="glass-panel overflow-hidden rounded-2xl border border-white/10" data-testid={`mdm-for-me-${folder}`}>
      <header className="flex items-center justify-between gap-3 border-b border-white/5 bg-sky-500/5 px-4 py-3">
        <span className="flex items-center gap-2 text-[10px] font-black uppercase tracking-widest text-sky-400">
          <Icon size={14} /> {meta.label}
        </span>
        <span className="text-xs text-slate-500">{files.length} file(s)</span>
      </header>
      <div className="space-y-2 p-4">
        {files.length === 0 ? (
          <p className="text-xs text-slate-500">No files in this folder yet.</p>
        ) : (
          <ul className="space-y-1">
            {files.map((f) => (
              <FileRow
                key={f.name}
                name={f.name}
                busy={busy === `file:${f.name}`}
                onOpen={() => void open(`file:${f.name}`, f.name, () => api.getMdmFileBlob(folder, f.name))}
                onDownload={() => void download(f.name, () => api.getMdmFileBlob(folder, f.name))}
              />
            ))}
          </ul>
        )}

        {isLibrary && (
          <div className="border-t border-white/5 pt-2">
            <p className="text-[10px] uppercase tracking-wider text-slate-500">From the family library</p>
            {library.isLoading && <p className="mt-1 text-xs text-slate-500">Loading library…</p>}
            {library.error && <p className="mt-1 text-xs text-amber-300/90">The family library could not be loaded.</p>}
            {library.data && !library.data.configured && (
              <p className="mt-1 text-xs text-slate-500">{library.data.reason}</p>
            )}
            {library.data?.configured && library.data.error && (
              <p className="mt-1 text-xs text-amber-300/90">{library.data.error}</p>
            )}
            {library.data?.configured && !library.data.error && library.data.books.length === 0 && (
              <p className="mt-1 text-xs text-slate-500">The family library has no books yet.</p>
            )}
            <ul className="mt-1 space-y-1">
              {library.data?.books.map((b) => (
                <BookRow
                  key={b.path}
                  book={b}
                  busy={busy === `book:${b.path}`}
                  onOpen={() => void open(`book:${b.path}`, b.name, () => api.getMdmLibraryBookBlob(b.path))}
                  onDownload={() => void download(b.name, () => api.getMdmLibraryBookBlob(b.path))}
                />
              ))}
            </ul>
            {library.data?.truncated && (
              <p className="mt-1 text-xs text-slate-500">
                Showing the first {library.data.books.length} of {library.data.count} books.
              </p>
            )}
          </div>
        )}
      </div>
      {error && <p className="border-t border-white/5 px-4 py-2 text-xs text-amber-300/90">{error}</p>}
      {viewer && <Viewer target={viewer} onClose={close} />}
    </section>
  );
}

export default function SharedFilesPanel() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['mdm-files-for-me'],
    queryFn: () => api.getMdmFilesForMe(),
    retry: false,
  });

  if (isLoading) {
    return <p className="text-sm text-slate-500" data-testid="mdm-for-me-loading">Loading shared files…</p>;
  }
  if (error || !data) {
    return (
      <p className="text-sm text-amber-300/90" data-testid="mdm-for-me-error">
        Shared files could not be loaded.
      </p>
    );
  }
  if (data.folders.length === 0) {
    return (
      <p className="text-sm text-slate-500" data-testid="mdm-for-me-empty">
        No shared folders have been pushed to you yet.
      </p>
    );
  }
  return (
    <div className="space-y-3 sm:space-y-4" data-testid="mdm-for-me">
      {data.folders.map((f) => <FolderCard key={f.folder} folder={f.folder} files={f.files} />)}
    </div>
  );
}
