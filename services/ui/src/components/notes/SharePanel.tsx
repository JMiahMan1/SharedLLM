import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, Share2, X } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import { errorText } from '../../lib/errorText';

interface NoteShare {
  id: string;
  with: string;
  name: string;
  can_edit: boolean;
}

interface SharePerson {
  id: string;
  name: string;
}

const STORAGE = 'nextcloud';

/**
 * Share a note with someone on the family's Nextcloud, through Nextcloud's
 * own sharing (the note stays one file; they see the same text). The people
 * offered are the Nextcloud users this account may share with.
 */
export default function SharePanel({ path, asUser }: { path: string; asUser?: 'admin' }) {
  const queryClient = useQueryClient();
  const sharesKey = ['note-shares', asUser ?? 'me', path] as const;
  const [person, setPerson] = useState('');
  const [canEdit, setCanEdit] = useState(true);
  const [busy, setBusy] = useState(false);

  const shares = useQuery({
    queryKey: sharesKey,
    queryFn: async (): Promise<NoteShare[]> => {
      const res = await api.noteShare({ action: 'list', path, storage: STORAGE, as_user: asUser });
      if (res.status !== 'SUCCESS') throw new Error(res.message || 'Could not load who this note is shared with');
      return (res.detail?.shares as NoteShare[] | undefined) ?? [];
    },
    retry: false,
  });

  const people = useQuery({
    queryKey: ['note-sharees', asUser ?? 'me'],
    queryFn: async (): Promise<SharePerson[]> => {
      const res = await api.noteShare({ action: 'people', storage: STORAGE, as_user: asUser });
      if (res.status !== 'SUCCESS') throw new Error(res.message || 'Could not load the people to share with');
      return (res.detail?.people as SharePerson[] | undefined) ?? [];
    },
    retry: false,
    staleTime: 300_000,
  });

  const sharedWith = new Set((shares.data ?? []).map((s) => s.with));
  const candidates = (people.data ?? []).filter((p) => !sharedWith.has(p.id));

  const act = async (run: () => Promise<{ status: string; message?: string }>, done: string) => {
    setBusy(true);
    try {
      const res = await run();
      if (res.status !== 'SUCCESS') throw new Error(res.message || 'Sharing failed');
      toast.success(done);
      await queryClient.invalidateQueries({ queryKey: sharesKey });
    } catch (err) {
      toast.error(errorText(err, 'Sharing failed'));
    } finally {
      setBusy(false);
    }
  };

  const share = () => {
    const chosen = candidates.find((p) => p.id === person);
    if (!chosen) return;
    void act(
      () => api.noteShare({ action: 'add', path, share_with: chosen.id, can_edit: canEdit, storage: STORAGE, as_user: asUser }),
      `Shared with ${chosen.name}`,
    ).then(() => setPerson(''));
  };

  return (
    <section className="space-y-2 rounded-xl border border-white/10 p-3" aria-label="Sharing" data-testid="note-share">
      <h4 className="flex items-center gap-2 text-xs font-semibold text-slate-200">
        <Share2 size={13} /> Shared with
      </h4>
      {shares.isLoading ? (
        <p className="flex items-center gap-2 text-xs text-slate-400" role="status">
          <Loader2 size={13} className="animate-spin" /> Loading…
        </p>
      ) : shares.isError ? (
        <p className="text-xs text-rose-300" role="alert">
          {errorText(shares.error, 'Could not load who this note is shared with')}
        </p>
      ) : (shares.data ?? []).length === 0 ? (
        <p className="text-xs text-slate-400">Only you so far.</p>
      ) : (
        <ul className="space-y-1">
          {(shares.data ?? []).map((s) => (
            <li key={s.id} className="flex items-center justify-between gap-2 text-xs text-slate-300">
              <span>
                {s.name} <span className="text-slate-500">· {s.can_edit ? 'can edit' : 'can view'}</span>
              </span>
              <button
                type="button"
                disabled={busy}
                aria-label={`Stop sharing with ${s.name}`}
                className="flex h-9 w-9 items-center justify-center rounded-lg text-slate-400 hover:bg-white/10 hover:text-rose-300"
                onClick={() =>
                  void act(
                    () => api.noteShare({ action: 'remove', share_id: s.id, storage: STORAGE, as_user: asUser }),
                    `No longer shared with ${s.name}`,
                  )
                }
              >
                <X size={14} />
              </button>
            </li>
          ))}
        </ul>
      )}
      {people.isLoading ? (
        <p className="flex items-center gap-2 text-xs text-slate-400" role="status">
          <Loader2 size={13} className="animate-spin" /> Loading people…
        </p>
      ) : people.isError ? (
        <p className="text-xs text-rose-300" role="alert">
          {errorText(people.error, 'Could not load the people to share with')}
        </p>
      ) : candidates.length === 0 ? (
        <p className="text-xs text-slate-400">
          {(people.data ?? []).length === 0 ? 'No one else on this Nextcloud to share with.' : 'Shared with everyone available.'}
        </p>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <select
            aria-label="Share with"
            value={person}
            onChange={(e) => setPerson(e.target.value)}
            className="glass-input min-h-9 px-2 py-1 text-base sm:text-xs"
          >
            <option value="">Choose someone…</option>
            {candidates.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
          <label className="flex items-center gap-1 text-xs text-slate-300">
            <input type="checkbox" checked={canEdit} onChange={(e) => setCanEdit(e.target.checked)} /> Can edit
          </label>
          <button
            type="button"
            disabled={busy || !person}
            className="glass-button min-h-9 px-3 py-1 text-xs disabled:opacity-60"
            onClick={share}
          >
            {busy ? <Loader2 size={13} className="animate-spin" /> : <Share2 size={13} />} Share
          </button>
        </div>
      )}
      <p className="text-[11px] text-slate-500">
        They get the same file through Nextcloud (Files → Shared with you), so edits on either side change the same
        note. It shows in their Notes here once they move it into their Notes folder.
      </p>
    </section>
  );
}
