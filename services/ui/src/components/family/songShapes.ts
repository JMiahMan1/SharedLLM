import type { SongJob } from '../../services/api';

/** Song shapes the audio server knows (music_song.STRUCTURES); `clip` is one
 *  section with no structure, capped at what one generation call can write. */
export const STRUCTURES = [
  { id: 'song', label: 'Song', detail: 'Intro · verse · chorus · verse · chorus · bridge · chorus · outro', min: 60, max: 240, step: 10, initial: 120 },
  { id: 'pop', label: 'Pop', detail: 'Verse · pre-chorus · chorus, twice, then bridge and a last chorus', min: 60, max: 240, step: 10, initial: 150 },
  { id: 'ballad', label: 'Ballad', detail: 'Intro · verse · verse · chorus · verse · chorus · outro', min: 60, max: 240, step: 10, initial: 120 },
  { id: 'short', label: 'Short song', detail: 'Intro · verse · chorus · outro', min: 30, max: 120, step: 10, initial: 60 },
  { id: 'clip', label: 'Just a clip', detail: 'One piece, no sections', min: 4, max: 26, step: 1, initial: 12 },
] as const;

export type StructureId = (typeof STRUCTURES)[number]['id'];

export function minutes(seconds: number): string {
  const s = Math.round(seconds);
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

/** What the song is doing now, in words. */
export function songStatus(job: SongJob): string {
  if (job.status === 'queued') {
    const ahead = (job.position ?? 1) - 1;
    return ahead > 0 ? `Waiting · ${ahead} song${ahead > 1 ? 's' : ''} ahead` : 'Up next';
  }
  if (job.status === 'running') {
    const p = job.progress;
    if (!p || !p.total) return 'Starting…';
    if (p.section === 'mixing') return 'Mixing the sections together…';
    return `Writing the ${p.section} (${Math.min(p.done + 1, p.total)} of ${p.total})`;
  }
  if (job.status === 'error') return job.error || 'The song could not be made';
  if (job.status === 'cancelled') return 'Cancelled';
  return 'Ready';
}
