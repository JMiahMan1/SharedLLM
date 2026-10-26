import { Link } from 'react-router-dom';
import { ChevronLeft } from 'lucide-react';
import { muted } from './ui';

/**
 * A way back to the library tab a detail page came from.
 *
 * The album, artist, playlist, book and podcast pages had no way out of
 * themselves: the only exit was the browser's own back gesture, and on a phone
 * that gesture is the app's Back button, which used to do nothing at all.
 */
export default function BackToLibrary({ to, label }: { to: string; label: string }) {
  return (
    <Link
      to={to}
      className={`inline-flex min-h-11 items-center gap-1 rounded-xl px-2 text-sm ${muted} hover:bg-[var(--site-card-bg-hover)]`}
    >
      <ChevronLeft size={16} aria-hidden />
      {label}
    </Link>
  );
}
