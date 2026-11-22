import { ShieldOff } from 'lucide-react';
import { Link } from 'react-router-dom';

/**
 * What a person sees on a page they may not open: an admin page for someone
 * who is not an admin, reached from a notice, a bookmark or a typed address.
 * It used to be a silent jump to the dashboard, which looked like the notice
 * was broken. Nothing of the page itself is loaded.
 */
export default function AccessRefused({ what = 'This page is for admins.' }: { what?: string }) {
  return (
    <div className="flex min-h-[50vh] items-center justify-center p-4" data-testid="access-refused" role="alert">
      <div className="glass-panel w-full max-w-sm space-y-3 rounded-2xl border border-white/10 p-5 text-center">
        <ShieldOff className="mx-auto text-amber-300" size={28} aria-hidden />
        <h1 className="text-lg font-semibold text-white">You can’t open this here</h1>
        <p className="text-sm text-slate-300">{what} Ask an admin if you need something from it.</p>
        <Link
          to="/"
          replace
          className="inline-flex min-h-11 items-center justify-center rounded-xl bg-sky-500/20 px-4 text-sm font-semibold text-sky-100 hover:bg-sky-500/30"
        >
          Go to the dashboard
        </Link>
      </div>
    </div>
  );
}
