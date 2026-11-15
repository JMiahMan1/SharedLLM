import type { Locator, Page, TestInfo } from '@playwright/test';

/**
 * Findings for one screen, gathered rather than asserted so a spec can report
 * every problem at once: controls shorter than a thumb (phones only), text cut
 * off by a box too narrow for it, and anything past the right edge.
 */
export interface Findings {
  small: string[];
  clipped: string[];
}

/** Whether this project is a phone (the 44px rule applies to touch-sized screens). */
export function isPhone(page: Page): boolean {
  return (page.viewportSize()?.width ?? 1440) < 600;
}

export async function audit(scope: Locator, phone: boolean, min = 44): Promise<Findings> {
  return scope.evaluate((root, args) => {
    const visible = (el: HTMLElement) => {
      const b = el.getBoundingClientRect();
      const s = getComputedStyle(el);
      // A 1px box is a screen-reader-only label (`sr-only`, or a responsive `md:sr-only`).
      return b.width > 1 && b.height > 1 && s.visibility !== 'hidden' && s.display !== 'none' && !el.closest('.sr-only');
    };
    const label = (el: Element) => (el.getAttribute('aria-label') || el.textContent || el.tagName).trim().replace(/\s+/g, ' ').slice(0, 40);
    const small: string[] = [];
    if (args.phone) {
      for (const el of Array.from(root.querySelectorAll<HTMLElement>('button, a[href], select, input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"]):not([type="range"]), [role="tab"], [role="switch"]'))) {
        if (!visible(el)) continue;
        // A control inside a label is measured by the label (the whole row is the target).
        const target = (el.closest('label') as HTMLElement | null) ?? el;
        const b = target.getBoundingClientRect();
        // A ::before hit area (the shared Toggle) widens the target past its box.
        const before = getComputedStyle(el, '::before');
        const extra = before.content !== 'none' && before.position === 'absolute' ? 20 : 0;
        if (b.height + extra + 0.5 < args.min) small.push(`${label(el)} (${Math.round(b.height)}px)`);
      }
    }
    const clipped: string[] = [];
    for (const el of Array.from(root.querySelectorAll<HTMLElement>('*'))) {
      if (!visible(el)) continue;
      const hasText = Array.from(el.childNodes).some((n) => n.nodeType === 3 && (n.textContent ?? '').trim().length > 0);
      if (!hasText) continue;
      const s = getComputedStyle(el);
      // An ellipsis is a deliberate cut; a hard clip is not.
      if (s.textOverflow === 'ellipsis' || s.webkitLineClamp !== 'none') continue;
      const hiddenX = s.overflowX === 'hidden' || s.overflowX === 'clip';
      if (hiddenX && el.scrollWidth > el.clientWidth + 1) clipped.push(`${label(el)} (${el.scrollWidth}>${el.clientWidth})`);
    }
    return { small, clipped };
  }, { phone, min });
}

/** The scrolling element of the shell (main on phones, the window elsewhere) scrolled to `top`. */
export async function scrollShell(page: Page, top: number): Promise<void> {
  await page.evaluate((t) => {
    const main = document.querySelector('main');
    if (main) main.scrollTo({ top: t, behavior: 'instant' as ScrollBehavior });
    window.scrollTo({ top: t, behavior: 'instant' as ScrollBehavior });
  }, top);
}

/**
 * Screenshots down a long page, one viewport at a time (the shell scrolls its
 * own main element, so a full-page shot only shows the first screen).
 */
export async function snapScrolling(page: Page, info: TestInfo, name: string, maxShots = 6): Promise<void> {
  const total = await page.evaluate(() => {
    const main = document.querySelector('main');
    return Math.max(main?.scrollHeight ?? 0, document.documentElement.scrollHeight);
  });
  const h = page.viewportSize()?.height ?? 800;
  for (let i = 0, top = 0; i < maxShots && top < total; i += 1, top += h - 80) {
    await scrollShell(page, top);
    await page.waitForTimeout(150);
    await page.screenshot({ path: info.outputPath(`${name}-${i}.png`) });
  }
  await scrollShell(page, 0);
}
