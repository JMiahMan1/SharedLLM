import type { Locator, Page, TestInfo } from '@playwright/test';

/**
 * Findings for one screen, gathered rather than asserted so a spec can report
 * every problem at once: controls shorter than a thumb (phones only), text cut
 * off by a box too narrow for it, and anything past the right edge.
 */
export interface Findings {
  small: string[];
  clipped: string[];
  /** Anything drawn past the edge of the card, dialog or panel it sits in. */
  escaped: string[];
}

/** Whether this project is a phone (the 44px rule applies to touch-sized screens). */
export function isPhone(page: Page): boolean {
  return (page.viewportSize()?.width ?? 1440) < 600;
}

/**
 * Android's WebView draws date and time fields with a native control much
 * wider than desktop Chrome's (and Samsung's large fonts widen it further). A
 * field with no width of its own keeps that preferred width even in a narrow
 * grid column (form fields do not stretch or shrink there like text boxes), so
 * one that fit here ran past its card on a phone. This gives the fields here
 * the same preferred width, in the base layer, so a width the app sets itself
 * still wins, exactly as on the phone.
 */
export const ANDROID_WIDE_FIELDS = `
  @layer base {
    input[type="time"], input[type="date"], input[type="datetime-local"],
    input[type="month"], input[type="week"] { width: 13rem; }
  }
`;

export async function audit(scope: Locator, phone: boolean, min = 44): Promise<Findings> {
  await scope.page().addStyleTag({ content: ANDROID_WIDE_FIELDS });
  await scope.page().waitForTimeout(50);
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
    // Something that sticks out of its own box: a time field running past a
    // dialog's edge, a button hanging off a card. The page can still fit the
    // screen, so the page-overflow check never saw these.
    const escaped: string[] = [];
    const paints = (s: CSSStyleDeclaration) =>
      parseFloat(s.borderLeftWidth) > 0 || parseFloat(s.borderRightWidth) > 0
      || (s.backgroundColor !== 'rgba(0, 0, 0, 0)' && s.backgroundColor !== 'transparent')
      || s.backgroundImage !== 'none';
    const isBox = (el: HTMLElement) => {
      if (el.getAttribute('role') === 'dialog' || el.tagName === 'DIALOG') return true;
      const s = getComputedStyle(el);
      return paints(s) && parseFloat(s.borderTopLeftRadius) >= 6;
    };
    for (const el of Array.from(root.querySelectorAll<HTMLElement>('*'))) {
      if (!visible(el)) continue;
      const s = getComputedStyle(el);
      if (s.position === 'fixed') continue;
      const text = Array.from(el.childNodes).some((n) => n.nodeType === 3 && (n.textContent ?? '').trim().length > 0);
      const control = /^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName) && (el as HTMLInputElement).type !== 'hidden';
      if (!text && !control && !(paints(s) && /^(BUTTON|A)$/.test(el.tagName))) continue;
      let box: HTMLElement | null = el.parentElement;
      let scrolls = false;
      while (box && box !== root.parentElement && !isBox(box)) {
        const o = getComputedStyle(box).overflowX;
        if (o === 'auto' || o === 'scroll') scrolls = true;
        box = box.parentElement;
      }
      if (!box || scrolls) continue;
      const bs = getComputedStyle(box).overflowX;
      if (bs === 'auto' || bs === 'scroll') continue;
      const b = el.getBoundingClientRect();
      const c = box.getBoundingClientRect();
      if (b.right > c.right + 1 || b.left < c.left - 1) {
        escaped.push(`${label(el)} (${Math.round(b.left)}..${Math.round(b.right)} outside ${Math.round(c.left)}..${Math.round(c.right)})`);
      }
    }
    return { small, clipped, escaped };
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
