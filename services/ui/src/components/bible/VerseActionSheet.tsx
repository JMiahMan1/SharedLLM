import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Bookmark,
  BookmarkCheck,
  BookMarked,
  Brain,
  ExternalLink,
  Highlighter,
  ImageIcon,
  Languages,
  Link2,
  MessageCircle,
  NotebookPen,
  ScrollText,
  Share2,
  Sparkles,
  X,
} from 'lucide-react';
import BibleStudyNotes from './BibleStudyNotes';
import JarvisStudyThread from './JarvisStudyThread';
import InterlinearPanel from './InterlinearPanel';
import WordStudyPanel from './WordStudyPanel';
import WordForWord, { WordForWordKey } from './WordForWord';
import ShareToChatSheet from './ShareToChatSheet';
import { api } from '../../services/api';
import { useHaptics } from '../../hooks/useHaptics';
import { renderVerseImage } from '../../lib/verseImage';
import { marksOnVerse } from './verseMarks';
import type { BibleMark, BibleVerse } from '../../types/api';

interface VerseActionSheetProps {
  verse: BibleVerse;
  /** The reader's marks; the sheet only acts on the ones covering this verse. */
  marks: BibleMark[];
  /** Translation the verse came from; the study panel reports what it carries. */
  version: string;
  /** The reader's page tone, used for a shared verse image. */
  page?: string;
  /** Which study Bible explains it; more than one can be installed per translation. */
  edition?: string;
  crossVersion?: boolean;
  onEditionChange?: (code: string) => void;
  onCrossVersionChange?: (on: boolean) => void;
  onClose: () => void;
  onToggleMark: (kind: 'highlight' | 'bookmark', color: string) => void;
  /** Called after a note and its pointer are both stored, to refresh the marks. */
  onNoteSaved?: () => void;
  onShared?: (target: 'clipboard' | 'system' | 'chat') => void;
  /** Open a passage in the reader (from a reference Jarvis names). */
  onOpenRef?: (ref: string) => void;
}

const COLORS: Array<{ id: string; label: string; className: string }> = [
  { id: 'yellow', label: 'Yellow', className: 'bg-amber-400' },
  { id: 'green', label: 'Green', className: 'bg-emerald-400' },
  { id: 'blue', label: 'Blue', className: 'bg-sky-400' },
  { id: 'pink', label: 'Pink', className: 'bg-pink-400' },
  { id: 'purple', label: 'Purple', className: 'bg-purple-400' },
];

// Actions are tiles in a grid, like the Bible App's row of verse actions: an
// icon over a short label, every one a thumb-sized target.
const ACTION_CLASS =
  'flex min-h-[4.5rem] w-full flex-col items-center justify-center gap-1.5 rounded-2xl px-1.5 py-2 text-center text-[11px] leading-tight text-slate-200 hover:bg-white/[0.07] transition-colors [&>span]:flex-none [&>span]:line-clamp-2';

/**
 * What you can do to a verse, as a bottom sheet on a phone and a centred dialog
 * on a desktop.
 *
 * Note bodies go to Notes (Nextcloud) rather than into a second store: the bible
 * service only ever stores an index row pointing at the note, so there is one
 * copy of what was written and it is already synced and searchable.
 *
 * Study notes are the one thing here that is not stored on the verse: they live
 * in their own table and are fetched on demand, so the reader can stay a plain
 * reader.
 */
