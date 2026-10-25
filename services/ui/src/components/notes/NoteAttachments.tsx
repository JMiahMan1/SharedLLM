import { useEffect, useMemo, useRef, useState } from 'react';
import { cachedUrl } from '../../lib/blobUrlCache';
import { useAudioRecorder } from '../../hooks/useAudioRecorder';
import { canvasPoint, paintSegment, type Point } from '../family/canvasPoint';
import { base64ToBytes, blobToBase64 } from '../../lib/base64';
import { FileText, ImagePlus, Loader2, Mic, Paperclip, PenLine, Square, Trash2, X } from 'lucide-react';
import { api } from '../../services/api';
import { errorText } from '../../lib/errorText';
import {
  ATTACHMENT_MAX_BYTES,
  attachmentKind,
  attachmentMarkdown,
  attachmentName,
  listAttachments,
  type NoteAttachment,
} from '../../lib/noteAttachments';

/** The folder a note is in: attachment links are relative to it. */
function noteFolder(path: string): string {
  return path.split('/').slice(0, -1).join('/');
}

/**
 * One attachment's file, read through the note's own account and shown via
 * an object URL (the API needs the app's token, which an <img src> cannot
 * send), from the same per-page cache chat photos use.
 */
function useAttachmentUrl(notePath: string | undefined, link: string, asUser?: 'admin') {
  const [loaded, setLoaded] = useState<{ key: string; url?: string; error?: unknown } | null>(null);
  const key = notePath ? `note:${asUser ?? 'me'}:${noteFolder(notePath)}:${link}` : '';
  useEffect(() => {
    if (!key) return;
    let live = true;
    cachedUrl(key, async () => {
      const res = await api.noteAttachment({ action: 'read', path: notePath!, attachment: link, as_user: asUser });
      const data = res.detail?.data;
      if (res.status !== 'SUCCESS' || typeof data !== 'string') throw new Error(res.message || 'Could not load the attachment');
      const mime = typeof res.detail?.mime === 'string' ? res.detail.mime : 'application/octet-stream';
      return new Blob([base64ToBytes(data)], { type: mime });
    })
      .then((url) => live && setLoaded({ key, url }))
      .catch((error: unknown) => live && setLoaded({ key, error }));
    return () => {
      live = false;
    };
  }, [key, notePath, link, asUser]);
  const current = loaded?.key === key ? loaded : null;
  return { url: current?.url ?? null, isLoading: Boolean(key) && !current, error: current?.error };
}

/** An attachment as it reads in a note: the picture, a player, or the file to open. */
export function AttachmentView({
  notePath,
  link,
  name,
  asUser,
  compact = false,
}: {
  notePath?: string;
  link: string;
  name?: string;
  asUser?: 'admin';
  compact?: boolean;
}) {
  const label = name || attachmentName(link);
  const kind = attachmentKind(label);
  // A card does not fetch a voice note or a file just to name it.
  const fetchIt = !compact || kind === 'image';
  const { url, isLoading, error } = useAttachmentUrl(fetchIt ? notePath : undefined, link, asUser);
  if (!fetchIt || !notePath) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-white/5 px-2 py-0.5 text-[10px] text-slate-300">
        {kind === 'audio' ? <Mic size={10} /> : <Paperclip size={10} />} {label}
      </span>
    );
  }
  if (isLoading) {
    return (
      <span className="inline-flex items-center gap-1 text-[11px] text-slate-400" role="status">
        <Loader2 size={11} className="animate-spin" /> Loading {label}…
      </span>
    );
  }
  if (error || !url) {
    return (
      <span className="inline-flex items-center gap-1 text-[11px] text-rose-300" role="alert">
        <Paperclip size={11} /> {label}: {errorText(error, 'could not be loaded')}
      </span>
    );
  }
  const shown = kind;
  if (shown === 'image') {
    return <img src={url} alt={label} className={`rounded-lg ${compact ? 'max-h-32' : 'max-h-80'} max-w-full object-contain`} />;
  }
  if (shown === 'audio') {
    return <audio controls src={url} aria-label={label} className="w-full max-w-sm" />;
  }
  return (
    <a href={url} download={label} className="inline-flex items-center gap-1 text-xs text-purple-300 underline">
      <FileText size={12} /> {label}
    </a>
  );
}

const PEN_COLOURS = ['#f8fafc', '#dc2626', '#2563eb', '#16a34a', '#eab308'];
const PEN_WIDTH = 4;

