import { Activity, Gamepad2, Palette, Sparkles, Info } from 'lucide-react';
import { decodeEnvelope, type ChatEnvelope } from '../../lib/chatEnvelope';

const KIND_STYLE: Record<ChatEnvelope['kind'], { icon: typeof Activity; ring: string; tint: string }> = {
  text: { icon: Info, ring: 'border-white/10', tint: 'text-slate-200' },
  activity: { icon: Activity, ring: 'border-emerald-400/30', tint: 'text-emerald-200' },
  game: { icon: Gamepad2, ring: 'border-purple-400/30', tint: 'text-purple-200' },
  creation: { icon: Palette, ring: 'border-pink-400/30', tint: 'text-pink-200' },
  system: { icon: Info, ring: 'border-white/10', tint: 'text-slate-300' },
};

/**
 * Renders one chat message body: plain text, or a card when the message carries
 * an envelope. A malformed envelope falls back to the readable text, so a bad
 * card can never blank out a conversation.
 */
export default function EnvelopeBody({ raw, fallback }: { raw?: string | null; fallback?: string | null }) {
  const { text, envelope } = decodeEnvelope(raw);

  if (!envelope) {
    return <>{text || fallback || 'Empty message'}</>;
  }

  const { icon: Icon, ring, tint } = KIND_STYLE[envelope.kind] ?? KIND_STYLE.text;

  return (
    <span className="block space-y-2" data-testid={`envelope-${envelope.kind}`}>
      {text && <span className="block whitespace-pre-wrap">{text}</span>}
      <span className={`block rounded-xl border ${ring} bg-white/5 p-3 space-y-2`}>
        <span className="flex items-center gap-2">
          <Icon size={15} className={tint} />
          <span className={`font-semibold ${tint}`}>{envelope.title}</span>
          {typeof envelope.stars === 'number' && envelope.stars > 0 && (
            <span className="ml-auto inline-flex items-center gap-1 rounded-full border border-amber-400/40 bg-amber-400/10 px-2 py-0.5 text-[10px] font-bold text-amber-200">
              <Sparkles size={11} /> {envelope.stars}
            </span>
          )}
        </span>

        {envelope.detail && <span className="block text-xs text-slate-300">{envelope.detail}</span>}

        {envelope.stats && envelope.stats.length > 0 && (
          <span className="flex flex-wrap gap-2">
            {envelope.stats.map((stat) => (
              <span
                key={`${stat.label}-${stat.value}`}
                className="rounded-lg border border-white/10 bg-white/5 px-2 py-1 text-[11px] text-slate-300"
              >
                <span className="block text-[9px] uppercase tracking-wider text-slate-500">{stat.label}</span>
                {stat.value}
              </span>
            ))}
          </span>
        )}

        {envelope.action && (
          <a
            href={envelope.action.href}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex min-h-9 items-center rounded-lg border border-white/15 bg-white/5 px-2.5 text-[11px] text-slate-200 hover:bg-white/10"
          >
            {envelope.action.label}
          </a>
        )}
      </span>
    </span>
  );
}
