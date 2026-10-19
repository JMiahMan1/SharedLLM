import { avatarTint, initials } from '../components/chat/chatModel';
import { presetFor } from './avatarPresets';

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

const escapeHtml = (value: string): string =>
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
    inner =
      `<span aria-hidden="true" style="font-size:${Math.round(size * 0.48)}px;line-height:1">${preset.emoji}</span>` +
      `<span aria-hidden="true" style="position:absolute;right:1px;bottom:0;font-size:${Math.round(size * 0.28)}px;line-height:1">${preset.badge}</span>`;
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