/** A finger or mouse drawing, saved as a PNG attachment. */
function DrawingPad({ onDone, onCancel }: { onDone: (png: Blob) => Promise<void>; onCancel: () => void }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const last = useRef<Point | null>(null);
  const [colour, setColour] = useState(PEN_COLOURS[0]);
  const [drawn, setDrawn] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  // Asked once, before the canvas exists, so the message is part of the first render.
  const [supported] = useState(() => {
    try {
      return Boolean(document.createElement('canvas').getContext('2d'));
    } catch {
      return false;
    }
  });

  const context = () => canvasRef.current?.getContext('2d') ?? null;

  useEffect(() => {
    // A dark page, so the PNG reads the same in Nextcloud as here.
    const ctx = context();
    if (!ctx) return;
    ctx.fillStyle = '#0f172a';
    ctx.fillRect(0, 0, ctx.canvas.width, ctx.canvas.height);
  }, []);

  // The family board's own pointer scaling and stroke painting.
  const point = (event: React.PointerEvent<HTMLCanvasElement>): Point =>
    canvasPoint(event.clientX, event.clientY, event.currentTarget.getBoundingClientRect(), event.currentTarget);

  const save = async () => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    setBusy(true);
    setError('');
    try {
      const png = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/png'));
      if (!png) throw new Error('The drawing could not be saved as a picture.');
      await onDone(png);
    } catch (err) {
      setError(errorText(err, 'Could not add the drawing'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-2 rounded-xl border border-white/10 p-2" data-testid="note-drawing">
      <canvas
        ref={canvasRef}
        width={800}
        height={500}
        aria-label="Drawing"
        className="w-full touch-none rounded-lg bg-slate-900"
        onPointerDown={(e) => {
          const ctx = context();
          if (!ctx) return;
          e.currentTarget.setPointerCapture?.(e.pointerId);
          const p = point(e);
          last.current = p;
          paintSegment(ctx, { color: colour, width: PEN_WIDTH, x1: p.x, y1: p.y, x2: p.x + 0.1, y2: p.y + 0.1 });
          setDrawn(true);
        }}
        onPointerMove={(e) => {
          const ctx = context();
          const from = last.current;
          if (!ctx || !from) return;
          const p = point(e);
          paintSegment(ctx, { color: colour, width: PEN_WIDTH, x1: from.x, y1: from.y, x2: p.x, y2: p.y });
          last.current = p;
        }}
        onPointerUp={() => {
          last.current = null;
        }}
        onPointerLeave={() => {
          last.current = null;
        }}
      />
      <div className="flex flex-wrap items-center gap-2">
        {PEN_COLOURS.map((c) => (
          <button
            key={c}
            type="button"
            aria-label={`Pen ${c}`}
            aria-pressed={colour === c}
            className="flex h-9 w-9 items-center justify-center"
            onClick={() => setColour(c)}
          >
            <span className={`h-6 w-6 rounded-full border-2 ${colour === c ? 'border-white' : 'border-transparent'}`} style={{ background: c }} />
          </button>
        ))}
        <button
          type="button"
          className="glass-button min-h-9 px-3 py-1 text-xs"
          disabled={busy || !drawn}
          onClick={() => void save()}
        >
          {busy ? <Loader2 size={13} className="animate-spin" /> : <PenLine size={13} />} Add drawing
        </button>
        <button type="button" className="min-h-9 rounded-xl px-3 py-1 text-xs text-slate-300 hover:bg-white/5" onClick={onCancel}>
          <X size={13} className="mr-1 inline" /> Cancel
        </button>
      </div>
      {(error || !supported) && (
        <p className="text-xs text-rose-300" role="alert">
          {error || 'Drawing is not available in this browser.'}
        </p>
      )}
    </div>
  );
}

/** The extension a recording is saved with, from the recorder's own type. */
function recordingExtension(mime: string): string {
  if (mime.includes('mp4') || mime.includes('aac')) return 'm4a';
  if (mime.includes('ogg')) return 'ogg';
  if (mime.includes('wav')) return 'wav';
  return 'webm';
}

function stamp(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
}

/**
 * A saved note's attachments: what it links to (picture, player or file,
 * each removable) and the ways to add one: a photo or file, a drawing, a
 * voice note. Each is uploaded beside the note, then its link is added to
 * the note's text, which the editor then saves like any other change.
 */
export default function NoteAttachments({
  notePath,
  content,
  asUser,
  onAdd,
  onRemove,
  drawnInText = false,
}: {
  /** The saved note; undefined for a note not saved yet. */
  notePath?: string;
  /** The editor already draws them in the text: list names only. */
  drawnInText?: boolean;
  content: string;
  asUser?: 'admin';
  /** Put this attachment's markdown line into the note. */
  onAdd: (line: string) => void;
  /** Take this attachment's link out of the note (its file is already gone). */
  onRemove: (link: string) => void;
}) {
  const attachments = useMemo(() => listAttachments(content), [content]);
  const [busy, setBusy] = useState<string>('');
  const [error, setError] = useState('');
  const [drawingOpen, setDrawingOpen] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const upload = async (blob: Blob, filename: string) => {
    if (!notePath) return;
    if (blob.size > ATTACHMENT_MAX_BYTES) {
      throw new Error(`${filename} is larger than ${ATTACHMENT_MAX_BYTES / (1024 * 1024)} MB.`);
    }
    const res = await api.noteAttachment({
      action: 'upload',
      path: notePath,
      filename,
      data: await blobToBase64(blob),
      as_user: asUser,
    });
    const link = res.detail?.link;
    if (res.status !== 'SUCCESS' || typeof link !== 'string') throw new Error(res.message || 'Could not add the attachment');
    const name = typeof res.detail?.name === 'string' ? res.detail.name : filename;
    onAdd(attachmentMarkdown(link, name, typeof res.detail?.mime === 'string' ? res.detail.mime : blob.type));
  };

  const run = async (label: string, action: () => Promise<void>) => {
    setBusy(label);
    setError('');
    try {
      await action();
    } catch (err) {
      setError(errorText(err, 'Could not change the attachments'));
    } finally {
      setBusy('');
    }
  };

  const remove = (attachment: NoteAttachment) =>
    run(`remove:${attachment.link}`, async () => {
      if (!window.confirm(`Delete ${attachment.name} from this note?`)) return;
      const res = await api.noteAttachment({ action: 'delete', path: notePath!, attachment: attachment.link, as_user: asUser });
      if (res.status !== 'SUCCESS') throw new Error(res.message || 'Could not delete the attachment');
      onRemove(attachment.link);
    });

  // The same recorder as chat's voice messages.
  const recorder = useAudioRecorder({
    onRecorded: (blob) => void run('voice', () => upload(blob, `voice-${stamp()}.${recordingExtension(blob.type)}`)),
    onError: (reason) =>
      setError(
        reason === 'unavailable'
          ? 'Recording is not available on this device.'
          : reason === 'empty'
            ? 'Nothing was recorded. Check the microphone and try again.'
            : 'Microphone permission is needed to record a voice note.',
      ),
  });
  const recording = recorder.recording;
  const startRecording = () => {
    setError('');
    return recorder.start();
  };
  const stopRecording = recorder.stop;

  return (
    <section className="space-y-2" aria-label="Attachments" data-testid="note-attachments">
      {attachments.length > 0 && (
        <ul className="flex flex-wrap gap-3">
          {attachments.map((a) => (
            <li key={a.link} className="flex max-w-full flex-col gap-1 rounded-xl border border-white/10 p-2">
              {!drawnInText && <AttachmentView notePath={notePath} link={a.link} name={a.name} asUser={asUser} />}
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-[11px] text-slate-400">{a.name}</span>
                <button
                  type="button"
                  aria-label={`Delete ${a.name}`}
                  disabled={Boolean(busy)}
                  className="flex h-9 w-9 items-center justify-center rounded-lg text-slate-400 hover:bg-white/10 hover:text-rose-300"
                  onClick={() => void remove(a)}
                >
                  {busy === `remove:${a.link}` ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />}
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
      {notePath ? (
        <div className="flex flex-wrap items-center gap-2">
          <input
            ref={fileRef}
            type="file"
            accept="image/*,audio/*,application/pdf"
            className="hidden"
            aria-label="Attach a file"
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = '';
              if (file) void run('file', () => upload(file, file.name));
            }}
          />
          <button
            type="button"
            className="glass-button min-h-11 px-3 py-1.5 text-xs"
            disabled={Boolean(busy) || recording}
            onClick={() => fileRef.current?.click()}
          >
            {busy === 'file' ? <Loader2 size={13} className="animate-spin" /> : <ImagePlus size={13} />} Photo or file
          </button>
          <button
            type="button"
            className="glass-button min-h-11 px-3 py-1.5 text-xs"
            disabled={Boolean(busy) || recording}
            aria-pressed={drawingOpen}
            onClick={() => setDrawingOpen(!drawingOpen)}
          >
            <PenLine size={13} /> Draw
          </button>
          {recording ? (
            <button type="button" className="glass-button min-h-11 px-3 py-1.5 text-xs text-rose-300" onClick={stopRecording}>
              <Square size={13} /> Stop recording
            </button>
          ) : (
            <button
              type="button"
              className="glass-button min-h-11 px-3 py-1.5 text-xs"
              disabled={Boolean(busy)}
              onClick={() => void startRecording()}
            >
              {busy === 'voice' ? <Loader2 size={13} className="animate-spin" /> : <Mic size={13} />} Voice note
            </button>
          )}
        </div>
      ) : (
        <p className="text-[11px] text-slate-500">Save the note first to add photos, drawings or voice notes.</p>
      )}
      {drawingOpen && notePath && (
        <DrawingPad
          onCancel={() => setDrawingOpen(false)}
          onDone={async (png) => {
            await upload(png, `drawing-${stamp()}.png`);
            setDrawingOpen(false);
          }}
        />
      )}
      {error && (
        <p className="text-xs text-rose-300" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
