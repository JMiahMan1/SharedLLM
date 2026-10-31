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
  /** 'tile' draws a rounded square with a thinner ring (inside a place's pill). */
  shape?: 'round' | 'tile';
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
  shape = 'round',
}: AvatarMarkerOptions): string {
  const preset = presetFor(avatar);
  const safeLabel = escapeHtml(label);
  const safeRing = escapeHtml(ringColor);
  const ring = shape === 'tile' ? 2 : 3;
  const radius = shape === 'tile' ? '8px' : '9999px';
  const box = `width:${size}px;height:${size}px;border:${ring}px solid ${safeRing};border-radius:${radius};box-sizing:border-box;`;
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
  // A count badge, because a few overlapping avatars read as separate pins on a
  // map at a glance - the number makes "these are together" unmistakable.
  const badge = `<span style="position:absolute;right:-6px;bottom:-6px;z-index:${people.length + 1};min-width:19px;height:19px;padding:0 5px;border-radius:9999px;background:#111827;border:1px solid rgba(255,255,255,.4);color:#fff;font:700 11px/17px ui-sans-serif,system-ui,-apple-system,sans-serif;text-align:center;box-sizing:border-box">${people.length}</span>`;
  return `<span role="group" aria-label="${safeLabel}" title="${safeLabel}" data-testid="map-avatar-cluster" data-avatar-count="${people.length}" style="display:inline-flex;align-items:center;position:relative;height:${height}px;width:${width}px">${avatars}${badge}</span>`;
}


// --- Saved places: a round pin, with everyone there in a pill above it ------

export type PlaceKind = 'home' | 'school' | 'work' | 'church' | 'place';

/** Line icons (24-unit viewBox) drawn white inside the place pin. */
const PLACE_ICON_PATHS: Record<PlaceKind, string> = {
  home: '<path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z"/>',
  school: '<path d="M22 10 12 5 2 10l10 5 10-5z"/><path d="M6 12v5c3 3 9 3 12 0v-5"/>',
  work: '<rect x="3" y="7" width="18" height="13" rx="2"/><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
  church: '<path d="M12 2v6M9 5h6"/><path d="M5 22V12l7-4 7 4v10z"/><path d="M10 22v-4h4v4"/>',
  place: '<path d="M12 21s-7-6.2-7-11a7 7 0 0 1 14 0c0 4.8-7 11-7 11z"/><circle cx="12" cy="10" r="2.5"/>',
};

/**
 * What a place is, for its icon: the Home Assistant zone icon when it has one
 * (``mdi:school``), else its id or name ("zone.home", "Lincoln Elementary").
 */
export function placeKind(place: { id?: string; name: string; icon?: string | null }): PlaceKind {
  const text = `${place.icon ?? ''} ${place.id ?? ''} ${place.name}`.toLowerCase();
  if (/\bhome\b|house/.test(text)) return 'home';
  if (/school|academy|college|university|elementary|preschool|campus/.test(text)) return 'school';
  if (/work|office|briefcase/.test(text)) return 'work';
  if (/church|chapel|cross/.test(text)) return 'church';
  return 'place';
}

export const PLACE_PIN_SIZE = 30;
export const PLACE_PIN_COLOR = '#0891b2';

/** The round pin a saved place is drawn as. */
export function placePinHtml(place: { name: string; kind: PlaceKind }, size = PLACE_PIN_SIZE): string {
  const label = escapeHtml(place.name);
  const icon = Math.round(size * 0.55);
  return (
    `<span role="img" aria-label="${label}" title="${label}" data-testid="map-place-pin" data-place-kind="${place.kind}" ` +
    `style="display:flex;align-items:center;justify-content:center;width:${size}px;height:${size}px;box-sizing:border-box;` +
    `border-radius:9999px;background:${PLACE_PIN_COLOR};border:2px solid rgba(255,255,255,.9);box-shadow:0 2px 8px rgba(0,0,0,.45)">` +
    `<svg width="${icon}" height="${icon}" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">` +
    `${PLACE_ICON_PATHS[place.kind]}</svg></span>`
  );
}

/** Avatars shown in a place's pill before the rest fold into "+N". */
export const PLACE_GROUP_MAX = 5;
const TILE = 30;
const PILL_PAD = 4;
const PILL_GAP = 4;

export function placeGroupLayout(count: number): { width: number; height: number } {
  if (count <= 0) return { width: 0, height: 0 };
  const tiles = Math.min(count, PLACE_GROUP_MAX + (count > PLACE_GROUP_MAX ? 1 : 0));
  return { width: PILL_PAD * 2 + tiles * TILE + (tiles - 1) * PILL_GAP, height: PILL_PAD * 2 + TILE };
}

/**
 * Everyone at a saved place, side by side in one dark pill that sits above
 * the place's pin: each person stays recognisable, and the pin says where.
 */
export function placeGroupHtml({ people, place }: { people: AvatarMarkerOptions[]; place: string }): string {
  if (people.length === 0) return '';
  const { width, height } = placeGroupLayout(people.length);
  const shown = people.length > PLACE_GROUP_MAX ? people.slice(0, PLACE_GROUP_MAX) : people;
  const tiles = shown
    .map((person) => avatarMarkerHtml({ ...person, size: TILE, shape: 'tile' }))
    .join('');
  const more =
    people.length > shown.length
      ? `<span style="display:flex;align-items:center;justify-content:center;width:${TILE}px;height:${TILE}px;border-radius:8px;background:rgba(255,255,255,.12);color:#fff;font:700 12px/1 ui-sans-serif,system-ui,-apple-system,sans-serif">+${people.length - shown.length}</span>`
      : '';
  const label = escapeHtml(`${people.map((p) => p.label).join(', ')} at ${place}`);
  return (
    `<span role="group" aria-label="${label}" title="${label}" data-testid="map-place-group" data-avatar-count="${people.length}" ` +
    `style="display:flex;align-items:center;gap:${PILL_GAP}px;box-sizing:border-box;width:${width}px;height:${height}px;padding:${PILL_PAD}px;` +
    `border-radius:12px;background:rgba(17,24,39,.92);border:1px solid rgba(255,255,255,.14);box-shadow:0 4px 14px rgba(0,0,0,.45)">` +
    `${tiles}${more}</span>`
  );
}
