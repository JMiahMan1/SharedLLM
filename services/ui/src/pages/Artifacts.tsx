import { useDeferredValue, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  Download,
  Eye,
  FileArchive,
  FileAudio,
  FileImage,
  FileText,
  FileVideo,
  HardDrive,
  Pencil,
  RefreshCcw,
  RotateCcw,
  Search,
  Trash2,
} from 'lucide-react';
import toast from 'react-hot-toast';
import Modal from '../components/ui/Modal';
import { useAuth } from '../context/AuthContext';
import { errorText } from '../lib/errorText';
import { formatBytes } from '../components/chat/chatModel';
import { api } from '../services/api';
import type { Artifact } from '../types/api';

const KINDS: { value: string; label: string }[] = [
  { value: '', label: 'All kinds' },
  { value: 'tts_audio', label: 'Audio' },
  { value: 'image', label: 'Images' },
  { value: 'video', label: 'Video' },
  { value: 'document', label: 'Documents' },
  { value: 'other', label: 'Other' },
];

const kindIcon = (kind: string) => {
  if (kind === 'tts_audio') return FileAudio;
  if (kind === 'image') return FileImage;
  if (kind === 'video') return FileVideo;
  if (kind === 'document') return FileText;
  return FileArchive;
};

const title = (artifact: Artifact) =>
  artifact.name.split('/').pop() || artifact.name;

