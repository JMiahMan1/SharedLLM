import { useState } from 'react';
import { useMediaToken } from '../../lib/mediaToken';
import { recipePhotoUrl } from '../../lib/recipePhoto';
import { MEAL_EMOJI } from '../../lib/recipeDoc';

const GRADIENTS: Record<string, string> = {
  breakfast: 'from-amber-500/30 to-orange-600/20',
  main: 'from-purple-500/30 to-indigo-600/20',
  soup: 'from-orange-500/30 to-red-600/20',
  salad: 'from-emerald-500/30 to-lime-600/20',
  side: 'from-yellow-500/25 to-amber-600/20',
  bread: 'from-amber-400/30 to-yellow-700/20',
  dessert: 'from-pink-500/30 to-rose-600/20',
  snack: 'from-sky-500/30 to-cyan-600/20',
  drink: 'from-cyan-500/30 to-blue-600/20',
};

/**
 * A recipe's photo, or, when it has none (or it will not load), a tile in the
 * colours of its kind of meal with an emoji, so the shelf never shows a hole.
 */
export default function RecipePhoto({
  path,
  url,
  file,
  meal = 'main',
  className = '',
}: {
  /** A cookbook recipe with a kept photo. */
  path?: string | null;
  /** Which of its photos (default: its cover); also keeps a changed cover from showing a stale copy. */
  file?: string | null;
  /** A search result's photo on the web. */
  url?: string | null;
  meal?: string;
  className?: string;
}) {
  useMediaToken();
  // The address that would not load; a new photo gets its own try.
  const [failed, setFailed] = useState<string | null>(null);
  const wanted = recipePhotoUrl(path ? { path, file } : { url });
  const src = wanted && wanted !== failed ? wanted : null;
  if (!src) {
    return (
      <div
        className={`flex items-center justify-center bg-gradient-to-br ${GRADIENTS[meal] ?? GRADIENTS.main} ${className}`}
        aria-hidden
      >
        <span className="-translate-y-3 text-5xl drop-shadow-lg">{MEAL_EMOJI[meal] ?? '🍽️'}</span>
      </div>
    );
  }
  return <img src={src} alt="" loading="lazy" onError={() => setFailed(src)} className={`object-cover ${className}`} />;
}
