import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import toast from 'react-hot-toast';
import { NotebookPen, Pencil } from 'lucide-react';
import type { IWidgetProps, NoteConfig } from '../../types/widget';
import { useWidgetStore } from '../../stores/widgetStore';
import { NOTE_MAX_CHARS, NOTE_TITLE_MAX, noteError, safeNoteHref, safeNoteImage } from '../../lib/widgetContent';
import { WidgetCard } from './WidgetCard';

/**
 * Markdown as the note widget draws it: GitHub-flavoured, raw HTML dropped
 * (never parsed), links only to web, mail and phone addresses, images only
 * from https. Exported so the rendering rules are tested on their own.
 */
export function NoteMarkdown({ markdown }: { markdown: string }) {
  return (
    <div className="prose prose-invert prose-sm max-w-none break-words prose-a:text-sky-300 prose-img:rounded-lg"
      data-testid="note-markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        urlTransform={(url, key) => (key === 'src' ? safeNoteImage(url) : safeNoteHref(url)) ?? ''}
        components={{
          a: ({ href, children }) => (href
            ? <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>
            : <span>{children}</span>),
          img: ({ src, alt }) => (typeof src === 'string' && src
            ? <img src={src} alt={alt ?? ''} loading="lazy" referrerPolicy="no-referrer" className="max-w-full h-auto" />
            : <span>{alt ?? ''}</span>),
        }}
      >
        {markdown}
      </ReactMarkdown>
    </div>
  );
}

const NoteWidget = ({ settingsButton, userSettings }: IWidgetProps) => {
  const config = (userSettings?.config ?? {}) as NoteConfig;
  const title = typeof config.title === 'string' ? config.title : '';
  const markdown = typeof config.markdown === 'string' ? config.markdown : '';
  const [editing, setEditing] = useState(false);
  const [draftTitle, setDraftTitle] = useState(title);
  const [draft, setDraft] = useState(markdown);
  const [saving, setSaving] = useState(false);
  const problem = noteError(draftTitle, draft);

  const startEditing = () => {
    setDraftTitle(title);
    setDraft(markdown);
    setEditing(true);
  };

  const save = async () => {
    if (problem) return;
    setSaving(true);
    try {
      await useWidgetStore.getState().updateWidgetConfig('note', { title: draftTitle.trim(), markdown: draft });
      setEditing(false);
    } catch (err) {
      toast.error(err instanceof Error && err.message ? `The note was not saved: ${err.message}` : 'The note was not saved');
    } finally {
      setSaving(false);
    }
  };

  return (
    <WidgetCard
      title={title || 'Note'}
      icon={<NotebookPen size={16} />}
      accentColor="#fbbf24"
      settingsButton={settingsButton}
      isExpandable={!editing && Boolean(markdown)}
      actions={!editing && (
        <button type="button" onClick={startEditing} aria-label="Edit note"
          className="min-h-11 min-w-11 flex items-center justify-center rounded-lg text-slate-400 hover:text-white hover:bg-white/5">
          <Pencil size={14} />
        </button>
      )}
    >
      {editing ? (
        <form className="flex flex-col gap-2 h-full" data-testid="note-editor"
          onSubmit={(e) => { e.preventDefault(); void save(); }}>
          <input value={draftTitle} onChange={(e) => setDraftTitle(e.target.value)} maxLength={NOTE_TITLE_MAX}
            placeholder="Title (optional)" aria-label="Note title"
            className="min-h-11 rounded-xl bg-white/5 border border-white/10 px-3 text-sm" />
          <textarea value={draft} onChange={(e) => setDraft(e.target.value)} maxLength={NOTE_MAX_CHARS}
            aria-label="Note text (markdown)" placeholder={'Write in markdown: **bold**, - lists, [links](https://...)'}
            className="flex-1 min-h-28 rounded-xl bg-white/5 border border-white/10 p-3 text-sm font-mono" />
          {problem && <p role="alert" className="text-xs text-amber-300">{problem}</p>}
          <div className="flex gap-2 justify-end">
            <button type="button" onClick={() => setEditing(false)} className="min-h-11 px-4 rounded-xl bg-white/5 text-sm">Cancel</button>
            <button type="submit" disabled={saving || Boolean(problem)}
              className="min-h-11 px-4 rounded-xl bg-amber-500/20 text-amber-100 text-sm font-semibold disabled:opacity-50">
              {saving ? 'Saving...' : 'Save'}
            </button>
          </div>
        </form>
      ) : markdown ? (
        <NoteMarkdown markdown={markdown} />
      ) : (
        <div className="flex flex-col items-center justify-center text-center gap-2 h-full p-2">
          <p className="text-sm text-slate-400">A note for your dashboard, in markdown.</p>
          <button type="button" onClick={startEditing}
            className="min-h-11 px-4 rounded-xl bg-amber-500/15 text-amber-100 text-sm font-semibold">Write a note</button>
        </div>
      )}
    </WidgetCard>
  );
};

export default NoteWidget;
