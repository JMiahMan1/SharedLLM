import type { CSSProperties } from 'react';
import { KIND_ICON } from '../../lib/deviceIcons';
import type { TileKind } from '../../lib/deviceTiles';

/** The round icon a tile and the detail's header share. */
export default function DeviceGlyph({ kind, accent, active, size = 'md' }: {
  kind: TileKind; accent: string; active: boolean; size?: 'md' | 'lg';
}) {
  const Icon = KIND_ICON[kind];
  const box = size === 'lg' ? 'h-12 w-12' : 'h-10 w-10';
  const style: CSSProperties | undefined = active
    ? { backgroundColor: accent.replace('rgb(', 'rgba(').replace(')', ', 0.22)'), color: accent } : undefined;
  return (
    <span className={`flex ${box} shrink-0 items-center justify-center rounded-full ${active ? '' : 'bg-white/[0.06] text-slate-400'}`}
      style={style} aria-hidden>
      <Icon size={size === 'lg' ? 24 : 20} />
    </span>
  );
}

