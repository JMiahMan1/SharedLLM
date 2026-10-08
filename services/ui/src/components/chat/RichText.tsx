import { Fragment, type ReactNode } from 'react';
import Attachment from './Attachment';
import { splitRichText, type TalkParameter } from './chatModel';

/**
 * A Talk message's text with its {placeholders} filled in: shared files as
 * attachments, @mentions as chips (yours highlighted), links clickable.
 */
export default function RichText({
  text,
  parameters,
  myActorIds,
  voice = false,
}: {
  text: string;
  parameters?: Record<string, TalkParameter>;
  myActorIds: ReadonlySet<string>;
  voice?: boolean;
}) {
  const parts = splitRichText(text, parameters || {});
  const out: ReactNode[] = [];
  parts.forEach((part, index) => {
    if (part.kind === 'file') {
      out.push(
        <span key={index} className="my-1 block">
          <Attachment file={part.file} voice={voice} />
        </span>,
      );
    } else if (part.kind === 'mention') {
      const me = part.id ? myActorIds.has(part.id.toLowerCase()) : false;
      out.push(
        <span
          key={index}
          className={`rounded-md px-1 font-semibold ${me ? 'bg-amber-400/25 text-amber-100' : 'bg-white/15'}`}
        >
          @{part.label}
        </span>,
      );
    } else if (part.kind === 'link') {
      out.push(
        <a
          key={index}
          href={part.href}
          target="_blank"
          rel="noopener noreferrer"
          onClick={(event) => event.stopPropagation()}
          className="break-all underline decoration-from-font underline-offset-2"
        >
          {part.text}
        </a>,
      );
    } else {
      out.push(<Fragment key={index}>{part.text}</Fragment>);
    }
  });
  return <>{out}</>;
}
