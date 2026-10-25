/**
 * Built-in profile pictures: Bible stories at their silliest.
 *
 * Each preset carries a custom, multi-layered inline SVG illustration drawn on
 * a 100×100 canvas (plus its signature emoji/badge metadata), so every character
 * renders crisply at any size on every device-including inside Leaflet map pins-
 * with zero image files to fetch. Identity stores only the id ("preset:<id>"),
 * so artwork can be refined without touching anyone's account.
 */
export interface AvatarPreset {
  id: string;
  name: string;
  /** The main figure (kept for accessible/text fallbacks). */
  emoji: string;
  /** The small corner joke. */
  badge: string;
  /** Background gradient, from → to. */
  from: string;
  to: string;
  /** Custom 100×100 inner SVG illustration markup. */
  svg: string;
}

export const AVATAR_PRESETS: AvatarPreset[] = [
  {
    id: 'balaams-donkey',
    name: "Balaam's Talking Donkey",
    emoji: '🫏',
    badge: '💬',
    from: '#92400e',
    to: '#facc15',
    svg: `
      <circle cx="50" cy="54" r="38" fill="#fef08a" opacity="0.22"/>
      <path d="M10 88 Q50 74 90 88 L90 100 L10 100 Z" fill="#78350f" opacity="0.45"/>
      <!-- Long expressive donkey ears -->
      <path d="M28 42 C14 14, 24 6, 35 26 C38 32, 36 40, 33 44 Z" fill="#78716c" stroke="#44403c" stroke-width="2.5"/>
      <path d="M29 38 C21 20, 26 14, 33 28 Z" fill="#fda4af"/>
      <path d="M62 40 C68 12, 80 10, 74 32 C72 38, 67 42, 63 44 Z" fill="#78716c" stroke="#44403c" stroke-width="2.5"/>
      <path d="M64 37 C68 20, 74 18, 70 32 Z" fill="#fda4af"/>
      <!-- Mane tuft -->
      <path d="M42 28 L46 18 L50 27 L55 19 L57 29 Z" fill="#292524"/>
      <!-- Head & neck -->
      <path d="M32 88 L36 58 L60 58 L64 88 Z" fill="#78716c"/>
      <ellipse cx="48" cy="52" rx="20" ry="18" fill="#a8a29e" stroke="#44403c" stroke-width="2.5"/>
      <!-- Sassy raised eyebrow & big cartoon eyes -->
      <circle cx="40" cy="47" r="5.5" fill="#ffffff" stroke="#292524" stroke-width="2"/>
      <circle cx="41.5" cy="47" r="2.7" fill="#1c1917"/>
      <circle cx="42.5" cy="45.8" r="1" fill="#ffffff"/>
      <circle cx="56" cy="47" r="5.5" fill="#ffffff" stroke="#292524" stroke-width="2"/>
      <circle cx="57.5" cy="47" r="2.7" fill="#1c1917"/>
      <circle cx="58.5" cy="45.8" r="1" fill="#ffffff"/>
      <path d="M34 39 Q40 35 45 40" fill="none" stroke="#292524" stroke-width="2.5" stroke-linecap="round"/>
      <path d="M51 40 Q57 34 63 38" fill="none" stroke="#292524" stroke-width="2.5" stroke-linecap="round"/>
      <!-- Big talking muzzle & toothy grin -->
      <ellipse cx="49" cy="66" rx="17" ry="13" fill="#e7e5e4" stroke="#44403c" stroke-width="2.5"/>
      <circle cx="42" cy="60" r="2.3" fill="#44403c"/>
      <circle cx="56" cy="60" r="2.3" fill="#44403c"/>
      <path d="M37 65 Q49 80 61 65 Z" fill="#881337" stroke="#44403c" stroke-width="2"/>
      <rect x="41" y="65" width="4" height="4.5" rx="1" fill="#ffffff"/>
      <rect x="46" y="65" width="4" height="4.5" rx="1" fill="#ffffff"/>
      <rect x="51" y="65" width="4" height="4.5" rx="1" fill="#ffffff"/>
      <ellipse cx="49" cy="72" rx="6" ry="3" fill="#fb7185"/>
      <!-- Comic speech bubble in top-right -->
      <path d="M64 12 H92 A5 5 0 0 1 97 17 V31 A5 5 0 0 1 92 36 H75 L66 43 L69 36 H64 A5 5 0 0 1 59 31 V17 A5 5 0 0 1 64 12 Z" fill="#ffffff" stroke="#1e293b" stroke-width="2.5"/>
      <text x="78" y="28" text-anchor="middle" font-family="system-ui,sans-serif" font-weight="900" font-size="11" fill="#dc2626">HEE?!</text>
    `,
  },
  {
    id: 'jonahs-whale',
    name: "Jonah's Whale",
    emoji: '🐋',
    badge: '🧔',
    from: '#0c4a6e',
    to: '#38bdf8',
    svg: `
      <!-- Water spout at top -->
      <path d="M46 24 Q44 10 32 10 M48 24 Q48 7 48 5 M50 24 Q52 10 64 10" fill="none" stroke="#bae6fd" stroke-width="3" stroke-linecap="round"/>
      <circle cx="30" cy="14" r="2.5" fill="#e0f2fe"/>
      <circle cx="66" cy="14" r="2.5" fill="#e0f2fe"/>
      <!-- Giant round whale body -->
      <path d="M12 58 C12 28, 42 22, 68 28 C84 32, 92 46, 94 36 C96 46, 90 56, 84 60 C82 76, 62 86, 38 86 C18 86, 12 74, 12 58 Z" fill="#0284c7" stroke="#082f49" stroke-width="2.5"/>
      <!-- Whale eye -->
      <circle cx="28" cy="42" r="5" fill="#ffffff" stroke="#082f49" stroke-width="2"/>
      <circle cx="27" cy="42" r="2.5" fill="#0f172a"/>
      <circle cx="26" cy="40.8" r="1" fill="#ffffff"/>
      <!-- Wide-open whale cavern mouth -->
      <path d="M16 54 Q45 46 68 58 Q64 82 36 82 Q16 80 16 54 Z" fill="#4c0519" stroke="#082f49" stroke-width="2.5"/>
      <!-- Baleen teeth along upper jaw -->
      <path d="M20 53 L22 59 L26 53 L29 59 L33 52 L36 59 L40 53 L43 59 L47 53 L50 59 L54 54 L58 60" fill="none" stroke="#f8fafc" stroke-width="2.2" stroke-linejoin="round"/>
      <!-- Pink tongue -->
      <ellipse cx="42" cy="77" rx="18" ry="5" fill="#fb7185"/>
      <!-- Tiny bearded Jonah peeking out from inside the mouth waving -->
      <circle cx="44" cy="64" r="6.5" fill="#fed7aa" stroke="#431407" stroke-width="1.8"/>
      <path d="M37.5 64 Q44 74 50.5 64 Q48 72 44 72 Q40 72 37.5 64 Z" fill="#451a03"/>
      <circle cx="42" cy="62.5" r="1.2" fill="#1e293b"/>
      <circle cx="46" cy="62.5" r="1.2" fill="#1e293b"/>
      <path d="M52 67 L58 59" stroke="#fed7aa" stroke-width="3" stroke-linecap="round"/>
      <circle cx="58.5" cy="58.5" r="2.2" fill="#fed7aa"/>
      <!-- Ocean waves at bottom -->
      <path d="M4 86 Q16 79 28 86 T52 86 T76 86 T100 86 L100 100 L4 100 Z" fill="#0369a1" opacity="0.85"/>
      <path d="M0 91 Q14 85 28 91 T56 91 T84 91 T100 91" fill="none" stroke="#bae6fd" stroke-width="2.5" stroke-linecap="round"/>
    `,
  },
  {
    id: 'noahs-ark',
    name: "Noah's Ark",
    emoji: '🦒',
    badge: '⛵',
    from: '#1e3a8a',
    to: '#38bdf8',
    svg: `
      <!-- Rainbow arc in the sky -->
      <path d="M12 52 A40 40 0 0 1 88 52" fill="none" stroke="#ef4444" stroke-width="4"/>
      <path d="M15 52 A37 37 0 0 1 85 52" fill="none" stroke="#facc15" stroke-width="4"/>
      <path d="M18 52 A34 34 0 0 1 82 52" fill="none" stroke="#22c55e" stroke-width="4"/>
      <path d="M21 52 A31 31 0 0 1 79 52" fill="none" stroke="#3b82f6" stroke-width="3.5"/>
      <!-- Two goofy giraffes sticking way out of the ark roof -->
      <path d="M38 56 L32 24" stroke="#facc15" stroke-width="7" stroke-linecap="round"/>
      <circle cx="34" cy="38" r="2" fill="#92400e"/>
      <circle cx="33" cy="46" r="1.8" fill="#92400e"/>
      <ellipse cx="30" cy="22" rx="6.5" ry="5" fill="#facc15" stroke="#78350f" stroke-width="1.8"/>
      <line x1="28" y1="17" x2="27" y2="13" stroke="#78350f" stroke-width="2"/>
      <circle cx="27" cy="12.5" r="1.8" fill="#78350f"/>
      <line x1="32" y1="17" x2="33" y2="13" stroke="#78350f" stroke-width="2"/>
      <circle cx="33" cy="12.5" r="1.8" fill="#78350f"/>
      <circle cx="28.5" cy="21.5" r="1.5" fill="#1e293b"/>
      <circle cx="32.5" cy="21.5" r="1.5" fill="#1e293b"/>
      <!-- Second giraffe -->
      <path d="M56 56 L64 26" stroke="#fbbf24" stroke-width="7" stroke-linecap="round"/>
      <circle cx="61" cy="37" r="2" fill="#92400e"/>
      <circle cx="59" cy="45" r="1.8" fill="#92400e"/>
      <ellipse cx="66" cy="24" rx="6.5" ry="5" fill="#fbbf24" stroke="#78350f" stroke-width="1.8"/>
      <line x1="64" y1="19" x2="63" y2="15" stroke="#78350f" stroke-width="2"/>
      <circle cx="63" cy="14.5" r="1.8" fill="#78350f"/>
      <line x1="68" y1="19" x2="69" y2="15" stroke="#78350f" stroke-width="2"/>
      <circle cx="69" cy="14.5" r="1.8" fill="#78350f"/>
      <circle cx="64.5" cy="23.5" r="1.5" fill="#1e293b"/>
      <circle cx="68.5" cy="23.5" r="1.5" fill="#1e293b"/>
      <!-- Ark cabin & roof -->
      <rect x="26" y="48" width="48" height="18" rx="3" fill="#b45309" stroke="#451a03" stroke-width="2.2"/>
      <path d="M22 50 L50 38 L78 50 Z" fill="#92400e" stroke="#451a03" stroke-width="2.2" stroke-linejoin="round"/>
      <circle cx="40" cy="56" r="4" fill="#fef08a" stroke="#451a03" stroke-width="1.8"/>
      <circle cx="52" cy="56" r="4" fill="#fef08a" stroke="#451a03" stroke-width="1.8"/>
      <circle cx="64" cy="56" r="4" fill="#fef08a" stroke="#451a03" stroke-width="1.8"/>
      <!-- Wooden Ark hull -->
      <path d="M12 62 H88 L76 84 H24 Z" fill="#78350f" stroke="#451a03" stroke-width="2.5" stroke-linejoin="round"/>
      <line x1="17" y1="70" x2="83" y2="70" stroke="#451a03" stroke-width="2"/>
      <!-- Rolling sea waves -->
      <path d="M0 82 Q14 75 28 82 T56 82 T84 82 T100 82 L100 100 L0 100 Z" fill="#0284c7"/>
      <path d="M6 88 Q20 82 34 88 T62 88 T90 88" fill="none" stroke="#e0f2fe" stroke-width="2.5" stroke-linecap="round"/>
    `,
  },
  {
    id: 'daniels-lion',
    name: "Daniel's Lion (Just Yawning)",
    emoji: '🦁',
    badge: '😴',
    from: '#78350f',
    to: '#fbbf24',
    svg: `
      <!-- Soft pillow at bottom -->
      <rect x="16" y="70" width="68" height="20" rx="10" fill="#e0e7ff" stroke="#4338ca" stroke-width="2"/>
      <!-- Fluffy scalloped lion mane -->
      <circle cx="50" cy="54" r="28" fill="#b45309" stroke="#451a03" stroke-width="2.5"/>
      <circle cx="28" cy="40" r="8" fill="#b45309"/>
      <circle cx="72" cy="40" r="8" fill="#b45309"/>
      <circle cx="24" cy="56" r="8" fill="#b45309"/>
      <circle cx="76" cy="56" r="8" fill="#b45309"/>
      <circle cx="32" cy="70" r="8" fill="#b45309"/>
      <circle cx="68" cy="70" r="8" fill="#b45309"/>
      <!-- Round ears -->
      <circle cx="33" cy="34" r="6" fill="#f59e0b" stroke="#451a03" stroke-width="2"/>
      <circle cx="67" cy="34" r="6" fill="#f59e0b" stroke="#451a03" stroke-width="2"/>
      <!-- Lion face -->
      <circle cx="50" cy="54" r="20" fill="#fbbf24" stroke="#451a03" stroke-width="2.2"/>
      <!-- Cozy striped nightcap -->
      <path d="M32 38 C36 16, 62 12, 80 26 C70 28, 62 34, 66 40 Z" fill="#6366f1" stroke="#312e81" stroke-width="2.2" stroke-linejoin="round"/>
      <path d="M31 38 Q50 33 67 40" fill="none" stroke="#ffffff" stroke-width="5" stroke-linecap="round"/>
      <circle cx="82" cy="26" r="5" fill="#ffffff" stroke="#312e81" stroke-width="2"/>
      <!-- Closed sleepy eyes -->
      <path d="M37 49 Q42 54 46 49" fill="none" stroke="#451a03" stroke-width="2.8" stroke-linecap="round"/>
      <path d="M54 49 Q58 54 63 49" fill="none" stroke="#451a03" stroke-width="2.8" stroke-linecap="round"/>
      <!-- Big sleepy yawn mouth -->
      <polygon points="47,54 53,54 50,58" fill="#78350f"/>
      <ellipse cx="50" cy="65" rx="9" ry="7.5" fill="#881337" stroke="#451a03" stroke-width="2"/>
      <ellipse cx="50" cy="69" rx="5.5" ry="3" fill="#fb7185"/>
      <!-- Whiskers -->
      <line x1="25" y1="58" x2="36" y2="60" stroke="#451a03" stroke-width="1.8" stroke-linecap="round"/>
      <line x1="25" y1="63" x2="36" y2="63" stroke="#451a03" stroke-width="1.8" stroke-linecap="round"/>
      <line x1="75" y1="58" x2="64" y2="60" stroke="#451a03" stroke-width="1.8" stroke-linecap="round"/>
      <line x1="75" y1="63" x2="64" y2="63" stroke="#451a03" stroke-width="1.8" stroke-linecap="round"/>
      <!-- Floating Zzz -->
      <text x="74" y="18" font-family="system-ui,sans-serif" font-weight="900" font-size="12" fill="#fef08a">Z</text>
      <text x="83" y="12" font-family="system-ui,sans-serif" font-weight="900" font-size="9" fill="#ffffff">z</text>
      <text x="90" y="8" font-family="system-ui,sans-serif" font-weight="900" font-size="7" fill="#fde68a">z</text>
    `,
  },
  {
    id: 'davids-slingshot',
    name: "David's Slingshot",
    emoji: '🪨',
    badge: '🎯',
    from: '#44403c',
    to: '#a8a29e',
    svg: `
      <!-- Bullseye target in top-right corner -->
      <circle cx="77" cy="25" r="16" fill="#ffffff" stroke="#1e293b" stroke-width="2.2"/>
      <circle cx="77" cy="25" r="11" fill="#ef4444"/>
      <circle cx="77" cy="25" r="6" fill="#ffffff"/>
      <circle cx="77" cy="25" r="3" fill="#ef4444"/>
      <!-- Starburst impact sparks -->
      <polygon points="77,7 79,14 86,11 82,18 89,21 81,23 85,30 78,26 75,33 74,25 67,27 72,21 67,15 74,16" fill="#facc15" opacity="0.85"/>
      <!-- Motion whoosh lines -->
      <path d="M26 62 Q48 44 66 30" fill="none" stroke="#fef08a" stroke-width="3" stroke-dasharray="5 4" stroke-linecap="round"/>
      <path d="M32 68 Q52 52 70 36" fill="none" stroke="#ffffff" stroke-width="2" stroke-dasharray="4 4" stroke-linecap="round"/>
      <!-- Flying smooth river stone -->
      <ellipse cx="60" cy="38" rx="9" ry="7" transform="rotate(-25 60 38)" fill="#94a3b8" stroke="#1e293b" stroke-width="2.2"/>
      <ellipse cx="58" cy="36" rx="4" ry="2.2" transform="rotate(-25 58 36)" fill="#e2e8f0"/>
      <!-- Wooden Y-Slingshot in foreground -->
      <path d="M28 92 L32 66 M32 66 L18 42 M32 66 L46 46" fill="none" stroke="#92400e" stroke-width="8" stroke-linecap="round" stroke-linejoin="round"/>
      <path d="M28 92 L32 66 M32 66 L18 42 M32 66 L46 46" fill="none" stroke="#b45309" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
      <!-- Elastic bands snapped forward -->
      <path d="M18 42 Q38 35 50 42" fill="none" stroke="#fde047" stroke-width="2.5" stroke-linecap="round"/>
      <path d="M46 46 Q52 40 50 42" fill="none" stroke="#fde047" stroke-width="2.5" stroke-linecap="round"/>
    `,
  },
  {
    id: 'goliath',
    name: 'Goliath (Big Mood)',
    emoji: '🗿',
    badge: '🤕',
    from: '#27272a',
    to: '#71717a',
    svg: `
      <!-- Orbiting dizzy ring & stars at top -->
      <ellipse cx="50" cy="18" rx="30" ry="7" fill="none" stroke="#fde047" stroke-width="2" stroke-dasharray="4 4"/>
      <polygon points="22,14 24,18 28,18 25,21 26,25 22,22 18,25 19,21 16,18 20,18" fill="#facc15"/>
      <polygon points="78,13 80,17 84,17 81,20 82,24 78,21 74,24 75,20 72,17 76,17" fill="#facc15"/>
      <polygon points="50,7 51.5,11 55.5,11 52.5,13.5 53.5,17.5 50,15 46.5,17.5 47.5,13.5 44.5,11 48.5,11" fill="#fde047"/>
      <!-- Giant head & scruffy beard -->
      <path d="M24 44 C24 24, 76 24, 76 44 L76 74 C76 88, 24 88, 24 74 Z" fill="#fdba74" stroke="#431407" stroke-width="2.5"/>
      <path d="M24 64 Q50 72 76 64 L74 82 Q50 94 26 82 Z" fill="#292524"/>
      <!-- Bronze Philistine helmet tilted askew -->
      <path d="M20 44 C20 20, 80 20, 80 44 Z" fill="#d97706" stroke="#451a03" stroke-width="2.5"/>
      <rect x="18" y="40" width="64" height="6" rx="2" fill="#f59e0b" stroke="#451a03" stroke-width="2"/>
      <!-- Big cartoon X-shaped bandage on forehead -->
      <g transform="translate(50,37) rotate(22)">
        <rect x="-11" y="-4" width="22" height="8" rx="3" fill="#fef3c7" stroke="#92400e" stroke-width="1.5"/>
      </g>
      <g transform="translate(50,37) rotate(-22)">
        <rect x="-11" y="-4" width="22" height="8" rx="3" fill="#fef3c7" stroke="#92400e" stroke-width="1.5"/>
      </g>
      <!-- Cross-eyed dizzy eyes -->
      <circle cx="38" cy="54" r="6.5" fill="#ffffff" stroke="#292524" stroke-width="2"/>
      <circle cx="41" cy="54" r="2.8" fill="#1e293b"/>
      <circle cx="62" cy="54" r="6.5" fill="#ffffff" stroke="#292524" stroke-width="2"/>
      <circle cx="59" cy="53" r="2.8" fill="#1e293b"/>
      <!-- Wobbly dazed mouth with tongue sticking out -->
      <path d="M38 73 Q44 69 50 73 T62 73" fill="none" stroke="#ffffff" stroke-width="2.5" stroke-linecap="round"/>
      <path d="M52 73 Q52 82 58 80 Q61 78 58 73 Z" fill="#fb7185" stroke="#881337" stroke-width="1.5"/>
    `,
  },
  {
    id: 'zacchaeus',
    name: 'Zacchaeus Up a Tree',
    emoji: '🌳',
    badge: '👀',
    from: '#14532d',
    to: '#4ade80',
    svg: `
      <!-- Sycamore trunk & sturdy branch -->
      <path d="M78 100 L72 52 L20 58 L20 48 L76 42 L88 100 Z" fill="#78350f" stroke="#451a03" stroke-width="2.2"/>
      <!-- Layered lush tree canopy -->
      <circle cx="28" cy="34" r="20" fill="#15803d"/>
      <circle cx="52" cy="26" r="22" fill="#16a34a"/>
      <circle cx="74" cy="34" r="18" fill="#15803d"/>
      <circle cx="40" cy="40" r="18" fill="#22c55e"/>
      <circle cx="62" cy="40" r="17" fill="#22c55e"/>
      <!-- Tiny dangling legs & sandals below the branch -->
      <line x1="43" y1="55" x2="41" y2="74" stroke="#fed7aa" stroke-width="5" stroke-linecap="round"/>
      <line x1="55" y1="55" x2="57" y2="74" stroke="#fed7aa" stroke-width="5" stroke-linecap="round"/>
      <ellipse cx="39" cy="76" rx="5" ry="2.8" fill="#92400e"/>
      <ellipse cx="59" cy="76" rx="5" ry="2.8" fill="#92400e"/>
      <!-- Peeking Zacchaeus with huge binocular-wide eyes in the leaves -->
      <circle cx="49" cy="36" r="12" fill="#fed7aa" stroke="#431407" stroke-width="2"/>
      <circle cx="43" cy="35" r="6.5" fill="#ffffff" stroke="#14532d" stroke-width="2.2"/>
      <circle cx="44" cy="35" r="3.2" fill="#0f172a"/>
      <circle cx="45" cy="33.5" r="1.2" fill="#ffffff"/>
      <circle cx="55" cy="35" r="6.5" fill="#ffffff" stroke="#14532d" stroke-width="2.2"/>
      <circle cx="56" cy="35" r="3.2" fill="#0f172a"/>
      <circle cx="57" cy="33.5" r="1.2" fill="#ffffff"/>
      <!-- Little hands gripping the branch -->
      <circle cx="37" cy="50" r="4" fill="#fed7aa" stroke="#431407" stroke-width="1.8"/>
      <circle cx="61" cy="48" r="4" fill="#fed7aa" stroke="#431407" stroke-width="1.8"/>
    `,
  },
  {
    id: 'burning-bush',
    name: 'The Burning Bush',
    emoji: '🔥',
    badge: '🌿',
    from: '#7c2d12',
    to: '#fb923c',
    svg: `
      <!-- Desert ground -->
      <ellipse cx="50" cy="86" rx="40" ry="8" fill="#9a3412" opacity="0.6"/>
      <!-- Outer dancing red/orange flame halo -->
      <path d="M50 8 C64 22, 86 34, 82 58 C80 74, 66 84, 50 84 C34 84, 20 74, 18 58 C14 34, 36 22, 50 8 Z" fill="#ef4444" stroke="#7f1d1d" stroke-width="2"/>
      <path d="M50 16 C60 28, 76 38, 73 58 C71 70, 62 78, 50 78 C38 78, 29 70, 27 58 C24 38, 40 28, 50 16 Z" fill="#f97316"/>
      <path d="M50 26 C58 36, 68 44, 66 58 C64 68, 58 74, 50 74 C42 74, 36 68, 34 58 C32 44, 42 36, 50 26 Z" fill="#facc15"/>
      <!-- Totally unharmed lush green bush right inside the fire! -->
      <circle cx="38" cy="58" r="12" fill="#16a34a" stroke="#14532d" stroke-width="2"/>
      <circle cx="62" cy="58" r="12" fill="#16a34a" stroke="#14532d" stroke-width="2"/>
      <circle cx="50" cy="50" r="14" fill="#22c55e" stroke="#14532d" stroke-width="2"/>
      <circle cx="50" cy="63" r="12" fill="#15803d"/>
      <!-- Wide glowing eyes inside the bush -->
      <circle cx="44" cy="53" r="4.5" fill="#fef08a" stroke="#14532d" stroke-width="1.8"/>
      <circle cx="44" cy="53" r="2" fill="#14532d"/>
      <circle cx="56" cy="53" r="4.5" fill="#fef08a" stroke="#14532d" stroke-width="1.8"/>
      <circle cx="56" cy="53" r="2" fill="#14532d"/>
      <!-- Kicked-off Moses sandals at the bottom -->
      <ellipse cx="28" cy="86" rx="8" ry="4" transform="rotate(-15 28 86)" fill="#b45309" stroke="#451a03" stroke-width="1.8"/>
      <path d="M24 85 Q28 81 32 85" fill="none" stroke="#fde68a" stroke-width="1.8"/>
      <ellipse cx="72" cy="87" rx="8" ry="4" transform="rotate(18 72 87)" fill="#b45309" stroke="#451a03" stroke-width="1.8"/>
      <path d="M68 86 Q72 82 76 86" fill="none" stroke="#fde68a" stroke-width="1.8"/>
    `,
  },
  {
    id: 'samsons-hair',
    name: "Samson's Hair Day",
    emoji: '💪',
    badge: '✂️',
    from: '#7c2d12',
    to: '#f97316',
    svg: `
      <!-- Glorious, impossibly voluminous rockstar mane of curls -->
      <circle cx="30" cy="36" r="14" fill="#451a03"/>
      <circle cx="70" cy="36" r="14" fill="#451a03"/>
      <circle cx="24" cy="54" r="15" fill="#451a03"/>
      <circle cx="76" cy="54" r="15" fill="#451a03"/>
      <circle cx="38" cy="24" r="14" fill="#78350f"/>
      <circle cx="62" cy="24" r="14" fill="#78350f"/>
      <circle cx="50" cy="20" r="15" fill="#451a03"/>
      <circle cx="28" cy="70" r="12" fill="#78350f"/>
      <circle cx="72" cy="70" r="12" fill="#78350f"/>
      <!-- Flexing muscular arms -->
      <path d="M14 72 Q10 52 22 50 Q30 50 28 62 L36 68" fill="#fdba74" stroke="#431407" stroke-width="2.2" stroke-linejoin="round"/>
      <path d="M86 72 Q90 52 78 50 Q70 50 72 62 L64 68" fill="#fdba74" stroke="#431407" stroke-width="2.2" stroke-linejoin="round"/>
      <!-- Samson's face -->
      <circle cx="50" cy="48" r="16" fill="#fed7aa" stroke="#431407" stroke-width="2.2"/>
      <!-- Confident wink & big grin -->
      <path d="M41 45 Q44 42 47 45" fill="none" stroke="#431407" stroke-width="2.5" stroke-linecap="round"/>
      <circle cx="56" cy="45" r="2.5" fill="#431407"/>
      <path d="M43 53 Q50 60 57 53 Z" fill="#ffffff" stroke="#431407" stroke-width="1.8"/>
      <!-- Sneaky barber scissors snipping in from top-right! -->
      <line x1="68" y1="22" x2="92" y2="10" stroke="#e2e8f0" stroke-width="4" stroke-linecap="round"/>
      <line x1="68" y1="12" x2="92" y2="24" stroke="#cbd5e1" stroke-width="4" stroke-linecap="round"/>
      <circle cx="91" cy="10" r="4.5" fill="none" stroke="#ef4444" stroke-width="2.5"/>
      <circle cx="91" cy="24" r="4.5" fill="none" stroke="#ef4444" stroke-width="2.5"/>
      <circle cx="78" cy="17" r="2" fill="#475569"/>
    `,
  },
  {
    id: 'josephs-coat',
    name: "Joseph's Coat",
    emoji: '🧥',
    badge: '🌈',
    from: '#581c87',
    to: '#ec4899',
    svg: `
      <!-- Dazzling 4-point sparkles around the coat -->
      <path d="M16 20 L18 26 L24 28 L18 30 L16 36 L14 30 L8 28 L14 26 Z" fill="#fef08a"/>
      <path d="M84 18 L86 23 L91 25 L86 27 L84 32 L82 27 L77 25 L82 23 Z" fill="#fef08a"/>
      <path d="M14 70 L15.5 74 L19.5 75.5 L15.5 77 L14 81 L12.5 77 L8.5 75.5 L12.5 74 Z" fill="#bae6fd"/>
      <!-- Cool sunglasses floating above the popped collar -->
      <rect x="35" y="16" width="13" height="8" rx="2" fill="#0f172a" stroke="#facc15" stroke-width="1.8"/>
      <rect x="52" y="16" width="13" height="8" rx="2" fill="#0f172a" stroke="#facc15" stroke-width="1.8"/>
      <line x1="48" y1="19" x2="52" y2="19" stroke="#facc15" stroke-width="2"/>
      <!-- Technicolor coat silhouette -->
      <path d="M32 30 L12 46 L20 58 L30 50 L26 88 H74 L70 50 L80 58 L88 46 L68 30 L50 38 Z" fill="#facc15" stroke="#1e1b4b" stroke-width="2.5" stroke-linejoin="round"/>
      <!-- Colorful patchwork stripes & blocks -->
      <rect x="29" y="42" width="14" height="14" fill="#ef4444" stroke="#1e1b4b" stroke-width="1.8"/>
      <rect x="43" y="42" width="14" height="14" fill="#3b82f6" stroke="#1e1b4b" stroke-width="1.8"/>
      <rect x="57" y="42" width="14" height="14" fill="#22c55e" stroke="#1e1b4b" stroke-width="1.8"/>
      <rect x="28" y="56" width="14" height="15" fill="#a855f7" stroke="#1e1b4b" stroke-width="1.8"/>
      <rect x="42" y="56" width="16" height="15" fill="#f97316" stroke="#1e1b4b" stroke-width="1.8"/>
      <rect x="58" y="56" width="14" height="15" fill="#06b6d4" stroke="#1e1b4b" stroke-width="1.8"/>
      <rect x="27" y="71" width="15" height="16" fill="#eab308" stroke="#1e1b4b" stroke-width="1.8"/>
      <rect x="42" y="71" width="16" height="16" fill="#ec4899" stroke="#1e1b4b" stroke-width="1.8"/>
      <rect x="58" y="71" width="15" height="16" fill="#10b981" stroke="#1e1b4b" stroke-width="1.8"/>
      <!-- Dramatic popped gold collar -->
      <polygon points="32,30 43,22 50,38" fill="#fde047" stroke="#1e1b4b" stroke-width="2"/>
      <polygon points="68,30 57,22 50,38" fill="#fde047" stroke="#1e1b4b" stroke-width="2"/>
    `,
  },
  {
    id: 'loaves-fishes',
    name: 'Loaves and Fishes',
    emoji: '🐟',
    badge: '🍞',
    from: '#0e7490',
    to: '#fcd34d',
    svg: `
      <!-- Leaping silver-blue fishes multiplying in an arc -->
      <g transform="translate(26,28) rotate(-25)">
        <ellipse cx="0" cy="0" rx="12" ry="6.5" fill="#38bdf8" stroke="#0c4a6e" stroke-width="2"/>
        <polygon points="-11,0 -19,-6 -19,6" fill="#0284c7" stroke="#0c4a6e" stroke-width="1.8"/>
        <circle cx="6" cy="-1.5" r="1.8" fill="#0f172a"/>
      </g>
      <g transform="translate(72,26) rotate(25)">
        <ellipse cx="0" cy="0" rx="12" ry="6.5" fill="#7dd3fc" stroke="#0c4a6e" stroke-width="2"/>
        <polygon points="11,0 19,-6 19,6" fill="#0284c7" stroke="#0c4a6e" stroke-width="1.8"/>
        <circle cx="-6" cy="-1.5" r="1.8" fill="#0f172a"/>
      </g>
      <!-- Plump golden bread loaves piled in the basket -->
      <ellipse cx="36" cy="54" rx="15" ry="10" transform="rotate(-12 36 54)" fill="#f59e0b" stroke="#78350f" stroke-width="2.2"/>
      <path d="M30 49 L34 55 M36 48 L40 54" stroke="#fef3c7" stroke-width="2" stroke-linecap="round"/>
      <ellipse cx="64" cy="54" rx="15" ry="10" transform="rotate(12 64 54)" fill="#d97706" stroke="#78350f" stroke-width="2.2"/>
      <path d="M60 48 L64 54 M66 49 L70 55" stroke="#fef3c7" stroke-width="2" stroke-linecap="round"/>
      <ellipse cx="50" cy="48" rx="16" ry="11" fill="#fbbf24" stroke="#78350f" stroke-width="2.2"/>
      <path d="M44 43 L47 50 M50 42 L53 49 M56 43 L59 50" stroke="#fef3c7" stroke-width="2" stroke-linecap="round"/>
      <!-- Woven picnic basket -->
      <path d="M18 58 H82 L72 88 H28 Z" fill="#b45309" stroke="#451a03" stroke-width="2.5" stroke-linejoin="round"/>
      <rect x="15" y="54" width="70" height="7" rx="3.5" fill="#d97706" stroke="#451a03" stroke-width="2.2"/>
      <line x1="34" y1="61" x2="38" y2="88" stroke="#78350f" stroke-width="2"/>
      <line x1="50" y1="61" x2="50" y2="88" stroke="#78350f" stroke-width="2"/>
      <line x1="66" y1="61" x2="62" y2="88" stroke="#78350f" stroke-width="2"/>
      <line x1="22" y1="70" x2="78" y2="70" stroke="#78350f" stroke-width="2"/>
      <line x1="25" y1="79" x2="75" y2="79" stroke="#78350f" stroke-width="2"/>
    `,
  },
  {
    id: 'peters-rooster',
    name: "Peter's Rooster",
    emoji: '🐓',
    badge: '⏰',
    from: '#991b1b',
    to: '#fb923c',
    svg: `
      <!-- Rising sun rays at bottom -->
      <circle cx="50" cy="92" r="34" fill="#fde047" opacity="0.28"/>
      <!-- Red scalloped comb -->
      <path d="M26 30 C24 16, 34 14, 38 24 C42 12, 52 14, 52 25 C58 16, 66 22, 58 32 Z" fill="#ef4444" stroke="#7f1d1d" stroke-width="2.2"/>
      <!-- Rooster head & neck feathers -->
      <path d="M24 88 L30 42 C30 28, 56 28, 58 44 L66 88 Z" fill="#ffffff" stroke="#1e293b" stroke-width="2.5"/>
      <path d="M26 70 L34 78 L42 68 L50 78 L58 68 L64 76 L66 88 L24 88 Z" fill="#ea580c" stroke="#7c2d12" stroke-width="2"/>
      <!-- Huge wide-awake eye -->
      <circle cx="42" cy="42" r="6" fill="#fef08a" stroke="#1e293b" stroke-width="2.2"/>
      <circle cx="43.5" cy="42" r="2.8" fill="#0f172a"/>
      <!-- Wide-open crowing beak + red wattle -->
      <polygon points="55,38 78,30 57,48" fill="#facc15" stroke="#78350f" stroke-width="2.2" stroke-linejoin="round"/>
      <polygon points="56,48 75,52 55,56" fill="#eab308" stroke="#78350f" stroke-width="2.2" stroke-linejoin="round"/>
      <ellipse cx="52" cy="58" rx="4.5" ry="7" fill="#ef4444" stroke="#7f1d1d" stroke-width="1.8"/>
      <!-- Sonic boom crow waves -->
      <path d="M80 28 Q88 38 80 48" fill="none" stroke="#fef08a" stroke-width="3" stroke-linecap="round"/>
      <path d="M86 22 Q96 38 86 54" fill="none" stroke="#ffffff" stroke-width="2.5" stroke-linecap="round"/>
      <!-- Ringing alarm clock in bottom-right -->
      <circle cx="78" cy="76" r="12" fill="#ffffff" stroke="#1e293b" stroke-width="2.2"/>
      <circle cx="69" cy="64" r="4" fill="#fbbf24" stroke="#1e293b" stroke-width="1.8"/>
      <circle cx="87" cy="64" r="4" fill="#fbbf24" stroke="#1e293b" stroke-width="1.8"/>
      <polyline points="78,70 78,76 83,79" fill="none" stroke="#dc2626" stroke-width="2.2" stroke-linecap="round"/>
    `,
  },
  {
    id: 'lost-sheep',
    name: 'The Lost Sheep',
    emoji: '🐑',
    badge: '❓',
    from: '#334155',
    to: '#94a3b8',
    svg: `
      <!-- Floating question marks all around -->
      <text x="18" y="28" font-family="system-ui,sans-serif" font-weight="900" font-size="18" fill="#facc15">?</text>
      <text x="78" y="24" font-family="system-ui,sans-serif" font-weight="900" font-size="20" fill="#fb7185">?</text>
      <text x="82" y="72" font-family="system-ui,sans-serif" font-weight="900" font-size="15" fill="#fde047">?</text>
      <!-- Little black sheep legs -->
      <rect x="35" y="74" width="6" height="16" rx="3" fill="#1e293b"/>
      <rect x="45" y="76" width="6" height="15" rx="3" fill="#0f172a"/>
      <rect x="55" y="74" width="6" height="16" rx="3" fill="#1e293b"/>
      <!-- Ultra-fluffy cloud wool body -->
      <circle cx="32" cy="56" r="14" fill="#f8fafc" stroke="#cbd5e1" stroke-width="2"/>
      <circle cx="68" cy="56" r="14" fill="#f8fafc" stroke="#cbd5e1" stroke-width="2"/>
      <circle cx="38" cy="40" r="14" fill="#ffffff" stroke="#cbd5e1" stroke-width="2"/>
      <circle cx="62" cy="40" r="14" fill="#ffffff" stroke="#cbd5e1" stroke-width="2"/>
      <circle cx="40" cy="68" r="13" fill="#f8fafc" stroke="#cbd5e1" stroke-width="2"/>
      <circle cx="60" cy="68" r="13" fill="#f8fafc" stroke="#cbd5e1" stroke-width="2"/>
      <circle cx="50" cy="54" r="20" fill="#ffffff"/>
      <!-- Droopy black ears -->
      <ellipse cx="28" cy="46" rx="7" ry="4" transform="rotate(-25 28 46)" fill="#1e293b"/>
      <ellipse cx="72" cy="46" rx="7" ry="4" transform="rotate(25 72 46)" fill="#1e293b"/>
      <!-- Dark sheep face -->
      <ellipse cx="50" cy="52" rx="14" ry="16" fill="#1e293b"/>
      <!-- Fluffy wool bangs on top of head -->
      <circle cx="45" cy="36" r="6" fill="#ffffff"/>
      <circle cx="55" cy="36" r="6" fill="#ffffff"/>
      <circle cx="50" cy="34" r="6.5" fill="#ffffff"/>
      <!-- Goofy mismatched bewildered eyes -->
      <circle cx="43" cy="49" r="5.5" fill="#ffffff"/>
      <circle cx="42" cy="48" r="2.5" fill="#0f172a"/>
      <circle cx="57" cy="49" r="4" fill="#ffffff"/>
      <circle cx="58" cy="50" r="2" fill="#0f172a"/>
      <!-- Confused squiggly mouth -->
      <path d="M44 61 Q47 58 50 61 T56 61" fill="none" stroke="#fda4af" stroke-width="2.2" stroke-linecap="round"/>
    `,
  },
  {
    id: 'frog-plague',
    name: 'Frogs. Everywhere.',
    emoji: '🐸',
    badge: '🛏️',
    from: '#14532d',
    to: '#4ade80',
    svg: `
      <!-- Top frog peeking down upside-down -->
      <ellipse cx="50" cy="20" rx="16" ry="11" fill="#22c55e" stroke="#14532d" stroke-width="2"/>
      <circle cx="41" cy="27" r="5" fill="#ffffff" stroke="#14532d" stroke-width="1.8"/>
      <circle cx="41" cy="27" r="2.2" fill="#0f172a"/>
      <circle cx="59" cy="27" r="5" fill="#ffffff" stroke="#14532d" stroke-width="1.8"/>
      <circle cx="59" cy="27" r="2.2" fill="#0f172a"/>
      <!-- Left frog on the side -->
      <ellipse cx="22" cy="52" rx="14" ry="11" fill="#16a34a" stroke="#14532d" stroke-width="2"/>
      <circle cx="16" cy="42" r="5" fill="#ffffff" stroke="#14532d" stroke-width="1.8"/>
      <circle cx="16" cy="42" r="2.2" fill="#0f172a"/>
      <circle cx="28" cy="42" r="5" fill="#ffffff" stroke="#14532d" stroke-width="1.8"/>
      <circle cx="28" cy="42" r="2.2" fill="#0f172a"/>
      <!-- Right frog on the side -->
      <ellipse cx="78" cy="52" rx="14" ry="11" fill="#16a34a" stroke="#14532d" stroke-width="2"/>
      <circle cx="72" cy="42" r="5" fill="#ffffff" stroke="#14532d" stroke-width="1.8"/>
      <circle cx="72" cy="42" r="2.2" fill="#0f172a"/>
      <circle cx="84" cy="42" r="5" fill="#ffffff" stroke="#14532d" stroke-width="1.8"/>
      <circle cx="84" cy="42" r="2.2" fill="#0f172a"/>
      <!-- Big main bug-eyed frog front and center with long pink tongue -->
      <ellipse cx="50" cy="68" rx="26" ry="20" fill="#4ade80" stroke="#14532d" stroke-width="2.5"/>
      <ellipse cx="50" cy="73" rx="18" ry="11" fill="#bbf7d0"/>
      <circle cx="36" cy="49" r="8" fill="#4ade80" stroke="#14532d" stroke-width="2.2"/>
      <circle cx="36" cy="49" r="5.5" fill="#ffffff"/>
      <circle cx="36" cy="49" r="2.8" fill="#0f172a"/>
      <circle cx="64" cy="49" r="8" fill="#4ade80" stroke="#14532d" stroke-width="2.2"/>
      <circle cx="64" cy="49" r="5.5" fill="#ffffff"/>
      <circle cx="64" cy="49" r="2.8" fill="#0f172a"/>
      <path d="M32 66 Q50 76 68 66" fill="none" stroke="#14532d" stroke-width="2.8" stroke-linecap="round"/>
      <!-- Curly pink frog tongue zapping out -->
      <path d="M52 70 Q68 82 78 74 Q82 70 76 68" fill="none" stroke="#fb7185" stroke-width="4.5" stroke-linecap="round"/>
    `,
  },
  {
    id: 'locusts',
    name: 'Locust Snack',
    emoji: '🦗',
    badge: '🍯',
    from: '#3f6212',
    to: '#a3e635',
    svg: `
      <!-- Golden honey dipper dripping wild honey from top-right -->
      <line x1="92" y1="8" x2="66" y2="28" stroke="#92400e" stroke-width="4" stroke-linecap="round"/>
      <ellipse cx="64" cy="30" rx="8" ry="6" transform="rotate(-35 64 30)" fill="#f59e0b" stroke="#78350f" stroke-width="2"/>
      <path d="M60 35 Q60 46 63 46 Q66 46 66 35 Z" fill="#fbbf24"/>
      <circle cx="63" cy="52" r="3" fill="#fbbf24"/>
      <!-- Happy cartoon grasshopper/locust -->
      <path d="M24 34 Q18 18 12 20 M34 32 Q34 16 42 16" fill="none" stroke="#1a2e05" stroke-width="2.2" stroke-linecap="round"/>
      <!-- Hind jumping leg -->
      <path d="M48 64 L70 42 L80 76" fill="none" stroke="#4d7c0f" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>
      <!-- Body & wings -->
      <ellipse cx="52" cy="64" rx="22" ry="12" transform="rotate(-10 52 64)" fill="#84cc16" stroke="#1a2e05" stroke-width="2.2"/>
      <ellipse cx="54" cy="58" rx="18" ry="7" transform="rotate(-18 54 58)" fill="#bef264" stroke="#365314" stroke-width="1.8"/>
      <!-- Locust head -->
      <circle cx="30" cy="52" r="13" fill="#65a30d" stroke="#1a2e05" stroke-width="2.2"/>
      <circle cx="26" cy="48" r="5" fill="#ffffff" stroke="#1a2e05" stroke-width="1.8"/>
      <circle cx="26" cy="48" r="2.3" fill="#0f172a"/>
      <path d="M24 57 Q30 62 35 56" fill="none" stroke="#1a2e05" stroke-width="2.2" stroke-linecap="round"/>
      <!-- Little white dinner bib around neck -->
      <path d="M24 62 H38 L35 76 Q31 79 27 76 Z" fill="#ffffff" stroke="#cbd5e1" stroke-width="1.5"/>
    `,
  },
  {
    id: 'jericho',
    name: 'Walls of Jericho',
    emoji: '🧱',
    badge: '🎺',
    from: '#78350f',
    to: '#fbbf24',
    svg: `
      <!-- Crumbling stone tower tilting and breaking apart -->
      <g transform="rotate(-12 42 62)">
        <rect x="20" y="38" width="42" height="50" rx="2" fill="#d6d3d1" stroke="#44403c" stroke-width="2.5"/>
        <rect x="18" y="30" width="10" height="10" fill="#d6d3d1" stroke="#44403c" stroke-width="2"/>
        <rect x="35" y="30" width="10" height="10" fill="#d6d3d1" stroke="#44403c" stroke-width="2"/>
        <rect x="52" y="30" width="10" height="10" fill="#d6d3d1" stroke="#44403c" stroke-width="2"/>
        <!-- Jagged crack down the middle -->
        <polyline points="40,32 35,48 44,58 36,72 42,86" fill="none" stroke="#292524" stroke-width="3" stroke-linejoin="round"/>
        <line x1="20" y1="54" x2="62" y2="54" stroke="#78716c" stroke-width="2"/>
        <line x1="20" y1="70" x2="62" y2="70" stroke="#78716c" stroke-width="2"/>
      </g>
      <!-- Flying bricks -->
      <rect x="12" y="18" width="12" height="7" rx="1" transform="rotate(-28 18 21)" fill="#a8a29e" stroke="#44403c" stroke-width="1.8"/>
      <rect x="48" y="14" width="12" height="7" rx="1" transform="rotate(22 54 17)" fill="#e7e5e4" stroke="#44403c" stroke-width="1.8"/>
      <!-- Gleaming golden trumpet/shofar blasting from right -->
      <path d="M92 48 L64 42 L64 58 L92 52 Z" fill="#facc15" stroke="#78350f" stroke-width="2.2" stroke-linejoin="round"/>
      <ellipse cx="64" cy="50" rx="3.5" ry="9" fill="#eab308" stroke="#78350f" stroke-width="2"/>
      <!-- Sonic shockwaves -->
      <path d="M56 38 Q48 50 56 62" fill="none" stroke="#fef08a" stroke-width="3" stroke-linecap="round"/>
      <path d="M49 32 Q38 50 49 68" fill="none" stroke="#ffffff" stroke-width="2.5" stroke-linecap="round"/>
    `,
  },
  {
    id: 'jacobs-ladder',
    name: "Jacob's Ladder",
    emoji: '🪜',
    badge: '😇',
    from: '#312e81',
    to: '#a78bfa',
    svg: `
      <!-- Twinkling stars & radiant heaven cloud at top-right -->
      <circle cx="22" cy="22" r="2" fill="#fef08a"/>
      <circle cx="34" cy="14" r="1.5" fill="#ffffff"/>
      <circle cx="82" cy="64" r="2" fill="#fef08a"/>
      <circle cx="72" cy="18" r="14" fill="#ffffff" opacity="0.9"/>
      <circle cx="84" cy="22" r="12" fill="#f8fafc" opacity="0.9"/>
      <circle cx="60" cy="22" r="10" fill="#f8fafc" opacity="0.9"/>
      <!-- Glowing golden ladder stretching diagonally -->
      <line x1="22" y1="88" x2="66" y2="18" stroke="#facc15" stroke-width="5" stroke-linecap="round"/>
      <line x1="38" y1="92" x2="82" y2="22" stroke="#facc15" stroke-width="5" stroke-linecap="round"/>
      <line x1="27" y1="78" x2="43" y2="82" stroke="#fde047" stroke-width="3.5" stroke-linecap="round"/>
      <line x1="35" y1="65" x2="51" y2="69" stroke="#fde047" stroke-width="3.5" stroke-linecap="round"/>
      <line x1="43" y1="52" x2="59" y2="56" stroke="#fde047" stroke-width="3.5" stroke-linecap="round"/>
      <line x1="51" y1="39" x2="67" y2="43" stroke="#fde047" stroke-width="3.5" stroke-linecap="round"/>
      <line x1="59" y1="26" x2="75" y2="30" stroke="#fde047" stroke-width="3.5" stroke-linecap="round"/>
      <!-- Cute winged angel with halo on the ladder -->
      <ellipse cx="38" cy="46" rx="9" ry="5" transform="rotate(-20 38 46)" fill="#ffffff" stroke="#cbd5e1" stroke-width="1.5"/>
      <ellipse cx="62" cy="50" rx="9" ry="5" transform="rotate(20 62 50)" fill="#ffffff" stroke="#cbd5e1" stroke-width="1.5"/>
      <circle cx="50" cy="44" r="8" fill="#fed7aa" stroke="#431407" stroke-width="1.8"/>
      <ellipse cx="50" cy="32" rx="8" ry="2.5" fill="none" stroke="#fde047" stroke-width="2.5"/>
      <circle cx="47.5" cy="43" r="1.4" fill="#1e293b"/>
      <circle cx="52.5" cy="43" r="1.4" fill="#1e293b"/>
      <path d="M47 47 Q50 50 53 47" fill="none" stroke="#431407" stroke-width="1.6" stroke-linecap="round"/>
    `,
  },
  {
    id: 'elijahs-chariot',
    name: "Elijah's Ride",
    emoji: '🐎',
    badge: '🔥',
    from: '#991b1b',
    to: '#facc15',
    svg: `
      <!-- Streaking fire trail across the sky -->
      <path d="M6 76 C24 84, 52 80, 88 54 C74 36, 42 42, 10 56 C24 62, 16 70, 6 76 Z" fill="#ef4444"/>
      <path d="M14 72 C32 78, 56 74, 82 54 C68 42, 44 46, 18 58 Z" fill="#f97316"/>
      <!-- Golden chariot body -->
      <path d="M22 50 H54 C60 50, 64 58, 60 68 H24 Z" fill="#facc15" stroke="#78350f" stroke-width="2.5" stroke-linejoin="round"/>
      <path d="M26 55 H52" stroke="#b45309" stroke-width="2.5" stroke-linecap="round"/>
      <!-- Blazing chariot wheel -->
      <circle cx="38" cy="70" r="13" fill="#ea580c" stroke="#7c2d12" stroke-width="2.5"/>
      <circle cx="38" cy="70" r="8" fill="#fde047" stroke="#78350f" stroke-width="2"/>
      <line x1="38" y1="57" x2="38" y2="83" stroke="#78350f" stroke-width="2"/>
      <line x1="25" y1="70" x2="51" y2="70" stroke="#78350f" stroke-width="2"/>
      <!-- Fiery steed rearing at the front -->
      <path d="M58 58 L68 30 L84 26 L88 34 L76 40 L74 58 Z" fill="#fef08a" stroke="#b45309" stroke-width="2.2" stroke-linejoin="round"/>
      <path d="M64 36 C56 26, 62 18, 72 22" fill="#ef4444"/>
      <circle cx="77" cy="31" r="2" fill="#7c2d12"/>
    `,
  },
  {
    id: 'manna',
    name: 'Manna from Heaven',
    emoji: '☁️',
    badge: '🥐',
    from: '#0369a1',
    to: '#fde68a',
    svg: `
      <!-- Golden sunbeams shining down from the clouds -->
      <polygon points="32,26 16,92 38,92" fill="#fef08a" opacity="0.28"/>
      <polygon points="50,26 42,92 58,92" fill="#fef08a" opacity="0.35"/>
      <polygon points="68,26 62,92 84,92" fill="#fef08a" opacity="0.28"/>
      <!-- Fluffy heaven clouds at the top -->
      <circle cx="28" cy="22" r="13" fill="#ffffff"/>
      <circle cx="44" cy="18" r="15" fill="#ffffff"/>
      <circle cx="62" cy="19" r="14" fill="#ffffff"/>
      <circle cx="76" cy="23" r="11" fill="#f8fafc"/>
      <rect x="24" y="20" width="54" height="14" rx="7" fill="#ffffff"/>
      <!-- Raining golden croissants and honey wafers! -->
      <path d="M24 54 C20 44, 38 44, 34 54 C31 50, 27 50, 24 54 Z" fill="#f59e0b" stroke="#78350f" stroke-width="2" stroke-linejoin="round"/>
      <path d="M62 48 C58 38, 76 38, 72 48 C69 44, 65 44, 62 48 Z" fill="#f59e0b" stroke="#78350f" stroke-width="2" stroke-linejoin="round"/>
      <path d="M42 72 C38 62, 58 62, 54 72 C51 68, 45 68, 42 72 Z" fill="#fbbf24" stroke="#78350f" stroke-width="2" stroke-linejoin="round"/>
      <circle cx="48" cy="44" r="4.5" fill="#fef3c7" stroke="#d97706" stroke-width="1.8"/>
      <circle cx="28" cy="74" r="4" fill="#fef3c7" stroke="#d97706" stroke-width="1.8"/>
      <circle cx="72" cy="70" r="4.5" fill="#fef3c7" stroke="#d97706" stroke-width="1.8"/>
    `,
  },
  {
    id: 'mustard-seed',
    name: 'Tiny Mustard Seed',
    emoji: '🌱',
    badge: '🔍',
    from: '#365314',
    to: '#bef264',
    svg: `
      <!-- Faint giant tree silhouette in background showing what it grows into -->
      <circle cx="50" cy="36" r="28" fill="#166534" opacity="0.25"/>
      <rect x="46" y="56" width="8" height="30" fill="#166534" opacity="0.25"/>
      <!-- Rich soil mound at bottom -->
      <path d="M12 88 Q50 68 88 88 L88 100 L12 100 Z" fill="#78350f" stroke="#451a03" stroke-width="2"/>
      <!-- Big magnifying glass zooming in on the middle -->
      <line x1="66" y1="64" x2="88" y2="86" stroke="#92400e" stroke-width="8" stroke-linecap="round"/>
      <circle cx="46" cy="46" r="26" fill="#ecfccb" fill-opacity="0.85" stroke="#334155" stroke-width="5"/>
      <circle cx="46" cy="46" r="23" fill="none" stroke="#94a3b8" stroke-width="1.5"/>
      <!-- Tiny sprout & teeny smiling golden seed inside the lens -->
      <path d="M46 46 L46 32" stroke="#16a34a" stroke-width="3" stroke-linecap="round"/>
      <path d="M46 36 C36 30, 36 22, 46 28" fill="#22c55e" stroke="#14532d" stroke-width="1.5"/>
      <path d="M46 34 C56 28, 56 20, 46 26" fill="#4ade80" stroke="#14532d" stroke-width="1.5"/>
      <circle cx="46" cy="51" r="7.5" fill="#eab308" stroke="#713f12" stroke-width="2"/>
      <circle cx="43.5" cy="50" r="1.3" fill="#1e293b"/>
      <circle cx="48.5" cy="50" r="1.3" fill="#1e293b"/>
      <path d="M43.5 53.5 Q46 56 48.5 53.5" fill="none" stroke="#713f12" stroke-width="1.5" stroke-linecap="round"/>
    `,
  },
  {
    id: 'red-sea',
    name: 'Red Sea Shortcut',
    emoji: '🌊',
    badge: '🚶',
    from: '#1e3a8a',
    to: '#f97316',
    svg: `
      <!-- Dry sandy path right down the middle -->
      <polygon points="40,24 60,24 72,100 28,100" fill="#fde68a"/>
      <!-- Footprints walking up the dry seabed -->
      <ellipse cx="47" cy="82" rx="2.5" ry="4" fill="#b45309" opacity="0.65"/>
      <ellipse cx="53" cy="72" rx="2.5" ry="4" fill="#b45309" opacity="0.65"/>
      <ellipse cx="47" cy="62" rx="2.5" ry="4" fill="#b45309" opacity="0.65"/>
      <ellipse cx="53" cy="52" rx="2.5" ry="4" fill="#b45309" opacity="0.65"/>
      <ellipse cx="47" cy="42" rx="2.5" ry="4" fill="#b45309" opacity="0.65"/>
      <!-- Left towering wall of water -->
      <path d="M0 14 Q28 18 36 32 Q30 60 32 100 L0 100 Z" fill="#0284c7" stroke="#0c4a6e" stroke-width="2"/>
      <path d="M8 22 Q24 26 30 34" fill="none" stroke="#e0f2fe" stroke-width="3" stroke-linecap="round"/>
      <!-- Right towering wall of water -->
      <path d="M100 14 Q72 18 64 32 Q70 60 68 100 L100 100 Z" fill="#0284c7" stroke="#0c4a6e" stroke-width="2"/>
      <path d="M92 22 Q76 26 70 34" fill="none" stroke="#e0f2fe" stroke-width="3" stroke-linecap="round"/>
      <!-- Surprised orange fish pressed against the water wall like an aquarium! -->
      <ellipse cx="20" cy="58" rx="8" ry="5" fill="#fb923c" stroke="#7c2d12" stroke-width="1.5"/>
      <polygon points="12,58 6,53 6,63" fill="#f97316"/>
      <circle cx="24" cy="57" r="2.2" fill="#ffffff"/>
      <circle cx="25" cy="57" r="1" fill="#0f172a"/>
    `,
  },
  {
    id: 'star-bethlehem',
    name: 'Star of Bethlehem',
    emoji: '⭐',
    badge: '🐫',
    from: '#1e1b4b',
    to: '#6366f1',
    svg: `
      <!-- Light beam shining down from the star -->
      <polygon points="50,28 22,96 78,96" fill="#fef08a" opacity="0.22"/>
      <!-- Twinkling background dots -->
      <circle cx="20" cy="22" r="1.8" fill="#ffffff"/>
      <circle cx="82" cy="20" r="2" fill="#fde047"/>
      <circle cx="18" cy="48" r="1.5" fill="#c7d2fe"/>
      <circle cx="84" cy="44" r="1.5" fill="#ffffff"/>
      <!-- Blazing 8-point Star of Bethlehem -->
      <polygon points="50,6 55,22 72,26 55,30 50,48 45,30 28,26 45,22" fill="#fde047" stroke="#f59e0b" stroke-width="1.5"/>
      <polygon points="50,14 53,23 62,26 53,29 50,38 47,29 38,26 47,23" fill="#ffffff"/>
      <!-- Cool desert camel at bottom looking up in sunglasses -->
      <path d="M26 94 L30 74 Q30 62 42 64 Q48 54 56 64 L62 66 L66 52 L78 52 C82 52, 82 60, 76 60 L72 60 L68 78 L66 94 Z" fill="#d97706" stroke="#451a03" stroke-width="2.2" stroke-linejoin="round"/>
      <rect x="70" y="51" width="9" height="4.5" rx="1" fill="#0f172a" stroke="#facc15" stroke-width="1.2"/>
    `,
  },
  {
    id: 'water-wine',
    name: 'Water into Wine',
    emoji: '🍇',
    badge: '🫙',
    from: '#4a044e',
    to: '#e879f9',
    svg: `
      <!-- Tilted terracotta stone jar at top-left -->
      <g transform="translate(32,30) rotate(48)">
        <ellipse cx="0" cy="4" rx="14" ry="17" fill="#c2410c" stroke="#431407" stroke-width="2.2"/>
        <rect x="-7" y="-17" width="14" height="7" rx="2" fill="#ea580c" stroke="#431407" stroke-width="2"/>
        <path d="M-14 0 C-22 0, -22 10, -14 10" fill="none" stroke="#9a3412" stroke-width="3"/>
      </g>
      <!-- Swirling stream transforming from blue water to ruby-purple wine! -->
      <path d="M46 38 C56 44, 50 56, 56 66" fill="none" stroke="#38bdf8" stroke-width="8" stroke-linecap="round"/>
      <path d="M53 54 C58 64, 52 74, 56 84" fill="none" stroke="#9333ea" stroke-width="9" stroke-linecap="round"/>
      <!-- Golden goblet catching the wine at bottom -->
      <path d="M40 72 H72 C72 86, 62 90, 56 90 C50 90, 40 86, 40 72 Z" fill="#facc15" stroke="#78350f" stroke-width="2.2"/>
      <ellipse cx="56" cy="73" rx="14" ry="3.5" fill="#7e22ce"/>
      <!-- Cluster of plump purple grapes in top-right -->
      <circle cx="74" cy="24" r="5.5" fill="#9333ea" stroke="#3b0764" stroke-width="1.5"/>
      <circle cx="83" cy="24" r="5.5" fill="#a855f7" stroke="#3b0764" stroke-width="1.5"/>
      <circle cx="78.5" cy="31" r="5.5" fill="#7e22ce" stroke="#3b0764" stroke-width="1.5"/>
      <circle cx="70" cy="31" r="5" fill="#9333ea" stroke="#3b0764" stroke-width="1.5"/>
      <circle cx="74" cy="38" r="5" fill="#a855f7" stroke="#3b0764" stroke-width="1.5"/>
      <path d="M76 18 Q80 12 84 16" fill="none" stroke="#22c55e" stroke-width="2.5" stroke-linecap="round"/>
    `,
  },
  {
    id: 'walking-water',
    name: 'Walking on Water',
    emoji: '🦶',
    badge: '🌊',
    from: '#0c4a6e',
    to: '#38bdf8',
    svg: `
      <!-- Deep sea water and glowing concentric surface ripples -->
      <rect x="0" y="50" width="100" height="50" fill="#0369a1"/>
      <ellipse cx="38" cy="58" rx="18" ry="5" fill="none" stroke="#bae6fd" stroke-width="2.2"/>
      <ellipse cx="64" cy="64" rx="20" ry="6" fill="none" stroke="#e0f2fe" stroke-width="2.5"/>
      <ellipse cx="64" cy="64" rx="12" ry="3.5" fill="none" stroke="#fef08a" stroke-width="1.8"/>
      <!-- Two sandal-clad feet strolling casually RIGHT ON TOP of the water -->
      <path d="M26 16 L30 48 L46 52 C50 52, 50 57, 44 57 L26 56 L22 16 Z" fill="#fed7aa" stroke="#431407" stroke-width="2" stroke-linejoin="round"/>
      <path d="M26 55 H47" stroke="#78350f" stroke-width="3.5" stroke-linecap="round"/>
      <path d="M30 49 L38 55 M36 49 L43 55" stroke="#92400e" stroke-width="2"/>
      <path d="M54 16 L58 54 L76 58 C80 58, 80 63, 74 63 L54 62 L50 16 Z" fill="#fdba74" stroke="#431407" stroke-width="2" stroke-linejoin="round"/>
      <path d="M54 61 H77" stroke="#78350f" stroke-width="3.5" stroke-linecap="round"/>
      <path d="M58 55 L66 61 M64 55 L71 61" stroke="#92400e" stroke-width="2"/>
      <!-- Shocked little fish poking its head out at bottom-left -->
      <ellipse cx="26" cy="78" rx="9" ry="6" transform="rotate(-20 26 78)" fill="#fbbf24" stroke="#78350f" stroke-width="1.8"/>
      <circle cx="30" cy="75" r="2.8" fill="#ffffff"/>
      <circle cx="30.5" cy="75" r="1.3" fill="#0f172a"/>
      <circle cx="34" cy="78" r="2" fill="#78350f"/>
    `,
  },
  {
    id: 'gideons-fleece',
    name: "Gideon's Fleece",
    emoji: '🐏',
    badge: '💧',
    from: '#475569',
    to: '#38bdf8',
    svg: `
      <!-- Bone-dry cracked desert ground underneath -->
      <path d="M8 78 Q50 68 92 78 L92 100 L8 100 Z" fill="#d97706" stroke="#78350f" stroke-width="2"/>
      <polyline points="22,84 30,89 26,95" fill="none" stroke="#78350f" stroke-width="1.8"/>
      <polyline points="72,82 66,88 74,94" fill="none" stroke="#78350f" stroke-width="1.8"/>
      <!-- Fluffy golden-white wool fleece sitting in the middle -->
      <circle cx="32" cy="54" r="13" fill="#f8fafc" stroke="#475569" stroke-width="2"/>
      <circle cx="68" cy="54" r="13" fill="#f8fafc" stroke="#475569" stroke-width="2"/>
      <circle cx="40" cy="42" r="14" fill="#ffffff" stroke="#475569" stroke-width="2"/>
      <circle cx="60" cy="42" r="14" fill="#ffffff" stroke="#475569" stroke-width="2"/>
      <circle cx="42" cy="60" r="13" fill="#f1f5f9" stroke="#475569" stroke-width="2"/>
      <circle cx="58" cy="60" r="13" fill="#f1f5f9" stroke="#475569" stroke-width="2"/>
      <circle cx="50" cy="50" r="15" fill="#ffffff"/>
      <!-- Giant splash of bright blue dew droplets wringing out of the fleece -->
      <path d="M50 14 C44 24, 44 30, 50 30 C56 30, 56 24, 50 14 Z" fill="#38bdf8" stroke="#0369a1" stroke-width="1.8"/>
      <path d="M24 26 C19 34, 19 39, 24 39 C29 39, 29 34, 24 26 Z" fill="#7dd3fc" stroke="#0369a1" stroke-width="1.8"/>
      <path d="M76 26 C71 34, 71 39, 76 39 C81 39, 81 34, 76 26 Z" fill="#7dd3fc" stroke="#0369a1" stroke-width="1.8"/>
      <path d="M40 70 C36 78, 36 83, 40 83 C44 83, 44 78, 40 70 Z" fill="#38bdf8" stroke="#0284c7" stroke-width="1.8"/>
      <path d="M58 72 C54 80, 54 85, 58 85 C62 85, 62 80, 58 72 Z" fill="#38bdf8" stroke="#0284c7" stroke-width="1.8"/>
    `,
  },
  {
    id: 'ravens-delivery',
    name: "Elijah's Raven Delivery",
    emoji: '🐦‍⬛',
    badge: '🥪',
    from: '#18181b',
    to: '#6366f1',
    svg: `
      <!-- Speed lines behind the express courier raven -->
      <line x1="8" y1="36" x2="26" y2="36" stroke="#a5b4fc" stroke-width="2.5" stroke-linecap="round"/>
      <line x1="4" y1="48" x2="20" y2="48" stroke="#a5b4fc" stroke-width="2.5" stroke-linecap="round"/>
      <line x1="10" y1="60" x2="24" y2="60" stroke="#a5b4fc" stroke-width="2.5" stroke-linecap="round"/>
      <!-- Sleek glossy black raven in mid-flight -->
      <path d="M22 48 C18 26, 38 16, 48 34 C44 38, 34 42, 22 48 Z" fill="#312e81" stroke="#09090b" stroke-width="2"/>
      <ellipse cx="44" cy="48" rx="18" ry="12" fill="#18181b" stroke="#09090b" stroke-width="2.2"/>
      <polygon points="26,48 12,42 15,54" fill="#18181b" stroke="#09090b" stroke-width="2"/>
      <circle cx="58" cy="40" r="11" fill="#18181b" stroke="#09090b" stroke-width="2.2"/>
      <!-- Cute red courier cap on the raven -->
      <path d="M50 32 C52 22, 66 22, 68 32 Z" fill="#ef4444" stroke="#7f1d1d" stroke-width="1.8"/>
      <path d="M58 31 H74" stroke="#ef4444" stroke-width="3" stroke-linecap="round"/>
      <!-- Bright bird eye & yellow beak -->
      <circle cx="61" cy="39" r="3" fill="#ffffff"/>
      <circle cx="62" cy="39" r="1.5" fill="#09090b"/>
      <polygon points="67,38 84,42 67,46" fill="#facc15" stroke="#78350f" stroke-width="1.8" stroke-linejoin="round"/>
      <!-- Giant stacked sandwich hanging from beak! -->
      <g transform="translate(74,64)">
        <rect x="-14" y="-12" width="28" height="7" rx="3" fill="#f59e0b" stroke="#78350f" stroke-width="1.8"/>
        <path d="M-15 -5 Q-10 -2 -5 -5 T5 -5 T15 -5" fill="none" stroke="#22c55e" stroke-width="3.5" stroke-linecap="round"/>
        <rect x="-13" y="-3" width="26" height="4" fill="#ef4444"/>
        <polygon points="-12,1 12,1 8,5 -8,5" fill="#fde047"/>
        <rect x="-14" y="3" width="28" height="7" rx="3" fill="#f59e0b" stroke="#78350f" stroke-width="1.8"/>
      </g>
    `,
  },
];

const BY_ID = new Map(AVATAR_PRESETS.map((p) => [p.id, p]));

export function presetFor(avatar: string | null | undefined): AvatarPreset | null {
  if (!avatar?.startsWith('preset:')) return null;
  return BY_ID.get(avatar.slice('preset:'.length)) ?? null;
}

/**
 * Renders a preset's custom SVG illustration as a self-contained <svg> string.
 * Includes a hidden <desc> with the preset's emoji + badge so text queries and
 * screen-reader fallbacks continue to work seamlessly.
 */
export function presetSvgHtml(preset: AvatarPreset): string {
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100%" height="100%" aria-hidden="true" style="display:block;width:100%;height:100%">` +
    `<desc>${preset.emoji} ${preset.badge}</desc>` +
    `${preset.svg}` +
    `</svg>`
  );
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
