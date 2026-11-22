import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ImageOff, Loader2, Sparkles } from 'lucide-react';

import { api } from '../../services/api';
import type { StudyBlock } from '../../types/api';

/**
 * An imported study section drawn from its structured blocks.
 *
 * Every block carries an id derived from its own words, used here as the DOM
 * id, so a link or a reminder can open the reader at a paragraph
 * (`#p-1a2b3c4d`) and read aloud can light up the paragraph being spoken.
 * Scripture references are buttons that open the Bible reader at the verse.
 * Footnotes are collected under the section instead of interrupting it, and
 * discussion questions get a place to write an answer, kept on this device.
 */
interface Props {
  bookId: number;
  blocks: StudyBlock[];
  /** The block being read aloud, highlighted and kept in view. */
  activeBlockId?: string | null;
  onOpenRef?: (ref: string) => void;
  onAsk?: (question: string) => void;
  askPending?: boolean;
  askedQuestion?: string | null;
  fontFamily: 'serif' | 'sans';
  fontScale: number;
  lineHeight: number;
}

function answerKey(bookId: number, blockId: string): string {
  return `jarvis.study-answer.${bookId}.${blockId}`;
}

/** Text with its scripture references turned into buttons. */
function Linked({ text, refs, onOpenRef }: { text: string; refs?: StudyBlock['refs']; onOpenRef?: (ref: string) => void }) {
  if (!refs?.length || !onOpenRef) return <>{text}</>;
  const parts: React.ReactNode[] = [];
  let cursor = 0;
  for (const [index, ref] of [...refs].sort((a, b) => a.start - b.start).entries()) {
    if (ref.start < cursor || ref.end > text.length) continue;
    if (ref.start > cursor) parts.push(text.slice(cursor, ref.start));
    parts.push(
      <button
        key={`${ref.start}-${index}`}
        type="button"
        onClick={() => onOpenRef(ref.ref)}
        className="inline rounded-sm px-0.5 text-left text-[var(--page-number)] underline decoration-dotted underline-offset-4 hover:decoration-solid focus-visible:outline focus-visible:outline-2 focus-visible:outline-amber-400"
        aria-label={`Open ${ref.ref} in the Bible`}
        data-testid="study-ref"
      >
        {text.slice(ref.start, ref.end)}
      </button>,
    );
    cursor = ref.end;
  }
  if (cursor < text.length) parts.push(text.slice(cursor));
  return <>{parts}</>;
}

function NoteMarks({ notes, byMarker }: { notes?: string[]; byMarker: Map<string, string> }) {
  if (!notes?.length) return null;
  return (
    <>
      {notes.map((marker) => {
        const target = byMarker.get(marker);
        return target ? (
          <sup key={marker} className="ml-0.5">
            <a
              href={`#${target}`}
              onClick={(event) => {
                event.preventDefault();
                document.getElementById(target)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
              }}
              className="text-[0.7em] text-[var(--page-muted)] no-underline"
              aria-label={`Footnote ${marker}`}
            >
              {marker}
            </a>
          </sup>
        ) : null;
      })}
    </>
  );
}

function StudyImage({ bookId, block }: { bookId: number; block: StudyBlock }) {
  const image = useQuery({
    queryKey: ['bible-book-asset', bookId, block.src],
    queryFn: () => api.getBibleBookAsset(bookId, block.src!),
    enabled: Boolean(block.src),
    staleTime: Infinity,
    retry: false,
  });
  const url = useMemo(() => (image.data ? URL.createObjectURL(image.data) : ''), [image.data]);
  useEffect(() => () => {
    if (url) URL.revokeObjectURL(url);
  }, [url]);
  return (
    <figure className="my-5">
      {url ? (
        <img src={url} alt={block.text || ''} className="mx-auto max-h-[60dvh] max-w-full rounded-lg" loading="lazy" />
      ) : image.isLoading ? (
        <div className="flex h-24 items-center justify-center rounded-lg bg-[var(--page-hover)]">
          <Loader2 size={16} className="animate-spin text-[var(--page-muted)]" />
        </div>
      ) : (
        <div className="flex h-16 items-center justify-center gap-2 rounded-lg bg-[var(--page-hover)] text-xs text-[var(--page-muted)]">
          <ImageOff size={14} /> Image not available
        </div>
      )}
      {block.text ? <figcaption className="mt-1 text-center text-xs text-[var(--page-muted)]">{block.text}</figcaption> : null}
    </figure>
  );
}

