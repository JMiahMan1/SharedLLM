import { useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Camera, Check, Loader2, Trash2, X } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../services/api';
import type { UserProfile } from '../types/api';
import { AVATAR_PRESETS, squarePhoto } from '../lib/avatarPresets';
import UserAvatar from './UserAvatar';
import { errorText } from '../lib/errorText';

/**
 * Choose a profile picture: a Bible-story character, your own photo, or your
 * initials. A photo is squared and shrunk on the device before it is sent,
 * and stays saved until you remove it.
 */
export default function AvatarPicker({ user, onClose }: { user: UserProfile; onClose: () => void }) {
  const queryClient = useQueryClient();
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [preparing, setPreparing] = useState(false);
  const saved = (profile: UserProfile) => {
    queryClient.setQueryData(['me'], profile);
    // Chat and Wander read the directory, which holds everyone's avatar
    // version. Without this a new photo would not reach them for five minutes.
    void queryClient.invalidateQueries({ queryKey: ['family-directory'] });
    void queryClient.invalidateQueries({ queryKey: ['me'] });
  };
  const choose = useMutation({ mutationFn: (id: string) => api.chooseAvatar(id), onSuccess: saved, onError: (e) => toast.error(errorText(e, 'That did not work')) });
  const upload = useMutation({ mutationFn: (dataUrl: string) => api.uploadAvatar(dataUrl), onSuccess: (p) => { saved(p); toast.success('Photo saved'); }, onError: (e) => toast.error(errorText(e, 'That did not work')) });
  const remove = useMutation({ mutationFn: () => api.removeAvatar(), onSuccess: saved, onError: (e) => toast.error(errorText(e, 'That did not work')) });
  const busy = choose.isPending || upload.isPending || remove.isPending || preparing;
  const current = user.avatar ?? null;

  const onFile = async (file: File | undefined) => {
    if (!file) return;
    setPreparing(true);
    try {
      upload.mutate(await squarePhoto(file));
    } catch (e) {
      toast.error(errorText(e, 'That did not work'));
    } finally {
      setPreparing(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center sm:justify-center" role="dialog" aria-modal="true" aria-label="Choose a profile picture">
      <button type="button" aria-label="Close" onClick={onClose} className="absolute inset-0 bg-black/60 backdrop-blur-sm" />
      <div className="relative w-full sm:max-w-2xl max-h-[88vh] overflow-y-auto rounded-t-2xl sm:rounded-2xl border border-white/10 bg-slate-950 p-5 pb-[max(1.25rem,env(safe-area-inset-bottom))]">
        <div className="flex items-center gap-4">
          <UserAvatar username={user.username} avatar={current} name={user.full_name || user.username} size={72} />
          <div className="min-w-0 flex-1">
            <h2 className="text-lg font-bold text-white">Profile picture</h2>
            <p className="text-xs text-slate-400">Pick a Bible character, or use your own photo.</p>
          </div>
          <button type="button" onClick={onClose} aria-label="Close" className="min-h-11 min-w-11 flex items-center justify-center rounded-xl text-slate-400">
            <X size={18} />
          </button>
        </div>

        <div className="mt-4 flex flex-wrap gap-2">
          <input ref={fileRef} type="file" accept="image/*" className="hidden" onChange={(e) => void onFile(e.target.files?.[0])} data-testid="avatar-file" />
          <button
            type="button"
            disabled={busy}
            onClick={() => fileRef.current?.click()}
            className="min-h-11 rounded-xl bg-purple-600 px-4 text-sm font-semibold text-white flex items-center gap-2 disabled:opacity-60"
          >
            {upload.isPending || preparing ? <Loader2 size={15} className="animate-spin" /> : <Camera size={15} />}
            {current?.startsWith('upload:') ? 'Change photo' : 'Upload a photo'}
          </button>
          {current && (
            <button
              type="button"
              disabled={busy}
              onClick={() => remove.mutate()}
              className="min-h-11 rounded-xl border border-white/10 px-4 text-sm text-slate-300 flex items-center gap-2 disabled:opacity-60"
            >
              <Trash2 size={14} /> Use my initials
            </button>
          )}
        </div>

        <div className="mt-5 grid grid-cols-3 sm:grid-cols-5 gap-3" data-testid="avatar-presets">
          {AVATAR_PRESETS.map((preset) => {
            const selected = current === `preset:${preset.id}`;
            return (
              <button
                key={preset.id}
                type="button"
                disabled={busy}
                onClick={() => choose.mutate(preset.id)}
                aria-pressed={selected}
                aria-label={preset.name}
                data-testid={`avatar-preset-${preset.id}`}
                className={`relative flex flex-col items-center gap-1.5 rounded-2xl p-2 text-center transition ${
                  selected ? 'bg-purple-500/20 ring-2 ring-purple-400' : 'hover:bg-white/5'
                }`}
              >
                <UserAvatar username={user.username} avatar={`preset:${preset.id}`} size={64} />
                <span className="text-[11px] leading-tight text-slate-300">{preset.name}</span>
                {selected && <Check size={14} className="absolute right-2 top-2 text-purple-200" />}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
