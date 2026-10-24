/** Planned workouts as calendar events (Health hub Phase 8: Calendar workouts). */

export const WORKOUT_KINDS = [
  { id: 'walk', label: 'Walk', emoji: '🚶' },
  { id: 'run', label: 'Run', emoji: '🏃' },
  { id: 'bike', label: 'Bike ride', emoji: '🚴' },
  { id: 'swim', label: 'Swim', emoji: '🏊' },
  { id: 'strength', label: 'Strength', emoji: '💪' },
  { id: 'stretch', label: 'Stretch / yoga', emoji: '🧘' },
] as const;

export type WorkoutKind = (typeof WORKOUT_KINDS)[number]['id'];

/** The calendar event title a planned workout becomes, e.g. "🏃 Run · 30 min". */
export function workoutEventSummary(kind: WorkoutKind, minutes: number): string {
  const entry = WORKOUT_KINDS.find((candidate) => candidate.id === kind) ?? WORKOUT_KINDS[0];
  return `${entry.emoji} ${entry.label} · ${minutes} min`;
}
