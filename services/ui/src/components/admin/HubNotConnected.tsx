import { Link } from 'react-router-dom';
import { House } from 'lucide-react';

/**
 * Stands in for an admin panel that works on the home hub's devices while no
 * hub is connected: one line saying so and where to connect one, instead of
 * a panel whose every call would fail.
 */
export default function HubNotConnected({ title }: { title: string }) {
  return (
    <section className="glass-panel p-4 sm:p-6 min-w-0" data-testid="hub-not-connected">
      <h3 className="flex items-center gap-3 text-xl font-bold text-white">
        <House size={20} className="text-slate-400" aria-hidden />
        {title}
      </h3>
      <p className="mt-2 text-sm text-slate-400">
        Not connected: this needs a home hub (HomeHub or Home Assistant).
      </p>
      {/* On its own line, so it is a full-size tap target on a phone. */}
      <Link to="/identity" className="mt-1 inline-flex min-h-11 items-center text-sm text-sky-300 underline underline-offset-2">
        Connect one in Accounts &amp; Integrations
      </Link>
    </section>
  );
}
