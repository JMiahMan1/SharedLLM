import { Star } from 'lucide-react';

/** Five stars: shows a value, and sets one when `onRate` is given (tap the same star again to clear). */
export default function StarRating({
  value,
  onRate,
  size = 14,
  label = 'Rating',
}: {
  value: number | null;
  onRate?: (stars: number) => void;
  size?: number;
  label?: string;
}) {
  const shown = value ?? 0;
  if (!onRate) {
    return (
      <span className="inline-flex items-center gap-0.5" aria-label={value ? `${label}: ${value} of 5` : `${label}: not rated`}>
        {[1, 2, 3, 4, 5].map((n) => (
          <Star
            key={n}
            size={size}
            className={n <= Math.round(shown) ? 'fill-amber-300 text-amber-300' : 'text-slate-600'}
            aria-hidden
          />
        ))}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center" role="radiogroup" aria-label={label}>
      {[1, 2, 3, 4, 5].map((n) => (
        <button
          key={n}
          type="button"
          role="radio"
          aria-checked={n === value}
          aria-label={`${n} star${n === 1 ? '' : 's'}`}
          onClick={() => onRate(n === value ? 0 : n)}
          className="flex h-11 w-9 items-center justify-center"
        >
          <Star size={size} className={n <= shown ? 'fill-amber-300 text-amber-300' : 'text-slate-500'} />
        </button>
      ))}
    </span>
  );
}
