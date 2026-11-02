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
  meal = 'main',
  className = '',
}: {
  /** A cookbook recipe with a kept photo. */
  path?: string | null;
  /** A search result's photo on the web. */
  url?: string | null;
  meal?: string;
  className?: string;
}) {
  useMediaToken();
  const [failed, setFailed] = useState(false);
  const src = failed ? null : recipePhotoUrl(path ? { path } : { url });
  if (!src) {
    return (
      <div
        className={`flex items-center justify-center bg-gradient-to-br ${GRADIENTS[meal] ?? GRADIENTS.main} ${className}`}
        aria-hidden
      >
        <span className="text-4xl drop-shadow">{MEAL_EMOJI[meal] ?? '🍽️'}</span>
      </div>
    );
  }
  return <img src={src} alt="" loading="lazy" onError={() => setFailed(true)} className={`object-cover ${className}`} />;
}
