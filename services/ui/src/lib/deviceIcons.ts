import {
  Activity, Blinds, Bot, Box, Cast, Cctv, Fan, Lightbulb, Lock, Mic, Plug, Smartphone, Speaker, Thermometer, Tv,
  type LucideIcon,
} from 'lucide-react';
import type { HubEntity } from './dashboards';
import { lightColour, rgbCss, type TileKind } from './deviceTiles';

export const KIND_ICON: Record<TileKind, LucideIcon> = {
  light: Lightbulb, switch: Plug, fan: Fan, tv: Tv, speaker: Speaker, media: Cast, climate: Thermometer, cover: Blinds,
  lock: Lock, vacuum: Bot, camera: Cctv, sensor: Activity, voice: Mic, phone: Smartphone, other: Box,
};

/** The accent a device is drawn with when it is on: a lit light's own colour, else the kind's. */
export function accentFor(kind: TileKind, primary: HubEntity | null): string {
  const lit = lightColour(primary);
  if (lit) return rgbCss(lit);
  if (kind === 'light') return 'rgb(251, 191, 36)';
  if (kind === 'tv' || kind === 'speaker' || kind === 'media' || kind === 'voice') return 'rgb(56, 189, 248)';
  if (kind === 'climate') return 'rgb(251, 146, 60)';
  return 'rgb(52, 211, 153)';
}

/** Is the device doing its thing (a light on, a TV playing): what the tile lights up for. */
export function isActiveState(primary: HubEntity | null): boolean {
  if (!primary) return false;
  if (primary.domain === 'media_player') return primary.state === 'playing' || primary.state === 'on' || primary.state === 'paused';
  if (primary.domain === 'climate') return primary.state !== 'off';
  if (primary.domain === 'lock') return primary.state === 'unlocked';
  if (primary.domain === 'cover') return primary.state === 'open' || primary.state === 'opening';
  if (primary.domain === 'vacuum') return primary.state === 'cleaning';
  return primary.state === 'on';
}

