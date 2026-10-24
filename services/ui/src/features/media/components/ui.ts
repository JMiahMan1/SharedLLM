/**
 * Theme-token classes for the media screens (§6.5). Everything here follows
 * the active theme pack (`--site-*`) and, inside the player surfaces, the
 * artwork accent (`--media-accent`); no fixed hues.
 */

export const text = 'text-[var(--site-text)]';
export const muted = 'text-[var(--site-text-muted)]';
export const accentText = 'text-[var(--media-accent,var(--site-accent-text))]';

/** A 44 px icon button on a quiet background. */
export const iconButton =
  'flex min-h-11 min-w-11 items-center justify-center rounded-xl text-[var(--site-text-muted)] transition-colors hover:bg-[var(--site-card-bg-hover)] hover:text-[var(--site-text)] disabled:opacity-50';

/** The primary action (Play). */
export const primaryButton =
  'inline-flex min-h-11 items-center justify-center gap-2 rounded-xl bg-[var(--site-accent)] px-5 text-sm font-semibold text-[var(--site-on-accent)] transition-opacity hover:opacity-90 disabled:opacity-50';

/** A secondary action (Shuffle, Retry). */
export const secondaryButton =
  'inline-flex min-h-11 items-center justify-center gap-2 rounded-xl border border-[var(--color-border-mid)] bg-[var(--site-card-bg)] px-4 text-sm font-medium text-[var(--site-text)] transition-colors hover:bg-[var(--site-card-bg-hover)] disabled:opacity-50';

/** A pill that is on or off (filters, tabs). */
export const chip = (on: boolean) =>
  `inline-flex min-h-11 shrink-0 items-center gap-1.5 rounded-full border px-4 text-sm transition-colors ${
    on
      ? 'border-[var(--site-accent-border)] bg-[var(--site-accent-soft)] text-[var(--site-accent-text)]'
      : 'border-[var(--color-border-soft)] bg-[var(--site-card-bg)] text-[var(--site-text-muted)] hover:text-[var(--site-text)]'
  }`;

/** Section titles: sentence case, 18-20 px (UX-25). */
export const shelfTitle = 'text-lg font-semibold text-[var(--site-text)] sm:text-xl';

/** A list row: full width, at least 44 px tall. */
export const row =
  'flex w-full min-h-11 items-center gap-3 rounded-xl p-2 text-left transition-colors hover:bg-[var(--site-card-bg-hover)] focus-visible:outline-2 focus-visible:outline-[var(--site-accent-focus)]';
