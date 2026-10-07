/**
 * The wash behind the text in focus mode, chosen by the hour it is being read.
 *
 * A pure function of the hour rather than of ``Date.now()`` so the mapping is
 * readable and testable, and so the same reading is always the same colour. It
 * lives apart from the component because a module that exports a component and
 * a plain function cannot be hot-reloaded.
 */
export function ambienceFor(hour: number): string {
  if (hour < 5) return 'from-slate-950 via-indigo-950/40 to-slate-950';
  if (hour < 9) return 'from-amber-950/30 via-slate-950 to-slate-950';
  if (hour < 17) return 'from-sky-950/25 via-slate-950 to-slate-950';
  if (hour < 21) return 'from-orange-950/25 via-slate-950 to-slate-950';
  return 'from-slate-950 via-violet-950/40 to-slate-950';
}
