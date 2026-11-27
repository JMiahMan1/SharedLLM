import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import LazyPhotoEditor from './LazyPhotoEditor';
import type { EditRecipe } from '../../lib/photoEdit/recipe';
import type { EditResult } from '../../lib/photoEdit/encode';

/** What the person chose: the edit, the picture as it is, or nothing (closed the editor). */
export type EditChoice = { kind: 'edited'; result: EditResult } | { kind: 'as-is' } | null;

interface Pending {
  blob: Blob;
  recipe?: EditRecipe;
  resolve: (choice: EditChoice) => void;
}

/**
 * Put the editor between choosing a picture and doing something with it,
 * in a couple of lines:
 *
 *   const gate = usePhotoEditGate({ maxBytes });
 *   const choice = await gate.ask(file);        // null: closed, nothing sent
 *   const blob = choice?.kind === 'edited' ? choice.result.blob : file;
 *   ...
 *   return <>{...}{gate.element}</>;
 *
 * "Send as is" is one tap in the editor. The edit's recipe and the untouched
 * original come back on `choice.result` for whoever keeps them (the gallery).
 */
export function usePhotoEditGate({
  maxBytes,
  doneLabel,
  asIsLabel,
  offerAsIs = true,
}: {
  maxBytes: number;
  doneLabel?: string;
  asIsLabel?: string;
  /** False when "as is" means nothing here (editing a picture already sent): the editor then offers only its done button and close. */
  offerAsIs?: boolean;
}): {
  ask: (blob: Blob, recipe?: EditRecipe) => Promise<EditChoice>;
  element: ReactNode;
  open: boolean;
} {
  const [pending, setPending] = useState<Pending | null>(null);
  const pendingRef = useRef<Pending | null>(null);

  const settle = useCallback((choice: EditChoice) => {
    const p = pendingRef.current;
    pendingRef.current = null;
    setPending(null);
    p?.resolve(choice);
  }, []);

  const ask = useCallback(
    (blob: Blob, recipe?: EditRecipe) =>
      new Promise<EditChoice>((resolve) => {
        // A second ask closes the first as "nothing chosen".
        pendingRef.current?.resolve(null);
        const p = { blob, recipe, resolve };
        pendingRef.current = p;
        setPending(p);
      }),
    [],
  );

  // Leaving the page answers an open question.
  useEffect(() => () => pendingRef.current?.resolve(null), []);

  const element = pending ? (
    <LazyPhotoEditor
      source={pending.blob}
      initialRecipe={pending.recipe}
      maxBytes={maxBytes}
      doneLabel={doneLabel}
      asIsLabel={asIsLabel}
      onDone={(result) => settle({ kind: 'edited', result })}
      onAsIs={offerAsIs ? () => settle({ kind: 'as-is' }) : undefined}
      onCancel={() => settle(null)}
    />
  ) : null;

  return { ask, element, open: !!pending };
}