function AnswerSpace({ bookId, block, onAsk, askPending, askedQuestion }: {
  bookId: number;
  block: StudyBlock;
  onAsk?: (question: string) => void;
  askPending?: boolean;
  askedQuestion?: string | null;
}) {
  const [answer, setAnswer] = useState(() => {
    try {
      return window.localStorage.getItem(answerKey(bookId, block.id)) ?? '';
    } catch {
      return '';
    }
  });
  useEffect(() => {
    const handle = window.setTimeout(() => {
      try {
        if (answer.trim()) window.localStorage.setItem(answerKey(bookId, block.id), answer);
        else window.localStorage.removeItem(answerKey(bookId, block.id));
      } catch {
        /* storage disabled: the answer still stays on screen */
      }
    }, 400);
    return () => window.clearTimeout(handle);
  }, [answer, bookId, block.id]);
  return (
    <div className="mt-2 space-y-2">
      <label className="sr-only" htmlFor={`answer-${block.id}`}>
        Your answer
      </label>
      <textarea
        id={`answer-${block.id}`}
        value={answer}
        onChange={(event) => setAnswer(event.target.value)}
        rows={2}
        placeholder="Your answer…"
        className="block w-full resize-y rounded-lg border border-[var(--page-rule)] bg-transparent px-3 py-2 font-sans text-sm text-[var(--page-ink)] placeholder:text-[var(--page-muted)] focus:outline-none focus:ring-2 focus:ring-amber-400/60"
        data-testid="study-answer"
      />
      {onAsk ? (
        <button
          type="button"
          onClick={() => onAsk(block.text)}
          disabled={askPending}
          className="inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-[var(--page-rule)] px-3 font-sans text-xs text-[var(--page-muted)] disabled:opacity-50"
        >
          {askPending && askedQuestion === block.text ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
          Ask Jarvis
        </button>
      ) : null}
    </div>
  );
}

export default function StudyBlocks({
  bookId,
  blocks,
  activeBlockId,
  onOpenRef,
  onAsk,
  askPending,
  askedQuestion,
  fontFamily,
  fontScale,
  lineHeight,
}: Props) {
  const notes = useMemo(() => blocks.filter((block) => block.type === 'note'), [blocks]);
  const byMarker = useMemo(() => {
    const map = new Map<string, string>();
    for (const note of notes) if (note.marker && !map.has(note.marker)) map.set(note.marker, note.id);
    return map;
  }, [notes]);
  const body = blocks.filter((block) => block.type !== 'note');
  const questionNumbers = useMemo(() => {
    const numbers = new Map<string, number>();
    for (const block of blocks) if (block.type === 'question') numbers.set(block.id, numbers.size + 1);
    return numbers;
  }, [blocks]);

  const frame = (block: StudyBlock, children: React.ReactNode, extra = '') => {
    const active = block.id === activeBlockId;
    return (
      <div
        key={block.id}
        id={block.id}
        data-block-id={block.id}
        data-block-type={block.type}
        data-active={active ? 'true' : undefined}
        className={`scroll-mt-24 rounded-md transition-colors duration-300 ${
          active ? '-mx-2 bg-amber-400/15 px-2 ring-1 ring-amber-400/40' : ''
        } ${extra}`}
      >
        {children}
      </div>
    );
  };

  return (
    <div
      className={`select-text text-[var(--page-ink)] ${fontFamily === 'serif' ? 'font-serif' : 'font-sans'}`}
      style={{ fontSize: `${1.125 * fontScale}rem`, lineHeight }}
      data-testid="bible-study-reader-text"
    >
      {body.map((block) => {
        switch (block.type) {
          case 'heading': {
            const level = block.level ?? 2;
            if (level <= 1) {
              return frame(block, <h2 className="mb-4 mt-8 font-serif text-[1.6em] leading-tight first:mt-0">{block.text}</h2>);
            }
            if (level === 2) {
              return frame(block, <h3 className="mb-3 mt-7 font-serif text-[1.3em] leading-snug">{block.text}</h3>);
            }
            return frame(
              block,
              <h4 className="mb-2 mt-6 font-sans text-[0.8em] font-semibold uppercase tracking-[0.12em] text-[var(--page-muted)]">
                {block.text}
              </h4>,
            );
          }
          case 'quote':
            return frame(
              block,
              <blockquote className="my-4 whitespace-pre-line border-l-2 border-amber-400/50 pl-4 italic">
                <Linked text={block.text} refs={block.refs} onOpenRef={onOpenRef} />
                <NoteMarks notes={block.notes} byMarker={byMarker} />
              </blockquote>,
            );
          case 'list': {
            const items = block.items ?? [];
            const List = block.ordered ? 'ol' : 'ul';
            // Refs index into the items joined by newlines.
            let offset = 0;
            return frame(
              block,
              <List className={`mb-4 space-y-1.5 pl-6 ${block.ordered ? 'list-decimal' : 'list-disc'}`}>
                {items.map((item, index) => {
                  const start = offset;
                  offset += item.length + 1;
                  const refs = (block.refs ?? [])
                    .filter((ref) => ref.start >= start && ref.end <= start + item.length)
                    .map((ref) => ({ ...ref, start: ref.start - start, end: ref.end - start }));
                  return (
                    <li key={`${block.id}-${index}`} className="text-pretty">
                      <Linked text={item} refs={refs} onOpenRef={onOpenRef} />
                    </li>
                  );
                })}
              </List>,
            );
          }
          case 'question': {
            const questionNumber = questionNumbers.get(block.id) ?? 0;
            return frame(
              block,
              <section className="my-5 rounded-xl border border-[var(--page-rule)] p-3" aria-label={`Question ${questionNumber}`}>
                <p className="font-sans text-[0.7em] uppercase tracking-[0.16em] text-[var(--page-muted)]">
                  Question {questionNumber}
                </p>
                <p className="mt-1 text-pretty">
                  <Linked text={block.text} refs={block.refs} onOpenRef={onOpenRef} />
                </p>
                <AnswerSpace
                  bookId={bookId}
                  block={block}
                  onAsk={onAsk}
                  askPending={askPending}
                  askedQuestion={askedQuestion}
                />
              </section>,
              'question',
            );
          }
          case 'table':
            return frame(
              block,
              <div className="my-4 overflow-x-auto">
                <table className="w-full border-collapse font-sans text-[0.85em]">
                  <tbody>
                    {(block.rows ?? []).map((row, rowIndex) => (
                      <tr key={rowIndex} className="border-b border-[var(--page-rule)]">
                        {row.map((cell, cellIndex) =>
                          rowIndex === 0 ? (
                            <th key={cellIndex} className="px-2 py-1.5 text-left font-semibold">
                              {cell}
                            </th>
                          ) : (
                            <td key={cellIndex} className="px-2 py-1.5 align-top">
                              {cell}
                            </td>
                          ),
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>,
            );
          case 'image':
            return frame(block, <StudyImage bookId={bookId} block={block} />);
          default:
            return frame(
              block,
              <p className="mb-4 text-pretty hyphens-auto">
                <Linked text={block.text} refs={block.refs} onOpenRef={onOpenRef} />
                <NoteMarks notes={block.notes} byMarker={byMarker} />
              </p>,
            );
        }
      })}

      {notes.length > 0 ? (
        <section className="mt-10 border-t border-[var(--page-rule)] pt-4 font-sans text-[0.8em]" aria-label="Notes">
          <p className="mb-2 text-[0.85em] uppercase tracking-[0.16em] text-[var(--page-muted)]">Notes</p>
          <ol className="space-y-1.5">
            {notes.map((note) => (
              <li key={note.id} id={note.id} data-block-id={note.id} className="flex scroll-mt-24 gap-2">
                {note.marker ? <span className="shrink-0 tabular-nums text-[var(--page-muted)]">{note.marker}.</span> : null}
                <span>
                  <Linked text={note.text} refs={note.refs} onOpenRef={onOpenRef} />
                </span>
              </li>
            ))}
          </ol>
        </section>
      ) : null}
    </div>
  );
}