export default function VerseActionSheet({
  verse,
  marks,
  version,
  page,
  edition,
  crossVersion = false,
  onEditionChange,
  onCrossVersionChange,
  onClose,
  onToggleMark,
  onNoteSaved,
  onShared,
  onOpenRef,
}: VerseActionSheetProps) {
  const [color, setColor] = useState('yellow');
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [studyOpen, setStudyOpen] = useState(false);
  const [askOpen, setAskOpen] = useState(false);
  const [noteBody, setNoteBody] = useState<string | null>(null);
  const [interlinearOpen, setInterlinearOpen] = useState(false);
  /** The word study, open on the verse (empty string) or on one Strong's number. */
  const [wordStudy, setWordStudy] = useState<string | null>(null);
  const [shareChatOpen, setShareChatOpen] = useState(false);
  const { trigger } = useHaptics();

  // Fetched as the sheet opens so the links are real anchors the reader taps:
  // a window opened after an await is a popup without a gesture, which phone
  // browsers and the app's web view block.
  const blbVerse = useQuery({
    queryKey: ['bible-blb-link', verse.reference, version, ''],
    queryFn: () => api.getBlbLink(verse.reference, undefined, version),
    staleTime: Infinity,
    retry: false,
  });
  const blbInterlinear = useQuery({
    queryKey: ['bible-blb-link', verse.reference, version, 'interlinear'],
    queryFn: () => api.getBlbLink(verse.reference, 'interlinear', version),
    staleTime: Infinity,
    retry: false,
  });
  const interlinear = useQuery({
    queryKey: ['bible-interlinear', verse.reference],
    queryFn: () => api.getBibleInterlinear(verse.reference),
    staleTime: 60 * 60 * 1000,
    retry: false,
  });
  // A word study is offered once a Strong's concordance is installed from the library.
  const lexicon = useQuery({
    queryKey: ['bible-lexicon-status'],
    queryFn: () => api.getBibleLexiconStatus(),
    staleTime: 10 * 60 * 1000,
    retry: false,
  });
  // The verse word for word with its Strong's numbers, as Blue Letter Bible opens a verse.
  const wordForWord = useQuery({
    queryKey: ['bible-lexicon-verse', verse.reference],
    queryFn: () => api.getBibleLexiconVerse(verse.reference),
    enabled: Boolean(lexicon.data?.installed),
    staleTime: 60 * 60 * 1000,
    retry: false,
  });
  const tokens = wordForWord.data?.tokens ?? [];
  const linkError = (q: typeof blbVerse) =>
    q.error ? `Blue Letter Bible link unavailable: ${q.error instanceof Error ? q.error.message : 'unknown error'}` : 'Loading link…';
  const blbTapped = () => void api.recordBibleEvent('blb_link_tap', verse.reference).catch(() => undefined);

  const own = marksOnVerse(marks, verse);
  const highlight = own.find((m) => m.kind === 'highlight');
  const bookmark = own.find((m) => m.kind === 'bookmark');
  const noteMark = own.find((m) => m.kind === 'note' && Boolean(m.note_path));

  const run = async (label: string, work: () => Promise<string | void>) => {
    setBusy(true);
    try {
      const message = await work();
      setStatus(message ?? label);
    } catch (err) {
      setStatus(err instanceof Error ? err.message : `${label} failed`);
    } finally {
      setBusy(false);
    }
  };

  const saveNote = () =>
    run('Saved to Notes', async () => {
      const saved = await api.createNote({
        title: verse.reference,
        // A folder inside Notes, where the Notes page lists it; a top-level
        // "Bible" folder was saved but never shown.
        category: 'Notes/Bible',
        content: `${verse.reference}\n\n${verse.text}`,
      });
      await api.recordBibleEvent('note_created', verse.reference);
      const path = typeof saved.detail?.path === 'string' ? saved.detail.path : '';
      if (!path) {
        // The note was written but we were not told where, so the verse cannot
        // be marked as having one. Say that rather than claim a pointer we do
        // not have.
        return 'Saved to Notes, but the app was not told where. Reopen Notes to find it.';
      }
      try {
        await api.putBibleMark({
          ref: verse.reference,
          kind: 'note',
          version_code: version,
          note_path: path,
          note_preview: `${verse.reference}: ${verse.text.slice(0, 120)}`,
        });
      } catch {
        // The note itself is safe; only the pointer failed. Saying so is better
        // than marking the verse and letting the reader find an empty note, or
        // than hiding it and letting them write the same note twice.
        onNoteSaved?.();
        return 'Saved to Notes, but this verse could not be marked as having one.';
      }
      onNoteSaved?.();
      return 'Saved to Notes';
    });

  const openNote = () =>
    run('Note opened', async () => {
      const stored = noteMark?.note_path;
      if (!stored) return 'No note for this verse yet.';
      const read = await api.readNote(verse.reference, 'nextcloud', stored);
      if (read.status !== 'SUCCESS') return read.message || 'The note could not be read.';
      setNoteBody(read.message);
      return 'Note opened';
    });

  const copy = () =>
    run('Copied', async () => {
      await navigator.clipboard.writeText(`${verse.reference}\n${verse.text}`);
      onShared?.('clipboard');
      return 'Copied to clipboard';
    });

  const share = () =>
    run('Shared', async () => {
      const payload = { title: verse.reference, text: `${verse.reference}\n${verse.text}` };
      if (typeof navigator.share !== 'function') {
        await navigator.clipboard.writeText(payload.text);
        onShared?.('clipboard');
        return 'Sharing is not available here, so it was copied instead';
      }
      await navigator.share(payload);
      onShared?.('system');
      return 'Shared';
    });

  const shareImage = () =>
    run('Shared', async () => {
      const blob = await renderVerseImage(verse.text, verse.reference, version, page);
      const file = new File([blob], `${verse.reference.replace(/[^A-Za-z0-9]+/g, '-')}.png`, { type: 'image/png' });
      if (typeof navigator.canShare === 'function' && navigator.canShare({ files: [file] })) {
        await navigator.share({ files: [file], title: verse.reference });
        onShared?.('system');
        return 'Shared';
      }
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = file.name;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 10_000);
      onShared?.('clipboard');
      return 'Image saved to your downloads';
    });

  const memorise = () =>
    run('Added to your memory verses', async () => {
      const body = await api.addBibleMemory({ ref: verse.reference });
      void trigger('success');
      return body.added?.length
        ? `Added to your memory verses. Practise it from Bible › Memorize.`
        : 'Already on your memory list.';
    });

  const openStudy = () =>
    run('', async () => {
      setStudyOpen(true);
      await api.recordBibleEvent('verse_tapped', verse.reference);
      return undefined;
    });

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center" role="dialog" aria-modal="true" aria-label={`Actions for ${verse.reference}`}>
      {/* A light scrim, as on Bible.com: the chosen verse stays readable above the panel. */}
      <button
        type="button"
        aria-label="Close verse actions"
        onClick={onClose}
        className="absolute inset-0 bg-black/25"
        data-testid="bible-sheet-scrim"
      />
      <div
        className="relative w-full sm:mb-6 sm:max-w-xl bg-slate-950/97 backdrop-blur-xl border border-white/10 border-b-0 sm:border-b rounded-t-3xl sm:rounded-3xl shadow-2xl p-4 pb-[max(1rem,env(safe-area-inset-bottom))] max-h-[min(80dvh,40rem)] overflow-y-auto overscroll-contain"
        data-testid="bible-verse-sheet"
      >
        <div className="mx-auto mb-3 h-1 w-10 rounded-full bg-white/20 sm:hidden" aria-hidden />
        <div className="flex items-start justify-between gap-3 mb-3">
          <div className="min-w-0">
            <p className="text-[10px] uppercase tracking-wider text-slate-500">Currently selected</p>
            <p className="text-sm font-semibold text-slate-100" data-testid="bible-sheet-ref">
              {verse.reference}
              {version ? <span className="ml-1.5 text-xs font-normal text-slate-400">{version.toUpperCase()}</span> : null}
            </p>
            {tokens.length === 0 && <p className="text-xs text-slate-400 mt-1 line-clamp-2">{verse.text}</p>}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close verse actions"
            className="min-h-11 min-w-11 shrink-0 flex items-center justify-center rounded-xl text-slate-400 hover:text-white"
          >
            <X size={16} />
          </button>
        </div>

        {tokens.length > 0 && (
          <div className="mb-3 rounded-2xl bg-white/[0.03] p-2">
            <WordForWord tokens={tokens} onWord={(strongs) => setWordStudy(strongs)} />
          </div>
        )}

        {/* One tap on a colour highlights in it (or recolours); the current
            colour again, or the first tile, takes the highlight off. */}
        <div className="mb-2 flex items-center gap-1 overflow-x-auto rounded-2xl bg-white/[0.04] px-2" data-testid="bible-mark-colors">
          <span className="mr-1 flex shrink-0 items-center gap-1.5 text-xs text-slate-300">
            <Highlighter size={14} className="text-amber-300" aria-hidden /> Highlight
          </span>
          {COLORS.map((c) => {
            const on = highlight ? highlight.color === c.id : color === c.id;
            return (
              <button
                key={c.id}
                type="button"
                disabled={busy}
                onClick={() => {
                  setColor(c.id);
                  onToggleMark('highlight', c.id);
                }}
                aria-label={highlight?.color === c.id ? `Remove the ${c.label.toLowerCase()} highlight` : `Highlight ${c.label.toLowerCase()}`}
                aria-pressed={on}
                className="flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-full"
              >
                <span className={`h-7 w-7 rounded-full ${c.className} ${on && highlight ? 'ring-2 ring-white ring-offset-2 ring-offset-slate-950' : ''}`} />
              </button>
            );
          })}
        </div>

        <div className="grid grid-cols-4 gap-1 sm:grid-cols-5">
          <button
            type="button"
            disabled={busy}
            onClick={() => onToggleMark('highlight', highlight ? highlight.color : color)}
            className={ACTION_CLASS}
            data-testid="bible-action-highlight"
          >
            {highlight ? <BookmarkCheck size={18} className="text-amber-300" /> : <Highlighter size={18} className="text-amber-300" />}
            <span>{highlight ? 'Remove highlight' : 'Highlight'}</span>
          </button>

          <button
            type="button"
            disabled={busy}
            onClick={() => onToggleMark('bookmark', '')}
            className={ACTION_CLASS}
            data-testid="bible-action-bookmark"
          >
            <Bookmark size={18} className={bookmark ? 'text-purple-300' : 'text-slate-400'} />
            <span>{bookmark ? 'Remove bookmark' : 'Bookmark'}</span>
          </button>

          <button
            type="button"
            disabled={busy}
            onClick={() => void openStudy()}
            className={ACTION_CLASS}
            data-testid="bible-action-study"
          >
            <BookMarked size={18} className="text-amber-300" />
            <span>Study notes</span>
          </button>

          <button
            type="button"
            disabled={busy}
            onClick={() => {
              void trigger('light');
              setAskOpen(true);
            }}
            className={ACTION_CLASS}
            data-testid="bible-action-ask"
          >
            <Sparkles size={18} className="text-sky-300" />
            <span>Ask about this verse</span>
          </button>

          <button
            type="button"
            disabled={busy}
            onClick={() => void memorise()}
            className={ACTION_CLASS}
            data-testid="bible-action-memorize"
          >
            <Brain size={18} className="text-fuchsia-300" />
            <span>Memorise this verse</span>
          </button>

          <button
            type="button"
            disabled={busy}
            onClick={() => void saveNote()}
            className={ACTION_CLASS}
            data-testid="bible-action-note"
          >
            <NotebookPen size={18} className="text-slate-400" />
            <span>Save to Notes</span>
          </button>

          {noteMark && (
            <button
              type="button"
              disabled={busy}
              onClick={() => void openNote()}
              className={ACTION_CLASS}
              data-testid="bible-action-open-note"
            >
              <NotebookPen size={18} className="text-sky-300" />
              <span>Open your note</span>
            </button>
          )}

          <button
            type="button"
            disabled={busy}
            onClick={() => void copy()}
            className={ACTION_CLASS}
            data-testid="bible-action-copy"
          >
            <Link2 size={18} className="text-slate-400" />
            <span>Copy</span>
          </button>

          <button
            type="button"
            disabled={busy}
            onClick={() => void share()}
            className={ACTION_CLASS}
            data-testid="bible-action-share"
          >
            <Share2 size={18} className="text-slate-400" />
            <span>Share</span>
          </button>

          <button
            type="button"
            disabled={busy}
            onClick={() => setShareChatOpen(true)}
            className={ACTION_CLASS}
            data-testid="bible-action-share-chat"
          >
            <MessageCircle size={18} className="text-sky-300" />
            <span>Share to Family chat</span>
          </button>

          <button
            type="button"
            disabled={busy}
            onClick={() => void shareImage()}
            className={ACTION_CLASS}
            data-testid="bible-action-share-image"
          >
            <ImageIcon size={18} className="text-slate-400" />
            <span>Share as image</span>
          </button>

          {blbVerse.data ? (
            <a
              href={blbVerse.data.url}
              target="_blank"
              rel="noopener noreferrer"
              onClick={blbTapped}
              className={ACTION_CLASS}
              data-testid="bible-action-blb"
            >
              <ExternalLink size={18} className="text-slate-400" />
              <span>Open on Blue Letter Bible</span>
            </a>
          ) : (
            <button type="button" onClick={() => setStatus(linkError(blbVerse))} className={`${ACTION_CLASS} opacity-60`} data-testid="bible-action-blb">
              <ExternalLink size={18} className="text-slate-400" />
              <span>Open on Blue Letter Bible</span>
            </button>
          )}

          {lexicon.data?.installed && (
            <button
              type="button"
              onClick={() => {
                void trigger('light');
                setWordStudy('');
              }}
              className={ACTION_CLASS}
              data-testid="bible-action-word-study"
            >
              <Languages size={18} className="text-sky-300" />
              <span>Word study (Strong&apos;s)</span>
            </button>
          )}

          {interlinear.data?.available ? (
            <button
              type="button"
              onClick={() => setInterlinearOpen(true)}
              className={ACTION_CLASS}
              data-testid="bible-action-interlinear"
            >
              <ScrollText size={18} className="text-amber-300" />
              <span>Interlinear (Greek &amp; Hebrew)</span>
            </button>
          ) : blbInterlinear.data && !interlinear.isLoading ? (
            <a
              href={blbInterlinear.data.url}
              target="_blank"
              rel="noopener noreferrer"
              onClick={blbTapped}
              className={ACTION_CLASS}
              data-testid="bible-action-interlinear"
            >
              <ScrollText size={18} className="text-slate-400" />
              <span>Interlinear on Blue Letter Bible</span>
            </a>
          ) : (
            <button
              type="button"
              onClick={() => setStatus(interlinear.isLoading ? 'Loading interlinear…' : linkError(blbInterlinear))}
              className={`${ACTION_CLASS} opacity-60`}
              data-testid="bible-action-interlinear"
            >
              <ScrollText size={18} className="text-slate-400" />
              <span>Interlinear (Greek &amp; Hebrew)</span>
            </button>
          )}
        </div>

        {noteBody !== null && (
          <div className="mt-3 rounded-xl border border-white/10 bg-black/30 p-3" data-testid="bible-note-body">
            <p className="mb-1 text-[11px] uppercase tracking-wide text-slate-500">Your note</p>
            <p className="whitespace-pre-wrap text-xs text-slate-200">{noteBody}</p>
            {noteMark && noteBody.trim() && (
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  run('Remembered', async () => {
                    await api.rememberBibleNote(noteMark.id, noteBody, verse.reference);
                    return 'Jarvis will remember this note when you study';
                  })
                }
                className="mt-2 min-h-11 rounded-lg border border-white/10 px-3 text-xs text-sky-200 hover:bg-white/5"
                data-testid="bible-note-remember"
              >
                Remember in study memory
              </button>
            )}
          </div>
        )}

        {status && (
          <p className="mt-3 text-xs text-slate-400" data-testid="bible-sheet-status">
            {status}
          </p>
        )}

        {tokens.length > 0 && <WordForWordKey inferred={tokens.some((t) => t.strongs && t.inferred)} />}
      </div>

      {studyOpen && (
        <BibleStudyNotes
          passage={verse.reference}
          version={version}
          edition={edition}
          crossVersion={crossVersion}
          onEditionChange={onEditionChange}
          onCrossVersionChange={onCrossVersionChange}
          onClose={() => setStudyOpen(false)}
        />
      )}

      {interlinearOpen && interlinear.data?.available && (
        <InterlinearPanel
          data={interlinear.data}
          blbUrl={blbInterlinear.data?.url}
          onWord={lexicon.data?.installed ? (strongs) => setWordStudy(strongs) : undefined}
          onClose={() => setInterlinearOpen(false)}
        />
      )}

      {wordStudy !== null && (
        <WordStudyPanel
          reference={verse.reference}
          initialStrongs={wordStudy || undefined}
          onOpenRef={
            onOpenRef
              ? (ref) => {
                  setWordStudy(null);
                  onClose();
                  onOpenRef(ref);
                }
              : undefined
          }
          onClose={() => setWordStudy(null)}
        />
      )}

      {shareChatOpen && (
        <ShareToChatSheet
          reference={verse.reference}
          text={verse.text}
          version={version}
          onClose={() => setShareChatOpen(false)}
          onSent={() => onShared?.('chat')}
        />
      )}

      {askOpen && (
        <JarvisStudyThread
          passage={verse.reference}
          version={version}
          edition={edition}
          crossVersion={crossVersion}
          onClose={() => setAskOpen(false)}
          onOpenRef={
            onOpenRef
              ? (ref) => {
                  setAskOpen(false);
                  onClose();
                  onOpenRef(ref);
                }
              : undefined
          }
        />
      )}
    </div>
  );
}