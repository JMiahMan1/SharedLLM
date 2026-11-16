import type { ReactNode } from 'react';

export function Card({ children, onClick, testId }: { children: ReactNode; onClick?: () => void; testId?: string }) {
  const className = 'block w-full min-w-0 rounded-2xl border border-rose-300/10 bg-white/[0.04] p-3.5 text-left backdrop-blur';
  return onClick ? (
    <button type="button" onClick={onClick} className={`${className} min-h-11 hover:bg-white/[0.07]`} data-testid={testId}>
      {children}
    </button>
  ) : (
    <div className={className} data-testid={testId}>
      {children}
    </div>
  );
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-semibold text-slate-300">{label}</span>
      {children}
    </label>
  );
}
