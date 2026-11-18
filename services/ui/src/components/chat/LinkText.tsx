import { Fragment } from 'react';
import { splitLinks } from './chatModel';

/**
 * Plain message text with its web addresses tappable. A tap opens the page in
 * the phone's browser and never reaches the bubble's own tap and long-press.
 */
export default function LinkText({ text }: { text: string }) {
  return (
    <>
      {splitLinks(text).map((part, index) =>
        part.kind === 'link' ? (
          <a
            key={index}
            href={part.href}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(event) => event.stopPropagation()}
            className="break-all underline decoration-from-font underline-offset-2"
            data-testid="private-link"
          >
            {part.text}
          </a>
        ) : (
          <Fragment key={index}>{part.kind === 'text' ? part.text : ''}</Fragment>
        ),
      )}
    </>
  );
}
