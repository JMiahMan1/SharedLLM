import type { LucideIcon } from 'lucide-react';

/** One hub registry panel on the Devices page: a titled card, like Phones and Integrations. */
export default function SetupSection({ title, icon: Icon, testId, aside, children }: {
  title: string; icon: LucideIcon; testId: string; aside?: React.ReactNode; children: React.ReactNode;
}) {
  return (
    <section className="glass-panel overflow-hidden rounded-2xl border border-white/10" data-testid={testId}>
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-white/5 bg-sky-500/5 px-4 py-3">
        <h2 className="flex min-w-0 items-center gap-2 text-[10px] font-black uppercase tracking-widest text-sky-400">
          <Icon size={14} /> {title}
        </h2>
        {aside}
      </header>
      {children}
    </section>
  );
}
