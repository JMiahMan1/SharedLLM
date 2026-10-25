import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import { Loader2 } from 'lucide-react';
import { TrackRow, type RowItem } from './TrackRow';
import { secondaryButton } from './ui';

/** Rows are drawn only near the viewport once a list is this long. */
export const VIRTUALIZE_AFTER = 100;
const ROW_HEIGHT = 60;

/** The scrolling ancestor (the app shell's <main>), else the document. */
function scrollParent(el: HTMLElement | null): HTMLElement | null {
  for (let node = el?.parentElement ?? null; node; node = node.parentElement) {
    const { overflowY } = getComputedStyle(node);
    if (overflowY === 'auto' || overflowY === 'scroll') return node;
  }
  return (document.scrollingElement as HTMLElement | null) ?? null;
}

/**
 * A long list of items that keeps loading as it is scrolled (§4.2 Library):
 * past 100 rows only the visible ones are in the page (react-virtual, against
 * the shell's scroll area), and reaching the end asks for the next page. A
 * "Load more" button does the same for keyboards and screen readers.
 */
export function ItemList({
  items, hasMore, loadingMore, onLoadMore, label,
}: {
  items: RowItem[];
  hasMore?: boolean;
  loadingMore?: boolean;
  onLoadMore?: () => void;
  label: string;
}) {
  const listRef = useRef<HTMLUListElement>(null);
  const sentinelRef = useRef<HTMLDivElement>(null);
  const [scrollEl, setScrollEl] = useState<HTMLElement | null>(null);
  const [margin, setMargin] = useState(0);
  const virtualize = items.length > VIRTUALIZE_AFTER;

  useLayoutEffect(() => {
    if (!virtualize || !listRef.current) return;
    const parent = scrollParent(listRef.current);
    setScrollEl(parent);
    if (parent) {
      const offset = listRef.current.getBoundingClientRect().top - parent.getBoundingClientRect().top + parent.scrollTop;
      setMargin(offset);
    }
  }, [virtualize]);

  // eslint-disable-next-line react-hooks/incompatible-library -- TanStack Virtual exposes mutable measurement functions by design.
  const virtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => scrollEl,
    estimateSize: () => ROW_HEIGHT,
    overscan: 8,
    scrollMargin: margin,
    enabled: virtualize,
  });

  // Reaching the end loads the next page.
  useEffect(() => {
    const el = sentinelRef.current;
    if (!el || !hasMore || !onLoadMore || typeof IntersectionObserver === 'undefined') return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting) && !loadingMore) onLoadMore();
    }, { rootMargin: '400px' });
    observer.observe(el);
    return () => observer.disconnect();
  }, [hasMore, loadingMore, onLoadMore]);

  const rows = virtualize ? virtualizer.getVirtualItems() : null;
  return (
    <>
      <ul
        ref={listRef}
        aria-label={label}
        className={virtualize ? 'relative' : 'space-y-1'}
        style={virtualize ? { height: virtualizer.getTotalSize() } : undefined}
      >
        {rows
          ? rows.map((v) => (
            <TrackRow
              key={items[v.index].uri || v.index}
              item={items[v.index]}
              className="absolute left-0 right-0"
              style={{ height: v.size, transform: `translateY(${v.start - margin}px)` }}
            />
          ))
          : items.map((item, i) => <TrackRow key={item.uri || i} item={item} />)}
      </ul>
      <div ref={sentinelRef} aria-hidden />
      {hasMore && onLoadMore ? (
        <div className="flex justify-center pt-2">
          <button type="button" onClick={onLoadMore} disabled={loadingMore} className={secondaryButton}>
            {loadingMore ? <Loader2 size={16} className="animate-spin" aria-hidden /> : null}
            {loadingMore ? 'Loading…' : 'Load more'}
          </button>
        </div>
      ) : null}
    </>
  );
}
