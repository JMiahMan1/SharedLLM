import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { History, Loader2 } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import { errorText } from '../../lib/errorText';
import { noteBody } from '../../lib/notesMeta';
import { confirmDialog } from '../ui/confirmDialog';

export interface NoteVersion {
  /** Nextcloud's version id (the time it was made, in seconds). */
  id: string;
  /** ISO time, or '' when Nextcloud did not say. */
  modified: string;
  size: number;
  label: string;
  author: string;
}

const STORAGE = 'nextcloud';

function when(version: NoteVersion): string {
  const date = version.modified ? new Date(version.modified) : new Date(Number(version.id) * 1000);
  return Number.isNaN(date.getTime()) ? version.id : date.toLocaleString();
}

/**
 * A note's earlier versions, straight from Nextcloud's own version history
 * (the files_versions app keeps one each time the file changes). Pick one to
 * read it; Restore puts it back over the note, and Nextcloud first files the
 * current text as a version too, so a restore can be undone from this list.
 */
export default function NoteHistory({
  path,
  asUser,
  dirty,
  onRestored,
}: {
  path: string;
  asUser?: 'admin';
  /** The editor holds unsaved changes, which a restore discards. */
  dirty: boolean;
  onRestored: () => void;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const [restoring, setRestoring] = useState(false);

  const list = useQuery({
    queryKey: ['note-versions', asUser ?? 'me', path],
    queryFn: async (): Promise<NoteVersion[]> => {
      const res = await api.noteVersions({ path, action: 'list', storage: STORAGE, as_user: asUser });
      if (res.status !== 'SUCCESS') throw new Error(res.message || 'Could not load the versions');
      return (res.detail?.versions as NoteVersion[] | undefined) ?? [];
    },
    retry: false,
    staleTime: 0,
  });

  const preview = useQuery({
    queryKey: ['note-version', asUser ?? 'me', path, selected],
    enabled: Boolean(selected),
    queryFn: async (): Promise<string> => {
      const res = await api.noteVersions({ path, action: 'read', version: selected ?? '', storage: STORAGE, as_user: asUser });
      if (res.status !== 'SUCCESS') throw new Error(res.message || 'Could not read that version');
      return noteBody(res.message ?? '');
    },
    retry: false,
  });

  const restore = async (version: NoteVersion) => {
    const warning = dirty ? ' Your unsaved changes in this note will be discarded.' : '';
    if (!(await confirmDialog({ title: 'Restore this version?', body: `Restore the version from ${when(version)}?${warning}`, confirmLabel: 'Restore' }))) return;
    setRestoring(true);
    try {
      const res = await api.noteVersions({ path, action: 'restore', version: version.id, storage: STORAGE, as_user: asUser });
      if (res.status !== 'SUCCESS') throw new Error(res.message || 'Could not restore that version');
      toast.success('Earlier version restored');
      onRestored();
    } catch (err) {
      toast.error(errorText(err, 'Could not restore that version'));
    } finally {
      setRestoring(false);
    }
  };

  const versions = list.data ?? [];
  const chosen = versions.find((v) => v.id === selected);

  return (
    <section className="space-y-2 rounded-xl border border-white/10 p-3" aria-label="Version history" data-testid="note-history">
      <h4 className="flex items-center gap-2 text-xs font-semibold text-slate-200">
        <History size={13} /> Version history
      </h4>
      {list.isLoading ? (
        <p className="flex items-center gap-2 text-xs text-slate-400" role="status">
          <Loader2 size={13} className="animate-spin" /> Loading versions…
        </p>
      ) : list.isError ? (
        <p className="text-xs text-rose-300" role="alert">
          {errorText(list.error, 'Could not load the versions')}
        </p>
      ) : versions.length === 0 ? (
        <p className="text-xs text-slate-400">
          No earlier versions yet. Nextcloud keeps one each time the note changes (when its Versions app is on).
        </p>
      ) : (
        <ul className="max-h-40 space-y-1 overflow-y-auto">
          {versions.map((version) => (
            <li key={version.id}>
              <button
                type="button"
                aria-pressed={selected === version.id}
                onClick={() => setSelected(selected === version.id ? null : version.id)}
                className={`flex min-h-9 w-full items-center justify-between gap-2 rounded-lg px-2 text-left text-xs ${
                  selected === version.id ? 'bg-white/10 text-slate-100' : 'text-slate-300 hover:bg-white/5'
                }`}
              >
                <span>
                  {when(version)}
                  {version.label && <span className="ml-2 text-purple-200">{version.label}</span>}
                </span>
                <span className="text-slate-500">{Math.max(1, Math.round(version.size / 1024))} KB</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {chosen && (
        <div className="space-y-2 border-t border-white/5 pt-2">
          {preview.isLoading ? (
            <p className="flex items-center gap-2 text-xs text-slate-400" role="status">
              <Loader2 size={13} className="animate-spin" /> Reading that version…
            </p>
          ) : preview.isError ? (
            <p className="text-xs text-rose-300" role="alert">
              {errorText(preview.error, 'Could not read that version')}
            </p>
          ) : (
            <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-lg bg-black/30 p-2 font-sans text-xs text-slate-300" data-testid="note-version-text">
              {preview.data || '(empty)'}
            </pre>
          )}
          <button
            type="button"
            className="glass-button min-h-9 px-3 py-1 text-xs"
            disabled={restoring || !preview.isSuccess}
            onClick={() => void restore(chosen)}
          >
            {restoring ? <Loader2 size={13} className="animate-spin" /> : <History size={13} />} Restore this version
          </button>
        </div>
      )}
    </section>
  );
}
