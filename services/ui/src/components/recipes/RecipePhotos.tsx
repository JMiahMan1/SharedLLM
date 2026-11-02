import { useRef, useState } from 'react';
import toast from 'react-hot-toast';
import { Camera, ImagePlus, Link2, Loader2, Star, Trash2 } from 'lucide-react';
import { api } from '../../services/api';
import { recipePhotoUrl, shrinkPhoto, type RecipePicture } from '../../lib/recipePhoto';
import { useMediaToken } from '../../lib/mediaToken';
import { confirmDialog } from '../ui/confirmDialog';

/**
 * The recipe's photos: anyone in the family can add one of what they cooked
 * (taken now, chosen from the phone, or from a web address); whoever may
 * change the recipe picks the cover and removes photos.
 */
export default function RecipePhotos({
  path,
  pictures,
  canChange,
  ownerLabel,
  onChanged,
}: {
  path: string;
  pictures: RecipePicture[];
  canChange: boolean;
  ownerLabel: string;
  onChanged: () => void;
}) {
  useMediaToken();
  const cameraRef = useRef<HTMLInputElement>(null);
  const pickRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [urlOpen, setUrlOpen] = useState(false);
  const [url, setUrl] = useState('');

  const run = async (key: string, call: () => Promise<{ status: string; message?: string }>, done: string) => {
    setBusy(key);
    try {
      const res = await call();
      if (res.status === 'SUCCESS') {
        toast.success(done);
        onChanged();
        return true;
      }
      toast.error(res.message || 'That did not work');
    } catch {
      toast.error('That did not work. Try again.');
    } finally {
      setBusy(null);
    }
    return false;
  };

  const addFile = async (file: File | undefined) => {
    if (!file) return;
    if (!file.type.startsWith('image/') && !/\.(jpe?g|png|webp|heic|heif|gif)$/i.test(file.name)) {
      toast.error('That is not a picture');
      return;
    }
    await run('add', async () => api.cookbookPhoto({ path, action: 'add', ...(await shrinkPhoto(file)) }), 'Photo added');
  };

  const addUrl = async () => {
    const address = url.trim();
    if (!/^https?:\/\//i.test(address)) {
      toast.error('Paste the photo’s web address (https://…)');
      return;
    }
    if (await run('add', () => api.cookbookPhoto({ path, action: 'add', url: address }), 'Photo added')) {
      setUrl('');
      setUrlOpen(false);
    }
  };

  const remove = async (picture: RecipePicture) => {
    if (!(await confirmDialog({ title: 'Remove this photo?', body: 'The photo is deleted from the recipe for everyone.', confirmLabel: 'Remove', danger: true }))) return;
    await run(`remove:${picture.link}`, () => api.cookbookPhoto({ path, action: 'remove', link: picture.link }), 'Photo removed');
  };

  return (
    <section className="glass-panel rounded-3xl border border-white/5 p-5" aria-labelledby="photos-heading" data-testid="recipe-photos">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id="photos-heading" className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wider text-slate-300">
          <Camera size={14} aria-hidden /> Photos
        </h3>
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={() => cameraRef.current?.click()} disabled={busy !== null} className="glass-button min-h-11 px-3 text-xs disabled:opacity-60" data-testid="recipe-photo-take">
            {busy === 'add' ? <Loader2 size={14} className="animate-spin" /> : <Camera size={14} />} Take a photo
          </button>
          <button type="button" onClick={() => pickRef.current?.click()} disabled={busy !== null} className="glass-button min-h-11 px-3 text-xs disabled:opacity-60">
            <ImagePlus size={14} /> Choose
          </button>
          <button type="button" onClick={() => setUrlOpen((open) => !open)} aria-expanded={urlOpen} disabled={busy !== null} className="glass-button min-h-11 px-3 text-xs disabled:opacity-60">
            <Link2 size={14} /> From the web
          </button>
        </div>
        <input
          ref={cameraRef}
          type="file"
          accept="image/*"
          capture="environment"
          className="hidden"
          aria-label="Take a photo"
          onChange={(event) => {
            void addFile(event.target.files?.[0]);
            event.target.value = '';
          }}
        />
        <input
          ref={pickRef}
          type="file"
          accept="image/*"
          className="hidden"
          aria-label="Choose a photo"
          data-testid="recipe-photo-input"
          onChange={(event) => {
            void addFile(event.target.files?.[0]);
            event.target.value = '';
          }}
        />
      </div>

      {urlOpen && (
        <div className="mt-3 flex gap-2">
          <input
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') void addUrl();
            }}
            placeholder="https://… (a photo’s address)"
            aria-label="Photo web address"
            type="url"
            className="glass-input min-h-11 min-w-0 flex-1 px-3 text-base sm:text-sm"
          />
          <button type="button" onClick={() => void addUrl()} disabled={busy !== null} className="glass-button min-h-11 px-4 text-xs disabled:opacity-60">
            Add
          </button>
        </div>
      )}

      {pictures.length === 0 ? (
        <p className="mt-3 text-xs text-slate-500">No photos yet. Cooked it? Take a picture and it becomes the cover.</p>
      ) : (
        <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
          {pictures.map((picture, index) => {
            const src = recipePhotoUrl({ path, file: picture.link });
            const cover = index === 0;
            return (
              <figure key={picture.link} className="overflow-hidden rounded-xl border border-white/10 bg-black/20" data-testid="recipe-photo">
                <a href={src ?? undefined} target="_blank" rel="noreferrer" className="relative block">
                  {src ? <img src={src} alt={picture.label} loading="lazy" className="aspect-square w-full object-cover hover:opacity-90" /> : <span className="block aspect-square w-full" />}
                  {cover && (
                    <span className="absolute left-1.5 top-1.5 rounded-full bg-black/60 px-2 py-0.5 text-[10px] font-semibold text-amber-200 backdrop-blur">★ Cover</span>
                  )}
                </a>
                <figcaption className="flex items-center justify-between gap-1 px-2 py-1">
                  <span className="truncate text-[11px] text-slate-400">{picture.label}</span>
                  {canChange && (
                    <span className="flex shrink-0">
                      {!cover && (
                        <button
                          type="button"
                          onClick={() => void run(`cover:${picture.link}`, () => api.cookbookPhoto({ path, action: 'cover', link: picture.link }), 'Cover changed')}
                          disabled={busy !== null}
                          aria-label={`Make ${picture.label} the cover`}
                          title="Make it the cover"
                          className="flex h-9 w-9 items-center justify-center rounded-lg text-slate-400 hover:bg-white/10 hover:text-amber-200 pointer-coarse:h-11 pointer-coarse:w-11"
                        >
                          {busy === `cover:${picture.link}` ? <Loader2 size={13} className="animate-spin" /> : <Star size={13} />}
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() => void remove(picture)}
                        disabled={busy !== null}
                        aria-label={`Remove ${picture.label}`}
                        title="Remove"
                        className="flex h-9 w-9 items-center justify-center rounded-lg text-slate-400 hover:bg-rose-500/10 hover:text-rose-300 pointer-coarse:h-11 pointer-coarse:w-11"
                      >
                        {busy === `remove:${picture.link}` ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />}
                      </button>
                    </span>
                  )}
                </figcaption>
              </figure>
            );
          })}
        </div>
      )}
      {!canChange && pictures.length > 1 ? (
        <p className="mt-2 text-[11px] text-slate-500">Only {ownerLabel} can choose the cover or remove photos.</p>
      ) : null}
    </section>
  );
}
