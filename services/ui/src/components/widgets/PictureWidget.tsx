import { useRef, useState } from 'react';
import toast from 'react-hot-toast';
import { Image as ImageIcon, Pencil, Upload } from 'lucide-react';
import type { IWidgetProps, PictureConfig } from '../../types/widget';
import { useWidgetStore } from '../../stores/widgetStore';
import { PICTURE_TEXT_MAX, pictureFromFile, pictureSrcError, safePictureSrc } from '../../lib/widgetContent';
import { WidgetCard } from './WidgetCard';

/**
 * One picture on the dashboard (Home Assistant's picture card): a link to an
 * https image, or a photo uploaded from this device (scaled down and kept
 * with the widget's settings). Only a source `safePictureSrc` accepts is
 * ever put in the `<img>`, and it is loaded without a referrer.
 */
const PictureWidget = ({ settingsButton, userSettings }: IWidgetProps) => {
  const config = (userSettings?.config ?? {}) as PictureConfig;
  const src = safePictureSrc(config.src);
  const caption = typeof config.caption === 'string' ? config.caption : '';
  const alt = typeof config.alt === 'string' ? config.alt : '';
  const [editing, setEditing] = useState(false);
  const [draftSrc, setDraftSrc] = useState('');
  const [draftCaption, setDraftCaption] = useState('');
  const [draftAlt, setDraftAlt] = useState('');
  const [saving, setSaving] = useState(false);
  const [broken, setBroken] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const problem = pictureSrcError(draftSrc.trim());

  const startEditing = () => {
    setDraftSrc(typeof config.src === 'string' ? config.src : '');
    setDraftCaption(caption);
    setDraftAlt(alt);
    setEditing(true);
  };

  const onFile = async (file: File | undefined) => {
    if (!file) return;
    try {
      setDraftSrc(await pictureFromFile(file));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'That picture could not be used');
    }
  };

  const save = async () => {
    if (problem) return;
    setSaving(true);
    try {
      await useWidgetStore.getState().updateWidgetConfig('picture', {
        src: draftSrc.trim(), caption: draftCaption.trim(), alt: draftAlt.trim(),
      });
      setBroken(false);
      setEditing(false);
    } catch (err) {
      toast.error(err instanceof Error && err.message ? `The picture was not saved: ${err.message}` : 'The picture was not saved');
    } finally {
      setSaving(false);
    }
  };

  const uploaded = draftSrc.startsWith('data:');
  return (
    <WidgetCard
      title={caption || 'Picture'}
      icon={<ImageIcon size={16} />}
      accentColor="#38bdf8"
      settingsButton={settingsButton}
      actions={!editing && (
        <button type="button" onClick={startEditing} aria-label="Change picture"
          className="min-h-11 min-w-11 flex items-center justify-center rounded-lg text-slate-400 hover:text-white hover:bg-white/5">
          <Pencil size={14} />
        </button>
      )}
    >
      {editing ? (
        <form className="flex flex-col gap-2" data-testid="picture-editor"
          onSubmit={(e) => { e.preventDefault(); void save(); }}>
          <input value={uploaded ? '' : draftSrc} onChange={(e) => setDraftSrc(e.target.value)}
            placeholder={uploaded ? 'Using the uploaded picture' : 'https://... link to a picture'} aria-label="Picture link"
            inputMode="url" className="min-h-11 rounded-xl bg-white/5 border border-white/10 px-3 text-sm" />
          <input ref={fileInput} type="file" accept="image/png,image/jpeg,image/webp,image/gif" className="hidden"
            aria-label="Upload a picture" onChange={(e) => void onFile(e.target.files?.[0])} />
          <button type="button" onClick={() => fileInput.current?.click()}
            className="min-h-11 rounded-xl bg-white/5 border border-white/10 text-sm flex items-center justify-center gap-2">
            <Upload size={14} /> {uploaded ? 'Upload a different picture' : 'Upload from this device'}
          </button>
          <input value={draftCaption} onChange={(e) => setDraftCaption(e.target.value)} maxLength={PICTURE_TEXT_MAX}
            placeholder="Caption (optional)" aria-label="Caption"
            className="min-h-11 rounded-xl bg-white/5 border border-white/10 px-3 text-sm" />
          <input value={draftAlt} onChange={(e) => setDraftAlt(e.target.value)} maxLength={PICTURE_TEXT_MAX}
            placeholder="What it shows, for screen readers" aria-label="Description for screen readers"
            className="min-h-11 rounded-xl bg-white/5 border border-white/10 px-3 text-sm" />
          {problem && <p role="alert" className="text-xs text-amber-300">{problem}</p>}
          <div className="flex gap-2 justify-end">
            <button type="button" onClick={() => setEditing(false)} className="min-h-11 px-4 rounded-xl bg-white/5 text-sm">Cancel</button>
            <button type="submit" disabled={saving || Boolean(problem)}
              className="min-h-11 px-4 rounded-xl bg-sky-500/20 text-sky-100 text-sm font-semibold disabled:opacity-50">
              {saving ? 'Saving...' : 'Save'}
            </button>
          </div>
        </form>
      ) : src && !broken ? (
        <figure className="h-full flex flex-col gap-1 m-0">
          <img src={src} alt={alt || caption || 'Picture'} referrerPolicy="no-referrer" loading="lazy"
            onError={() => setBroken(true)} data-testid="picture-image"
            className="flex-1 min-h-0 w-full object-cover rounded-xl" />
        </figure>
      ) : (
        <div className="flex flex-col items-center justify-center text-center gap-2 h-full p-2">
          <p className="text-sm text-slate-400">
            {src ? 'The picture could not be loaded.' : 'Show a photo on your dashboard.'}
          </p>
          <button type="button" onClick={startEditing}
            className="min-h-11 px-4 rounded-xl bg-sky-500/15 text-sky-100 text-sm font-semibold">
            {src ? 'Change picture' : 'Choose a picture'}
          </button>
        </div>
      )}
    </WidgetCard>
  );
};

export default PictureWidget;