/** Managed files that outlive the workspace that made them. */
const Artifacts = () => {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const [search, setSearch] = useState('');
  const deferredSearch = useDeferredValue(search);
  const [kind, setKind] = useState('');
  const [includeDeleted, setIncludeDeleted] = useState(false);
  const [offset, setOffset] = useState(0);
  const [preview, setPreview] = useState<Artifact | null>(null);
  const [renaming, setRenaming] = useState<Artifact | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [hardDelete, setHardDelete] = useState<Artifact | null>(null);
  const [confirmPhrase, setConfirmPhrase] = useState('');
  const [previewUrl, setPreviewUrl] = useState('');
  const limit = 50;

  const usage = useQuery({
    queryKey: ['artifact-usage'],
    queryFn: () => api.getStorageUsage(),
  });

  const artifacts = useQuery({
    queryKey: ['artifacts', kind, deferredSearch, includeDeleted, offset],
    queryFn: () => api.listArtifacts({
      kind: kind || undefined,
      search: deferredSearch.trim() || undefined,
      include_deleted: includeDeleted || undefined,
      limit,
      offset,
    }),
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['artifacts'] });
    queryClient.invalidateQueries({ queryKey: ['artifact-usage'] });
    queryClient.invalidateQueries({ queryKey: ['system-storage-usage'] });
  };

  const remove = useMutation({
    mutationFn: (artifact: Artifact) => api.deleteArtifact(artifact.id),
    onSuccess: () => {
      toast.success('Moved to Recently deleted; restore it for 24 hours.');
      invalidate();
    },
    onError: (error) => toast.error(errorText(error, 'Could not delete the artifact')),
  });

  const restore = useMutation({
    mutationFn: (artifact: Artifact) => api.restoreArtifact(artifact.id),
    onSuccess: () => {
      toast.success('Artifact restored');
      invalidate();
    },
    onError: (error) => toast.error(errorText(error, 'Could not restore the artifact')),
  });

  const hardRemove = useMutation({
    mutationFn: ({ artifact, phrase }: { artifact: Artifact; phrase: string }) =>
      api.deleteArtifact(artifact.id, { hard: true, confirmPhrase: phrase }),
    onSuccess: () => {
      toast.success('Artifact permanently deleted');
      setHardDelete(null);
      setConfirmPhrase('');
      invalidate();
    },
    onError: (error) => toast.error(errorText(error, 'Could not delete the artifact')),
  });

  const rename = useMutation({
    mutationFn: ({ artifact, name }: { artifact: Artifact; name: string }) =>
      api.patchArtifact(artifact.id, { name }),
    onSuccess: () => {
      toast.success('Artifact renamed');
      setRenaming(null);
      invalidate();
    },
    onError: (error) => toast.error(errorText(error, 'Could not rename the artifact')),
  });

  const download = useMutation({
    mutationFn: (artifact: Artifact) => api.getArtifactDownloadUrl(artifact.id),
    onSuccess: (data) => {
      const link = document.createElement('a');
      link.href = data.url;
      link.download = data.name;
      document.body.appendChild(link);
      link.click();
      link.remove();
    },
    onError: (error) => toast.error(errorText(error, 'Could not prepare the download')),
  });

  const openPreview = useMutation({
    mutationFn: (artifact: Artifact) => api.getArtifactDownloadUrl(artifact.id),
    onSuccess: (data) => setPreviewUrl(data.url),
    onError: (error) => toast.error(errorText(error, 'Could not open the artifact')),
  });

  const openPreviewFor = (artifact: Artifact) => {
    setPreviewUrl('');
    setPreview(artifact);
    openPreview.mutate(artifact);
  };

  const usedPct = usage.data && usage.data.quota > 0
    ? Math.min(100, (usage.data.used / usage.data.quota) * 100)
    : 0;
  const total = artifacts.data?.total ?? 0;
  const rows = useMemo(() => artifacts.data?.artifacts ?? [], [artifacts.data]);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-black text-white flex items-center gap-2">
            <HardDrive className="text-purple-400" /> Artifacts
          </h1>
          <p className="text-sm text-slate-400 mt-1">
            Generated audio, images, video and documents that survive their workspace.
          </p>
        </div>
        <button
          onClick={() => {
            invalidate();
            void artifacts.refetch();
          }}
          className="glass-card px-3 py-2 text-xs text-slate-300 hover:text-white flex items-center gap-2"
        >
          <RefreshCcw size={14} className={artifacts.isFetching ? 'animate-spin' : ''} /> Refresh
        </button>
      </div>

      <section className="glass-card p-4 sm:p-5" aria-label="Storage usage">
        {usage.isLoading ? (
          <p className="text-sm text-slate-400">Reading storage usage…</p>
        ) : usage.isError ? (
          <p className="text-sm text-rose-300">{errorText(usage.error, 'Storage usage is unavailable')}</p>
        ) : usage.data ? (
          <div className="space-y-3">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <p className="text-sm text-slate-300">
                <span className="font-bold text-white">{formatBytes(usage.data.used) || '0 B'}</span>
                {' of '}
                <span className="font-bold text-white">{formatBytes(usage.data.quota) || '0 B'}</span>
                {' used · '}{usage.data.count} artifact{usage.data.count === 1 ? '' : 's'}
              </p>
              <p className="text-xs text-slate-500">{formatBytes(usage.data.free) || '0 B'} free on the volume</p>
            </div>
            <div className="h-2.5 rounded-full bg-slate-800 overflow-hidden">
              <div
                className={`h-full rounded-full ${usage.data.warn ? 'bg-amber-500' : 'bg-purple-500'}`}
                style={{ width: `${Math.max(usedPct > 0 ? 2 : 0, usedPct)}%` }}
              />
            </div>
            {usage.data.warn && (
              <p className="flex items-start gap-2 text-sm text-amber-200 bg-amber-500/10 border border-amber-500/30 rounded-xl p-3">
                <AlertTriangle size={17} className="shrink-0 mt-0.5" />
                You are at {Math.round(usedPct)}% of your storage. This may fail due to space; clean up or ask an admin for more.
              </p>
            )}
          </div>
        ) : null}
      </section>

      <div className="flex flex-wrap items-center gap-2">
        <label className="glass-card flex items-center gap-2 px-3 py-2 flex-1 min-w-[220px]">
          <Search size={15} className="text-slate-500" />
          <input
            value={search}
            onChange={(e) => { setSearch(e.target.value); setOffset(0); }}
            placeholder="Search artifacts"
            aria-label="Search artifacts"
            className="bg-transparent outline-none text-sm text-white placeholder:text-slate-500 w-full"
          />
        </label>
        <select
          value={kind}
          onChange={(e) => { setKind(e.target.value); setOffset(0); }}
          aria-label="Filter by kind"
          className="glass-card px-3 py-2 text-sm text-slate-200 bg-transparent"
        >
          {KINDS.map((k) => <option key={k.value} value={k.value} className="bg-slate-900">{k.label}</option>)}
        </select>
        <label className="glass-card flex items-center gap-2 px-3 py-2 text-xs text-slate-300">
          <input
            type="checkbox"
            checked={includeDeleted}
            onChange={(e) => { setIncludeDeleted(e.target.checked); setOffset(0); }}
            className="accent-purple-500"
          />
          Show deleted
        </label>
      </div>

      {artifacts.isLoading ? (
        <p className="text-sm text-slate-400 p-4">Loading artifacts…</p>
      ) : artifacts.isError ? (
        <p className="text-sm text-rose-300 p-4">{errorText(artifacts.error, 'Artifacts are unavailable')}</p>
      ) : rows.length === 0 ? (
        <div className="glass-card p-8 text-center">
          <HardDrive size={28} className="mx-auto text-slate-600 mb-2" />
          <p className="text-sm text-slate-400">
            {includeDeleted ? 'No deleted artifacts.' : 'No artifacts yet. Generated audio, images and documents will collect here.'}
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {rows.map((artifact) => {
            const Icon = kindIcon(artifact.kind);
            const deleted = Boolean(artifact.deleted_at);
            return (
              <article
                key={artifact.id}
                className={`glass-card p-3 sm:p-4 flex flex-wrap items-center gap-3 ${deleted ? 'opacity-60' : ''}`}
              >
                <div className="h-10 w-10 rounded-xl bg-purple-500/10 border border-purple-500/20 flex items-center justify-center shrink-0">
                  <Icon size={19} className="text-purple-300" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="font-semibold text-white truncate">{title(artifact)}</p>
                    {!artifact.is_attached && !deleted && (
                      <span className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-slate-700 text-slate-300">Unattached</span>
                    )}
                    {deleted && (
                      <span className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-200">Recently deleted</span>
                    )}
                    {user?.is_admin && artifact.owner_user !== user.username && (
                      <span className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-slate-700 text-slate-300">{artifact.owner_user}</span>
                    )}
                  </div>
                  <p className="text-xs text-slate-500 mt-0.5">
                    {artifact.kind.replace('_', ' ')} · {formatBytes(artifact.size) || '0 B'}
                    {' · '}{new Date(artifact.created_at).toLocaleString()}
                    {artifact.generator ? ` · ${artifact.generator}` : ''}
                  </p>
                </div>
                <div className="flex items-center gap-1">
                  {!deleted && (
                    <>
                      <button
                        onClick={() => openPreviewFor(artifact)}
                        aria-label={`Preview ${title(artifact)}`}
                        title="Preview"
                        className="p-2 rounded-lg text-slate-400 hover:text-white hover:bg-white/5"
                      >
                        <Eye size={17} />
                      </button>
                      <button
                        onClick={() => download.mutate(artifact)}
                        aria-label={`Download ${title(artifact)}`}
                        title="Download"
                        className="p-2 rounded-lg text-slate-400 hover:text-white hover:bg-white/5"
                      >
                        <Download size={17} />
                      </button>
                      <button
                        onClick={() => { setRenaming(artifact); setRenameValue(title(artifact)); }}
                        aria-label={`Rename ${title(artifact)}`}
                        title="Rename"
                        className="p-2 rounded-lg text-slate-400 hover:text-white hover:bg-white/5"
                      >
                        <Pencil size={16} />
                      </button>
                      <button
                        onClick={() => remove.mutate(artifact)}
                        aria-label={`Delete ${title(artifact)}`}
                        title="Delete"
                        className="p-2 rounded-lg text-slate-400 hover:text-rose-300 hover:bg-rose-500/10"
                      >
                        <Trash2 size={17} />
                      </button>
                    </>
                  )}
                  {deleted && (
                    <>
                      <button
                        onClick={() => restore.mutate(artifact)}
                        className="px-2.5 py-1.5 rounded-lg text-xs text-emerald-200 bg-emerald-500/10 hover:bg-emerald-500/20 flex items-center gap-1.5"
                      >
                        <RotateCcw size={14} /> Restore
                      </button>
                      <button
                        onClick={() => { setHardDelete(artifact); setConfirmPhrase(''); }}
                        aria-label={`Delete ${title(artifact)} permanently`}
                        title="Delete permanently"
                        className="p-2 rounded-lg text-slate-400 hover:text-rose-300 hover:bg-rose-500/10"
                      >
                        <Trash2 size={17} />
                      </button>
                    </>
                  )}
                </div>
              </article>
            );
          })}
        </div>
      )}

      {total > limit && (
        <div className="flex items-center justify-between gap-2">
          <button
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - limit))}
            className="glass-card px-3 py-2 text-xs disabled:opacity-40"
          >
            Previous
          </button>
          <p className="text-xs text-slate-500">
            {offset + 1}–{Math.min(offset + limit, total)} of {total}
          </p>
          <button
            disabled={offset + limit >= total}
            onClick={() => setOffset(offset + limit)}
            className="glass-card px-3 py-2 text-xs disabled:opacity-40"
          >
            Next
          </button>
        </div>
      )}

      <Modal isOpen={Boolean(preview)} onClose={() => { setPreview(null); setPreviewUrl(''); }} title={preview ? title(preview) : 'Artifact'} size="4xl">
        {!previewUrl ? (
          <p className="text-sm text-slate-400">Preparing preview…</p>
        ) : preview?.kind === 'image' ? (
          <img src={previewUrl} alt={preview.name} className="max-h-[70vh] mx-auto rounded-xl" />
        ) : preview?.kind === 'video' ? (
          <video src={previewUrl} controls className="w-full max-h-[70vh] rounded-xl" />
        ) : preview?.kind === 'tts_audio' || preview?.mime_type?.startsWith('audio/') ? (
          <audio src={previewUrl} controls className="w-full" />
        ) : (
          <div className="space-y-3">
            <p className="text-sm text-slate-400">This kind is not previewable in the browser.</p>
            <a href={previewUrl} download={preview?.name} className="inline-flex items-center gap-2 text-sm text-purple-300 hover:text-purple-200">
              <Download size={15} /> Download {preview ? title(preview) : 'artifact'}
            </a>
          </div>
        )}
      </Modal>

      <Modal isOpen={Boolean(renaming)} onClose={() => setRenaming(null)} title="Rename artifact" size="md">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (renaming && renameValue.trim()) rename.mutate({ artifact: renaming, name: renameValue.trim() });
          }}
          className="space-y-4"
        >
          <input
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
            aria-label="Artifact name"
            className="w-full glass-card px-3 py-2 text-sm text-white bg-transparent"
            autoFocus
          />
          <div className="flex justify-end gap-2">
            <button type="button" onClick={() => setRenaming(null)} className="px-3 py-2 text-sm text-slate-300">Cancel</button>
            <button type="submit" disabled={!renameValue.trim() || rename.isPending} className="px-4 py-2 rounded-xl bg-purple-600 text-white text-sm font-semibold disabled:opacity-40">
              Save name
            </button>
          </div>
        </form>
      </Modal>

      <Modal isOpen={Boolean(hardDelete)} onClose={() => { setHardDelete(null); setConfirmPhrase(''); }} title="Delete permanently" size="md">
        <div className="space-y-4">
          <p className="text-sm text-slate-300">
            This removes the artifact and its bytes immediately. Type <strong>delete {hardDelete ? hardDelete.name : ''}</strong> to confirm.
          </p>
          <input
            value={confirmPhrase}
            onChange={(e) => setConfirmPhrase(e.target.value)}
            aria-label="Confirmation phrase"
            className="w-full glass-card px-3 py-2 text-sm text-white bg-transparent"
            placeholder={hardDelete ? `delete ${hardDelete.name}` : ''}
          />
          <div className="flex justify-end gap-2">
            <button type="button" onClick={() => { setHardDelete(null); setConfirmPhrase(''); }} className="px-3 py-2 text-sm text-slate-300">Cancel</button>
            <button
              type="button"
              disabled={!hardDelete || confirmPhrase !== `delete ${hardDelete.name}` || hardRemove.isPending}
              onClick={() => hardDelete && hardRemove.mutate({ artifact: hardDelete, phrase: confirmPhrase })}
              className="px-4 py-2 rounded-xl bg-rose-600 text-white text-sm font-semibold disabled:opacity-40"
            >
              Delete forever
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
};

export default Artifacts;
