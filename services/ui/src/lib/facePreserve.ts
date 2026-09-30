/**
 * Face-preserving generation: make a NEW picture of the people in an existing
 * photo without drifting their identity.
 *
 * Honest scope, because it shapes the copy in the UI: this is a text-conditioned
 * image-edit model, not an identity-conditioning one (no PuLID/InstantID/IP-Adapter
 * here). The face is held by *instruction*, so retention is approximate and gets
 * weaker the further the new scene drifts from the original pose and lighting.
 * For the same reason the current image backend accepts a single source image, so
 * this cannot borrow a face from a second photo -- that is the face-swap path,
 * and it needs a backend that takes two images.
 */

/** How many people the caller believes are in the photo. */
export type FaceCount = 'one' | 'many' | 'auto';

export const FACE_PRESERVE_SUGGESTIONS = [
  'on the deck of a sailing boat at sunset',
  'in a snowy pine forest, breath visible in the cold',
  'at a dinner table, candlelit, mid-laugh',
  'in a 1970s film still, grainy and warm',
  'as a professional studio headshot on a grey background',
] as const;

const IDENTITY_RULE =
  'Keep every person in the photo recognisably the same individual: preserve ' +
  'their facial structure, bone shape, eye shape and colour, nose, mouth, ' +
  'skin tone, hairline and hair colour, and their age. Do not beautify, ' +
  'smooth, slim or otherwise alter the face.';

const SINGLE_RULE =
  'Keep the person’s face exactly as it is in the original photo — same ' +
  'identity, same features, same skin tone. Do not beautify, smooth or ' +
  'otherwise alter the face.';

const PLURAL_RULE =
  'Keep each person recognisably the same individual as in the original, and ' +
  'keep them consistent with one another: do not blend their faces together ' +
  'and do not give anyone another person’s features. Do not beautify, smooth ' +
  'or otherwise alter any face.';

/** Nudge the model away from the original framing, which is the usual failure. */
const DIVERSITY_RULE =
  'Treat the original as a reference for who these people are, not as the ' +
  'picture to reproduce: change the background, setting, clothing and ' +
  'composition to what is described, while keeping the faces.';

/**
 * Build the prompt for a face-preserving generate.
 *
 * The caller's own words for the new scene are preserved verbatim at the end,
 * because a model given a rewritten instruction tends to follow the rewrite.
 */
export function buildFacePreservePrompt(
  scene: string,
  faces: FaceCount = 'auto',
): string {
  const rule =
    faces === 'one' ? SINGLE_RULE : faces === 'many' ? PLURAL_RULE : IDENTITY_RULE;
  const trimmed = (scene ?? '').trim();
  return [
    'Create a new photograph using the uploaded photo as the identity ' +
      'reference for the people in it.',
    rule,
    DIVERSITY_RULE,
    trimmed ? `Now: ${trimmed}` : 'Now: keep the same people, in a natural setting.',
  ].join('\n');
}

/**
 * True when the prompt already carries a strong identity instruction.
 *
 * Used to avoid stacking two identity blocks when the user typed their own
 * "keep the face" wording — repeating it is not harmless, it makes the model
 * over-weight the face and ignore the new scene.
 */
export function hasIdentityInstruction(prompt: string | null | undefined): boolean {
  if (!prompt) return false;
  const p = prompt.toLowerCase();
  return (
    /\b(keep|preserve|retain|maintain|don'?t change|do not change|same)\b/.test(p) &&
    /\b(face|faces|facial|identity|features|likeness|who (they|he|she) (is|are))\b/.test(p)
  );
}
