import type { CSSProperties } from 'react';

interface GameArtworkProps {
  slug: string;
  emoji?: string;
  category?: string;
  art?: { from?: string; to?: string } | null;
  className?: string;
  variant?: 'card' | 'banner' | 'thumb' | 'splash';
}

const PALETTES: Record<string, { from: string; to: string; accent: string }> = {
  'family-cloud-hop': { from: '#1e3a8a', to: '#f472b6', accent: '#fde047' },
  'family-petal-swap': { from: '#831843', to: '#ec4899', accent: '#fbcfe8' },
  'family-orbit-guard': { from: '#0f172a', to: '#312e81', accent: '#38bdf8' },
  'family-memory-meadow': { from: '#064e3b', to: '#10b981', accent: '#fef08a' },
  'family-garden-pipes': { from: '#0c4a6e', to: '#0284c7', accent: '#7dd3fc' },
  'family-block-bloom': { from: '#365314', to: '#65a30d', accent: '#bef264' },
  'family-checkers': { from: '#451a03', to: '#b45309', accent: '#fcd34d' },
  'family-good-word': { from: '#4c1d95', to: '#c026d3', accent: '#fde047' },
  'family-jubilee': { from: '#78350f', to: '#d97706', accent: '#fef3c7' },
  'family-telly-swing': { from: '#1e1b4b', to: '#7e22ce', accent: '#22d3ee' },
};

const CATEGORY_PALETTES: Record<string, { from: string; to: string; accent: string }> = {
  arcade: { from: '#1e1b4b', to: '#6d28d9', accent: '#a78bfa' },
  classic: { from: '#0f172a', to: '#0369a1', accent: '#38bdf8' },
  puzzle: { from: '#064e3b', to: '#0d9488', accent: '#5eead4' },
  action: { from: '#4c0519', to: '#be123c', accent: '#fda4af' },
  strategy: { from: '#3b0764', to: '#7e22ce', accent: '#e9d5ff' },
};

function resolvePalette(slug: string, category?: string, art?: { from?: string; to?: string } | null) {
  if (art?.from && art?.to) {
    return { from: art.from, to: art.to, accent: '#fde047' };
  }
  if (PALETTES[slug]) return PALETTES[slug];
  if (category && CATEGORY_PALETTES[category.toLowerCase()]) {
    return CATEGORY_PALETTES[category.toLowerCase()];
  }
  // Deterministic fallback from slug hash
  let hash = 0;
  for (let i = 0; i < slug.length; i++) hash = (hash * 31 + slug.charCodeAt(i)) | 0;
  const hues = [
    { from: '#1e1b4b', to: '#7c3aed', accent: '#38bdf8' },
    { from: '#0f172a', to: '#0284c7', accent: '#f472b6' },
    { from: '#31102f', to: '#db2777', accent: '#fde047' },
    { from: '#064e3b', to: '#059669', accent: '#a7f3d0' },
  ];
  return hues[Math.abs(hash) % hues.length];
}

