/**
 * The small "≠" a verse carries when the two side-by-side translations word it
 * materially differently (`verseDiff.ts`). The same mark sits in the legend,
 * so the reader learns it once. Decorative to a screen reader: the verse says
 * "worded differently" in its own label.
 */
export default function DiffBadge() {
  return (
    <span
      aria-hidden="true"
      className="shrink-0 self-center rounded px-1 text-[0.7em] font-sans font-semibold leading-none text-amber-600 ring-1 ring-amber-500/60 select-none"
    >
      ≠
    </span>
  );
}
