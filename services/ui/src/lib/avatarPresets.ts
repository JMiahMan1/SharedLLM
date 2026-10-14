/**
 * Built-in profile pictures: Bible stories at their silliest.
 *
 * Each is a big emoji with a little one for the punchline, on its own colour,
 * so they render crisply at any size on every device with no image files to
 * ship. Identity stores only the id ("preset:<id>"), so a picture can be
 * redrawn or renamed here without touching anyone's account.
 */
export interface AvatarPreset {
  id: string;
  name: string;
  /** The main figure. */
  emoji: string;
  /** The small corner joke. */
  badge: string;
  /** Background gradient, from → to. */
  from: string;
  to: string;
}

export const AVATAR_PRESETS: AvatarPreset[] = [
  { id: 'balaams-donkey', name: "Balaam's Talking Donkey", emoji: '🫏', badge: '💬', from: '#a16207', to: '#facc15' },
  { id: 'jonahs-whale', name: "Jonah's Whale", emoji: '🐋', badge: '🧔', from: '#0369a1', to: '#38bdf8' },
  { id: 'noahs-ark', name: "Noah's Ark", emoji: '🦒', badge: '⛵', from: '#1e40af', to: '#60a5fa' },
  { id: 'daniels-lion', name: "Daniel's Lion (Just Yawning)", emoji: '🦁', badge: '😴', from: '#b45309', to: '#fbbf24' },
  { id: 'davids-slingshot', name: "David's Slingshot", emoji: '🪨', badge: '🎯', from: '#57534e', to: '#a8a29e' },
  { id: 'goliath', name: 'Goliath (Big Mood)', emoji: '🗿', badge: '🤕', from: '#3f3f46', to: '#a1a1aa' },
  { id: 'zacchaeus', name: 'Zacchaeus Up a Tree', emoji: '🌳', badge: '👀', from: '#166534', to: '#4ade80' },
  { id: 'burning-bush', name: 'The Burning Bush', emoji: '🔥', badge: '🌿', from: '#9a3412', to: '#fb923c' },
  { id: 'samsons-hair', name: "Samson's Hair Day", emoji: '💪', badge: '✂️', from: '#7c2d12', to: '#f97316' },
  { id: 'josephs-coat', name: "Joseph's Coat", emoji: '🧥', badge: '🌈', from: '#7e22ce', to: '#f472b6' },
  { id: 'loaves-fishes', name: 'Loaves and Fishes', emoji: '🐟', badge: '🍞', from: '#0e7490', to: '#fcd34d' },
  { id: 'peters-rooster', name: "Peter's Rooster", emoji: '🐓', badge: '⏰', from: '#b91c1c', to: '#fca5a5' },
  { id: 'lost-sheep', name: 'The Lost Sheep', emoji: '🐑', badge: '❓', from: '#475569', to: '#e2e8f0' },
  { id: 'frog-plague', name: 'Frogs. Everywhere.', emoji: '🐸', badge: '🛏️', from: '#15803d', to: '#86efac' },
  { id: 'locusts', name: 'Locust Snack', emoji: '🦗', badge: '🍯', from: '#4d7c0f', to: '#bef264' },
  { id: 'jericho', name: 'Walls of Jericho', emoji: '🧱', badge: '🎺', from: '#92400e', to: '#d6d3d1' },
  { id: 'jacobs-ladder', name: "Jacob's Ladder", emoji: '🪜', badge: '😇', from: '#4338ca', to: '#c4b5fd' },
  { id: 'elijahs-chariot', name: "Elijah's Ride", emoji: '🐎', badge: '🔥', from: '#b91c1c', to: '#fde047' },
  { id: 'manna', name: 'Manna from Heaven', emoji: '☁️', badge: '🥐', from: '#0284c7', to: '#fef3c7' },
  { id: 'mustard-seed', name: 'Tiny Mustard Seed', emoji: '🌱', badge: '🔍', from: '#3f6212', to: '#d9f99d' },
  { id: 'red-sea', name: 'Red Sea Shortcut', emoji: '🌊', badge: '🚶', from: '#1d4ed8', to: '#f87171' },
  { id: 'star-bethlehem', name: 'Star of Bethlehem', emoji: '⭐', badge: '🐫', from: '#1e1b4b', to: '#6366f1' },
  { id: 'water-wine', name: 'Water into Wine', emoji: '🍇', badge: '🫙', from: '#701a75', to: '#e879f9' },
  { id: 'walking-water', name: 'Walking on Water', emoji: '🦶', badge: '🌊', from: '#0c4a6e', to: '#7dd3fc' },
  { id: 'gideons-fleece', name: "Gideon's Fleece", emoji: '🐏', badge: '💧', from: '#525252', to: '#bae6fd' },
  { id: 'ravens-delivery', name: "Elijah's Raven Delivery", emoji: '🐦‍⬛', badge: '🥪', from: '#18181b', to: '#71717a' },
];

const BY_ID = new Map(AVATAR_PRESETS.map((p) => [p.id, p]));

export function presetFor(avatar: string | null | undefined): AvatarPreset | null {
  if (!avatar?.startsWith('preset:')) return null;
  return BY_ID.get(avatar.slice('preset:'.length)) ?? null;
}

/**
 * A picked photo, scaled to a 512px square JPEG in the browser so the upload
 * is small and every avatar has the same shape. The centre of the photo is
 * kept, the way a phone's contact photo crop does it.
 */
export async function squarePhoto(file: File, size = 512): Promise<string> {
  const bitmap = await createImageBitmap(file);
  const side = Math.min(bitmap.width, bitmap.height);
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('This device cannot prepare the picture.');
  ctx.drawImage(bitmap, (bitmap.width - side) / 2, (bitmap.height - side) / 2, side, side, 0, 0, size, size);
  bitmap.close?.();
  return canvas.toDataURL('image/jpeg', 0.85);
}