function SceneSvg({ slug, category }: { slug: string; category?: string }) {
  if (slug.includes('cloud-hop')) {
    return (
      <svg viewBox="0 0 320 160" className="h-full w-full" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
        <circle cx="265" cy="36" r="22" fill="#fde047" fillOpacity="0.28" />
        <circle cx="265" cy="36" r="13" fill="#fef08a" fillOpacity="0.7" />
        <path d="M25 135 Q55 112 85 135 Z" fill="#ffffff" fillOpacity="0.2" />
        <g fill="#ffffff" fillOpacity="0.32">
          <rect x="28" y="102" width="76" height="22" rx="11" />
          <circle cx="48" cy="100" r="14" />
          <circle cx="68" cy="96" r="17" />
          <circle cx="88" cy="102" r="12" />
        </g>
        <g fill="#ffffff" fillOpacity="0.45">
          <rect x="195" y="74" width="90" height="24" rx="12" />
          <circle cx="218" cy="72" r="15" />
          <circle cx="242" cy="66" r="19" />
          <circle cx="264" cy="73" r="13" />
        </g>
        <path d="M120 130 Q150 55 215 48" stroke="#fde047" strokeWidth="2.5" strokeDasharray="5 5" fill="none" strokeOpacity="0.65" />
        <polygon points="160,22 164,31 174,32 166,38 169,48 160,42 151,48 154,38 146,32 156,31" fill="#fde047" fillOpacity="0.85" />
      </svg>
    );
  }

  if (slug.includes('petal-swap')) {
    return (
      <svg viewBox="0 0 320 160" className="h-full w-full" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
        <g stroke="#ffffff" strokeOpacity="0.12" strokeWidth="1">
          <rect x="36" y="24" width="52" height="52" rx="12" fill="#ffffff" fillOpacity="0.06" />
          <rect x="96" y="24" width="52" height="52" rx="12" fill="#ffffff" fillOpacity="0.1" />
          <rect x="36" y="84" width="52" height="52" rx="12" fill="#ffffff" fillOpacity="0.1" />
          <rect x="96" y="84" width="52" height="52" rx="12" fill="#ffffff" fillOpacity="0.06" />
        </g>
        <g transform="translate(245 80)" fill="#fbcfe8" fillOpacity="0.35">
          {[0, 60, 120, 180, 240, 300].map((deg) => (
            <ellipse key={deg} cx="0" cy="-26" rx="12" ry="22" transform={`rotate(${deg})`} />
          ))}
          <circle cx="0" cy="0" r="11" fill="#fde047" fillOpacity="0.7" />
        </g>
        <circle cx="62" cy="50" r="12" fill="#f472b6" fillOpacity="0.45" />
        <circle cx="122" cy="50" r="12" fill="#fde047" fillOpacity="0.45" />
        <circle cx="62" cy="110" r="12" fill="#38bdf8" fillOpacity="0.45" />
        <circle cx="122" cy="110" r="12" fill="#4ade80" fillOpacity="0.45" />
      </svg>
    );
  }

  if (slug.includes('orbit-guard')) {
    return (
      <svg viewBox="0 0 320 160" className="h-full w-full" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
        <circle cx="45" cy="30" r="1.5" fill="#fff" fillOpacity="0.7" />
        <circle cx="95" cy="128" r="2" fill="#38bdf8" fillOpacity="0.8" />
        <circle cx="275" cy="35" r="1.8" fill="#fff" fillOpacity="0.6" />
        <circle cx="290" cy="118" r="1.5" fill="#f472b6" fillOpacity="0.7" />
        <circle cx="160" cy="80" r="62" stroke="#38bdf8" strokeOpacity="0.2" strokeWidth="1.5" strokeDasharray="6 6" fill="none" />
        <circle cx="160" cy="80" r="42" stroke="#818cf8" strokeOpacity="0.28" strokeWidth="1.5" fill="none" />
        <path d="M 114 42 A 60 60 0 0 1 206 42" stroke="#38bdf8" strokeWidth="5" strokeLinecap="round" fill="none" strokeOpacity="0.8" />
        <circle cx="66" cy="44" r="7" fill="#fb7185" fillOpacity="0.8" />
        <path d="M 42 28 L 62 41" stroke="#fb7185" strokeWidth="3" strokeLinecap="round" strokeOpacity="0.6" />
      </svg>
    );
  }

  if (slug.includes('memory-meadow')) {
    return (
      <svg viewBox="0 0 320 160" className="h-full w-full" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
        <path d="M0 125 Q85 95 170 125 T320 115 L320 160 L0 160 Z" fill="#065f46" fillOpacity="0.45" />
        <path d="M0 138 Q120 112 240 138 T320 132 L320 160 L0 160 Z" fill="#047857" fillOpacity="0.55" />
        <g transform="rotate(-10 68 68)">
          <rect x="44" y="36" width="46" height="62" rx="9" fill="#ffffff" fillOpacity="0.22" stroke="#ffffff" strokeOpacity="0.4" strokeWidth="1.5" />
          <circle cx="67" cy="67" r="10" fill="#fde047" fillOpacity="0.6" />
        </g>
        <g transform="rotate(8 252 68)">
          <rect x="228" y="36" width="46" height="62" rx="9" fill="#ffffff" fillOpacity="0.22" stroke="#ffffff" strokeOpacity="0.4" strokeWidth="1.5" />
          <circle cx="251" cy="67" r="10" fill="#fde047" fillOpacity="0.6" />
        </g>
      </svg>
    );
  }

  if (slug.includes('garden-pipes')) {
    return (
      <svg viewBox="0 0 320 160" className="h-full w-full" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
        <g stroke="#7dd3fc" strokeWidth="10" strokeLinecap="round" strokeLinejoin="round" fill="none" strokeOpacity="0.35">
          <path d="M 24 45 H 88 V 112 H 150" />
          <path d="M 296 115 H 232 V 50 H 175" />
        </g>
        <g stroke="#38bdf8" strokeWidth="4" strokeDasharray="6 6" fill="none" strokeOpacity="0.75">
          <path d="M 24 45 H 88 V 112 H 150" />
          <path d="M 296 115 H 232 V 50 H 175" />
        </g>
        <circle cx="88" cy="45" r="8" fill="#bae6fd" fillOpacity="0.5" />
        <circle cx="88" cy="112" r="8" fill="#bae6fd" fillOpacity="0.5" />
        <circle cx="232" cy="50" r="8" fill="#bae6fd" fillOpacity="0.5" />
        <circle cx="232" cy="115" r="8" fill="#bae6fd" fillOpacity="0.5" />
      </svg>
    );
  }

  if (slug.includes('block-bloom')) {
    return (
      <svg viewBox="0 0 320 160" className="h-full w-full" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
        <g fill="#bef264" fillOpacity="0.25" stroke="#ecfccb" strokeOpacity="0.35" strokeWidth="1.5">
          <rect x="34" y="34" width="28" height="28" rx="6" />
          <rect x="66" y="34" width="28" height="28" rx="6" />
          <rect x="34" y="66" width="28" height="28" rx="6" />
          <rect x="226" y="66" width="28" height="28" rx="6" />
          <rect x="258" y="66" width="28" height="28" rx="6" />
          <rect x="258" y="98" width="28" height="28" rx="6" />
        </g>
      </svg>
    );
  }

  if (slug.includes('checkers')) {
    return (
      <svg viewBox="0 0 320 160" className="h-full w-full" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
        <g fill="#ffffff" fillOpacity="0.1">
          {[0, 1, 2, 3].map((r) =>
            [0, 1, 2, 3, 4, 5, 6, 7].map((c) =>
              (r + c) % 2 === 0 ? <rect key={`${r}-${c}`} x={c * 40} y={r * 40} width="40" height="40" /> : null,
            ),
          )}
        </g>
        <circle cx="60" cy="60" r="14" fill="#ef4444" fillOpacity="0.7" stroke="#fca5a5" strokeWidth="2" />
        <circle cx="260" cy="100" r="14" fill="#f8fafc" fillOpacity="0.75" stroke="#cbd5e1" strokeWidth="2" />
      </svg>
    );
  }

  if (slug.includes('jubilee')) {
    return (
      <svg viewBox="0 0 320 160" className="h-full w-full" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
        {/* Monopoly-style Jubilee board perspective corner & property deeds */}
        <g transform="translate(160 88) rotate(-14)">
          <rect x="-135" y="-58" width="270" height="118" rx="10" fill="#fef3c7" fillOpacity="0.16" stroke="#fde68a" strokeOpacity="0.45" strokeWidth="2" />
          {/* Board corner tile (JUBILEE +200) */}
          <rect x="-131" y="-54" width="54" height="54" rx="6" fill="#fbbf24" fillOpacity="0.35" stroke="#fef08a" strokeOpacity="0.6" strokeWidth="1.5" />
          <text x="-104" y="-22" textAnchor="middle" fill="#fef3c7" fontSize="9" fontWeight="900" letterSpacing="0.5">+200</text>
          {/* Property tiles with color bands & miniature houses */}
          <g transform="translate(-73 -54)">
            <rect width="38" height="54" rx="4" fill="#fffbeb" fillOpacity="0.22" stroke="#fde68a" strokeOpacity="0.4" />
            <rect width="38" height="12" rx="3" fill="#ef4444" fillOpacity="0.85" />
            <rect x="6" y="16" width="7" height="6" rx="1" fill="#22c55e" />
            <rect x="16" y="16" width="7" height="6" rx="1" fill="#22c55e" />
          </g>
          <g transform="translate(-31 -54)">
            <rect width="38" height="54" rx="4" fill="#fffbeb" fillOpacity="0.22" stroke="#fde68a" strokeOpacity="0.4" />
            <rect width="38" height="12" rx="3" fill="#eab308" fillOpacity="0.85" />
            <rect x="13" y="15" width="12" height="8" rx="1.5" fill="#dc2626" stroke="#fef08a" strokeWidth="0.8" />
          </g>
          <g transform="translate(11 -54)">
            <rect width="38" height="54" rx="4" fill="#fffbeb" fillOpacity="0.22" stroke="#fde68a" strokeOpacity="0.4" />
            <rect width="38" height="12" rx="3" fill="#22c55e" fillOpacity="0.85" />
          </g>
          <g transform="translate(53 -54)">
            <rect width="38" height="54" rx="4" fill="#fffbeb" fillOpacity="0.22" stroke="#fde68a" strokeOpacity="0.4" />
            <rect width="38" height="12" rx="3" fill="#3b82f6" fillOpacity="0.85" />
          </g>
        </g>
        {/* Tumbling 3D dice */}
        <g transform="translate(54 44) rotate(-18)">
          <rect x="-16" y="-16" width="32" height="32" rx="7" fill="#fffbeb" fillOpacity="0.9" stroke="#f59e0b" strokeWidth="2" />
          <circle cx="-7" cy="-7" r="2.6" fill="#451a03" />
          <circle cx="0" cy="0" r="2.6" fill="#451a03" />
          <circle cx="7" cy="7" r="2.6" fill="#451a03" />
        </g>
        <g transform="translate(262 108) rotate(16)">
          <rect x="-15" y="-15" width="30" height="30" rx="7" fill="#fffbeb" fillOpacity="0.88" stroke="#f59e0b" strokeWidth="2" />
          <circle cx="-6" cy="-6" r="2.4" fill="#dc2626" />
          <circle cx="6" cy="-6" r="2.4" fill="#451a03" />
          <circle cx="-6" cy="6" r="2.4" fill="#451a03" />
          <circle cx="6" cy="6" r="2.4" fill="#dc2626" />
        </g>
      </svg>
    );
  }

  if (slug.includes('good-word')) {
    return (
      <svg viewBox="0 0 320 160" className="h-full w-full" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
        <polygon points="40,0 110,160 0,160" fill="#fde047" fillOpacity="0.12" />
        <polygon points="280,0 320,160 210,160" fill="#fde047" fillOpacity="0.12" />
        <circle cx="65" cy="42" r="4" fill="#fde047" fillOpacity="0.7" />
        <circle cx="255" cy="48" r="5" fill="#f472b6" fillOpacity="0.7" />
        <circle cx="85" cy="115" r="3.5" fill="#38bdf8" fillOpacity="0.7" />
        <circle cx="242" cy="112" r="4" fill="#4ade80" fillOpacity="0.7" />
      </svg>
    );
  }

  if (slug.includes('telly-swing')) {
    return (
      <svg viewBox="0 0 320 160" className="h-full w-full" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
        <path d="M 80 0 L 145 78" stroke="#22d3ee" strokeWidth="2.5" strokeOpacity="0.6" />
        <path d="M 45 95 Q 145 135 265 55" stroke="#f472b6" strokeWidth="2.5" strokeDasharray="5 5" fill="none" strokeOpacity="0.65" />
        <circle cx="80" cy="8" r="5" fill="#22d3ee" fillOpacity="0.85" />
        <rect x="228" y="42" width="54" height="42" rx="8" fill="#0f172a" fillOpacity="0.5" stroke="#22d3ee" strokeOpacity="0.5" strokeWidth="2" />
      </svg>
    );
  }

  // Classic / Benchmark Arcade vector synthwave grid + invader/arcade geometry
  const isClassic = category === 'classic' || slug.includes('breakout') || slug.includes('pong');
  return (
    <svg viewBox="0 0 320 160" className="h-full w-full" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
      {/* Perspective arcade floor grid */}
      <g stroke="#ffffff" strokeOpacity="0.14" strokeWidth="1">
        <line x1="0" y1="115" x2="320" y2="115" />
        <line x1="0" y1="132" x2="320" y2="132" />
        <line x1="0" y1="150" x2="320" y2="150" />
        <line x1="160" y1="95" x2="20" y2="160" />
        <line x1="160" y1="95" x2="90" y2="160" />
        <line x1="160" y1="95" x2="160" y2="160" />
        <line x1="160" y1="95" x2="230" y2="160" />
        <line x1="160" y1="95" x2="300" y2="160" />
      </g>
      {isClassic ? (
        <g fill="#38bdf8" fillOpacity="0.45">
          <rect x="34" y="28" width="36" height="12" rx="3" />
          <rect x="76" y="28" width="36" height="12" rx="3" fill="#f472b6" />
          <rect x="208" y="28" width="36" height="12" rx="3" fill="#fde047" />
          <rect x="250" y="28" width="36" height="12" rx="3" />
          <circle cx="220" cy="78" r="6" fill="#ffffff" fillOpacity="0.8" />
        </g>
      ) : (
        <g fill="#a78bfa" fillOpacity="0.35">
          <circle cx="52" cy="42" r="2.5" fill="#fff" fillOpacity="0.7" />
          <circle cx="268" cy="36" r="2" fill="#fde047" fillOpacity="0.8" />
          <circle cx="244" cy="72" r="3" fill="#38bdf8" fillOpacity="0.7" />
          <rect x="42" y="64" width="36" height="22" rx="6" stroke="#fff" strokeOpacity="0.3" fill="none" />
          <rect x="242" y="56" width="36" height="22" rx="6" stroke="#fff" strokeOpacity="0.3" fill="none" />
        </g>
      )}
    </svg>
  );
}

