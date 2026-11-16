/**
 * Beloved's motifs from the Song of Solomon, drawn inline (no images to load):
 * a vine with leaves (2:15, 6:11), a lily among thorns (2:1-2) and a
 * pomegranate (4:3, 6:7). They use `currentColor`, so both themes tint them.
 */

export function Vine({ className = '' }: { className?: string }) {
  return (
    <svg viewBox="0 0 240 24" preserveAspectRatio="none" aria-hidden className={`h-5 w-full ${className}`} fill="none">
      <path d="M0 12 C 30 2, 50 22, 80 12 S 130 2, 160 12 S 210 22, 240 12" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" opacity="0.7" />
      {[24, 64, 104, 144, 184, 224].map((x, i) => (
        <path
          key={x}
          d={i % 2 ? `M${x} 12 q 6 -9 13 -7 q -3 8 -13 7z` : `M${x} 12 q 6 9 13 7 q -3 -8 -13 -7z`}
          fill="currentColor"
          opacity="0.55"
        />
      ))}
    </svg>
  );
}

export function Lily({ size = 20, className = '' }: { size?: number; className?: string }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden className={className} fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 21v-7" />
      <path d="M12 14c-3.5 0-6-2.5-6.5-6 2.5.3 4.5 1.5 6.5 4 2-2.5 4-3.7 6.5-4-.5 3.5-3 6-6.5 6z" />
      <path d="M12 12c-1-2.5-1-5.5 0-8.5 1 3 1 6 0 8.5z" />
      <path d="M12 19c-2 0-3.5-1-4.5-2.5M12 19c2 0 3.5-1 4.5-2.5" />
    </svg>
  );
}

export function Pomegranate({ size = 20, className = '' }: { size?: number; className?: string }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden className={className} fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M9 5.5 10 3l2 1.5L14 3l1 2.5" />
      <path d="M12 5.5c-4.4 0-8 3.4-8 7.8C4 17.6 7.6 21 12 21s8-3.4 8-7.7c0-4.4-3.6-7.8-8-7.8z" />
      <circle cx="10" cy="12" r="0.9" fill="currentColor" />
      <circle cx="13.5" cy="11" r="0.9" fill="currentColor" />
      <circle cx="12" cy="14.5" r="0.9" fill="currentColor" />
      <circle cx="15" cy="15" r="0.9" fill="currentColor" />
      <circle cx="9" cy="16" r="0.9" fill="currentColor" />
    </svg>
  );
}
