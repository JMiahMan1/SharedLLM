import { useLayoutEffect, type RefObject } from 'react';

/**
 * Publish how tall the bottom bar is as `--bottom-bar` on the root element.
 *
 * The bar sits over the page, and its height depends on the phone (the
 * gesture or button area beneath it), so a padding written as a number was
 * always wrong somewhere: too little hid the last lines and stopped scrolling
 * early, too much left an empty band. Content that ends under the bar pads by
 * this instead (`pb-clear-bottom-bar` in index.css).
 */
export function usePublishBottomBarHeight(ref: RefObject<HTMLElement | null>): void {
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const root = document.documentElement;
    const publish = () => root.style.setProperty('--bottom-bar', `${Math.ceil(el.getBoundingClientRect().height)}px`);
    publish();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(publish);
    // The border box: the gesture area is the bar's padding, and the default
    // (content box) would not notice it changing.
    observer?.observe(el, { box: 'border-box' });
    return () => {
      observer?.disconnect();
      root.style.removeProperty('--bottom-bar');
    };
  }, [ref]);
}
