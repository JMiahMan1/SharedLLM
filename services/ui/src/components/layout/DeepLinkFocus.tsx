import { useEffect, useRef } from 'react';
import toast from 'react-hot-toast';
import { useLocation, useNavigate } from 'react-router-dom';
import { FOCUS_PARAM, focusNoun, focusSelector } from '../../lib/noticeLink';

/** How long a page may take to draw the item a link points at before it is called gone. */
export const FOCUS_WAIT_MS = 12_000;
/** How long the item stays highlighted once found. */
export const HIGHLIGHT_MS = 3_500;
export const FOCUS_GONE_TOAST = 'deep-link-gone';
/** Marks a gate the person must pass first (a locked private chat): the wait pauses while it shows. */
export const WAIT_SELECTOR = '[data-deep-link-wait]';

function prefersReducedMotion(): boolean {
  try {
    return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
  } catch {
    return false;
  }
}

/**
 * A tapped notice can point at one item on its page (`?focus=msg-42`, see
 * lib/noticeLink.ts): once the page has drawn it, the item is scrolled into
 * view and highlighted. An item the page never draws (deleted, answered, or no
 * longer this person's to see) leaves the page as it is and says so. While a
 * gate is on screen (a private chat's lock) the wait is paused, so unlocking
 * slowly does not read as "gone". The parameter is then dropped, so going back
 * or reloading does not repeat it.
 *
 * Pages mark their items with `data-focus-id`; nothing else is needed.
 */
export default function DeepLinkFocus() {
  const location = useLocation();
  const navigate = useNavigate();
  const handled = useRef<string>('');
  const latest = useRef(location);
  useEffect(() => {
    latest.current = location;
  }, [location]);

  useEffect(() => {
    const focus = new URLSearchParams(location.search).get(FOCUS_PARAM);
    if (!focus) return;
    const key = `${location.key}:${focus}`;
    if (handled.current === key) return;
    handled.current = key;

    let done = false;
    let waited = 0;
    const finish = () => {
      done = true;
      observer.disconnect();
      window.clearInterval(ticker);
      // Drop the parameter, keeping the rest of the address (as the page may have
      // changed it meanwhile) and the page's own state.
      const now = latest.current;
      const next = new URLSearchParams(now.search);
      if (!next.has(FOCUS_PARAM)) return;
      next.delete(FOCUS_PARAM);
      const search = next.toString();
      navigate({ pathname: now.pathname, search: search ? `?${search}` : '', hash: now.hash }, { replace: true, state: now.state });
    };
    const tryFind = () => {
      if (done) return;
      const el = document.querySelector<HTMLElement>(focusSelector(focus));
      if (!el) return;
      el.scrollIntoView?.({ block: 'center', behavior: prefersReducedMotion() ? 'auto' : 'smooth' });
      el.classList.add('deep-link-highlight');
      el.setAttribute('data-deep-linked', 'true');
      window.setTimeout(() => {
        el.classList.remove('deep-link-highlight');
        el.removeAttribute('data-deep-linked');
      }, HIGHLIGHT_MS);
      finish();
    };
    const STEP = 250;
    const ticker = window.setInterval(() => {
      if (done) return;
      if (document.querySelector(WAIT_SELECTOR)) return; // behind a gate: not counted
      waited += STEP;
      if (waited < FOCUS_WAIT_MS) return;
      toast(`That ${focusNoun(focus)} is no longer here. Showing the page it was on.`, {
        id: FOCUS_GONE_TOAST,
        icon: 'ℹ️',
        duration: 6000,
      });
      finish();
    }, STEP);
    const observer = new MutationObserver(tryFind);
    observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['data-focus-id'] });
    tryFind();
    return () => {
      if (!done) {
        // Another page or another link: the next run starts over.
        observer.disconnect();
        window.clearInterval(ticker);
        handled.current = '';
      }
    };
  }, [location.key, location.search, navigate]);

  return null;
}
