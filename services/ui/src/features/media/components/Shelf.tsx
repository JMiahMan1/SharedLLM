import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { ChevronRight } from 'lucide-react';
import { shelfTitle } from './ui';

/**
 * A titled row of cards that scrolls sideways with snap points (12 px apart
 * on phones, 16 px on wider screens), with "See all" to the matching
 * library tab.
 */
export function Shelf({ title, seeAll, children }: { title: string; seeAll?: string; children: ReactNode }) {
  const id = `shelf-${title.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`;
  return (
    <section aria-labelledby={id} className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <h2 id={id} className={shelfTitle}>{title}</h2>
        {seeAll ? (
          <Link
            to={seeAll}
            aria-label={`See all ${title.toLowerCase()}`}
            className="inline-flex min-h-11 items-center gap-0.5 rounded-xl px-2 text-sm text-[var(--site-accent-text)] hover:bg-[var(--site-card-bg-hover)]"
          >
            See all<ChevronRight size={16} aria-hidden />
          </Link>
        ) : null}
      </div>
      {children}
    </section>
  );
}

/** The scrolling row inside a shelf. */
export function ShelfRow({ children }: { children: ReactNode }) {
  return (
    <div className="-mx-1 flex snap-x snap-mandatory gap-3 overflow-x-auto px-1 pb-2 sm:gap-4">
      {children}
    </div>
  );
}
