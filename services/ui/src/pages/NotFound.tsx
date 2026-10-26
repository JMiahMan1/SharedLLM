import { Link } from 'react-router-dom';

/**
 * Shown for any URL the router does not know.
 *
 * Without this, an old bookmark, a mistyped address or a stale push deep link
 * rendered the app shell with an empty page and no explanation. Saying so, and
 * offering the way back, is the difference between "this is broken" and "that
 * link is old".
 */
export default function NotFound() {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-3 p-6 text-center">
      <p className="text-lg font-semibold text-white">That page does not exist</p>
      <p className="max-w-sm text-sm text-slate-400">
        The link may be old, or it may have a typo in it.
      </p>
      <Link
        to="/"
        className="mt-2 rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-sky-500 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-300"
      >
        Go home
      </Link>
    </div>
  );
}
