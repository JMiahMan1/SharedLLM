import { avatarTint, initials } from '../components/chat/chatModel';
import { presetFor, presetSvgHtml } from './avatarPresets';

export interface AvatarMarkerOptions {
  /** Person's display name; also the accessible label. */
  label: string;
  /** Their `avatar` value: "preset:<id>", "upload:<version>" or empty. */
  avatar?: string | null;
  /** Object URL for an uploaded photo, when it has finished loading. */
  imageUrl?: string | null;
  /** Freshness colour, drawn as the ring around the picture. */
  ringColor: string;
  size?: number;
  opacity?: number;
}

export const escapeHtml = (value: string): string =>
  value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');

/**
 * The HTML inside a Leaflet map marker: the person's uploaded photo, their
 * built-in character, or their initials, ringed in the colour that already
 * told the map whether their fix is live, recent or stale.
 */
export function avatarMarkerHtml({
  label,
  avatar,
  imageUrl,
  ringColor,
  size = 34,
  opacity = 1,
}: AvatarMarkerOptions): string {
  const preset = presetFor(avatar);
  const safeLabel = escapeHtml(label);
  const safeRing = escapeHtml(ringColor);
  const box = `width:${size}px;height:${size}px;border:3px solid ${safeRing};border-radius:9999px;box-sizing:border-box;`;
  const shell = `display:flex;align-items:center;justify-content:center;overflow:hidden;position:relative;box-shadow:0 2px 8px rgba(0,0,0,.45);opacity:${opacity};`;

  let inner: string;
  let kind: 'photo' | 'preset' | 'initials';
  let background: string;
  if (imageUrl) {
    kind = 'photo';
    background = '#0f172a';
    inner = `<img src="${escapeHtml(imageUrl)}" alt="" draggable="false" style="width:100%;height:100%;object-fit:cover;display:block" />`;
  } else if (preset) {
    kind = 'preset';
    background = `linear-gradient(135deg, ${preset.from}, ${preset.to})`;
    inner = presetSvgHtml(preset);
  } else {
    kind = 'initials';
    background = avatarTint(label);
    inner = `<span aria-hidden="true" style="color:#fff;font-weight:700;font-size:${Math.round(size * 0.36)}px;line-height:1">${escapeHtml(initials(label))}</span>`;
  }

  return (
    `<span role="img" aria-label="${safeLabel}" title="${safeLabel}" data-testid="map-avatar-marker" data-avatar-kind="${kind}" ` +
    `style="${shell}${box}background:${background}">${inner}</span>`
  );
}

export interface AvatarClusterMarkerOptions {
  people: AvatarMarkerOptions[];
  overlap?: number;
}

export function avatarClusterMarkerLayout(
  people: AvatarMarkerOptions[],
  overlap = 12,
): { width: number; height: number } {
  if (people.length === 0) return { width: 0, height: 0 };
  const sizes = people.map((person) => person.size ?? 34);
  return {
    width: sizes.reduce((total, size, index) => total + (index === 0 ? size : size - overlap), 0),
    height: Math.max(...sizes),
  };
}

export function avatarClusterMarkerHtml({
  people,
  overlap = 12,
}: AvatarClusterMarkerOptions): string {
  if (people.length === 0) return '';
  const label = people.map((person) => person.label).join(', ');
  const { width, height } = avatarClusterMarkerLayout(people, overlap);
  const avatars = people
    .map((person, index) => {
      const size = person.size ?? 34;
      return `<span style="display:inline-flex;flex:0 0 auto;position:relative;z-index:${people.length - index};margin-left:${index === 0 ? 0 : -overlap}px">${avatarMarkerHtml({ ...person, size })}</span>`;
    })
    .join('');
  const safeLabel = escapeHtml(label);
  return `<span role="group" aria-label="${safeLabel}" title="${safeLabel}" data-testid="map-avatar-cluster" data-avatar-count="${people.length}" style="display:inline-flex;align-items:center;height:${height}px;width:${width}px">${avatars}</span>`;
}