export default function GameArtwork({
  slug,
  emoji,
  category,
  art,
  className = '',
  variant = 'card',
}: GameArtworkProps) {
  const palette = resolvePalette(slug, category, art);
  const style: CSSProperties = {
    backgroundImage: `radial-gradient(circle at 50% 25%, rgba(255,255,255,0.22), transparent 65%), linear-gradient(135deg, ${palette.from}, ${palette.to})`,
  };

  if (variant === 'thumb') {
    return (
      <span
        className={`relative flex shrink-0 items-center justify-center overflow-hidden rounded-xl border border-white/15 shadow-inner ${className}`}
        style={style}
        aria-hidden="true"
      >
        <span className="relative z-10 drop-shadow">{emoji || '🕹️'}</span>
      </span>
    );
  }

  return (
    <div
      className={`relative overflow-hidden ${className}`}
      style={style}
      aria-hidden="true"
    >
      <div className="pointer-events-none absolute inset-0">
        <SceneSvg slug={slug} category={category} />
      </div>
      <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-slate-950/55 via-transparent to-black/15" />
      <div className="relative z-10 flex h-full w-full items-center justify-center">
        <span
          className={`flex items-center justify-center rounded-3xl border border-white/25 bg-white/10 shadow-xl backdrop-blur-md transition-transform duration-300 group-hover:scale-105 ${
            variant === 'splash'
              ? 'h-28 w-28 text-7xl'
              : variant === 'banner'
                ? 'h-12 w-12 text-3xl rounded-2xl'
                : 'h-20 w-20 text-5xl'
          }`}
        >
          <span className="drop-shadow-lg">{emoji || '🕹️'}</span>
        </span>
      </div>
    </div>
  );
}
