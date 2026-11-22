import type { StudyNarrationMark, StudyNarrationPlan } from '../../types/api';

/**
 * Which block a moment in a spoken piece belongs to.
 *
 * The server says where each block starts and ends in the piece's text; speech
 * runs at a roughly even pace, so the share of the audio played is the share
 * of the characters spoken. Good to the paragraph, which is what is lit up.
 */
export function blockAt(marks: StudyNarrationMark[], fraction: number): string | null {
  if (!marks.length) return null;
  const total = marks[marks.length - 1].end || 1;
  const position = Math.max(0, Math.min(1, fraction)) * total;
  for (const mark of marks) if (position <= mark.end) return mark.block_id;
  return marks[marks.length - 1].block_id;
}

/** The piece to start at so reading begins at `block` (the one on screen), else the first. */
export function startingPiece(plan: StudyNarrationPlan, block: string | null): number {
  if (!block) return 0;
  const found = plan.chunks.findIndex((chunk) => chunk.blocks.some((mark) => mark.block_id === block));
  return found >= 0 ? found : 0;
}
