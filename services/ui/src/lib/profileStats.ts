/**
 * Profile stats (docs/PROFILE_STATS.md): the shapes the gateway answers with,
 * and where each stat leads. Every stat on the page is a link into the page
 * that already does that thing (the reader, the quiz, a recipe, a game); this
 * file is the one place those links are built, so the page never invents a
 * parallel screen.
 */

export type ProfileArea = 'bible' | 'activity' | 'health' | 'garden' | 'recipes' | 'games' | 'jarvis_use';
export type CardState = 'ok' | 'empty' | 'hidden' | 'unavailable';
export type ShareArea = 'garden' | 'recipes' | 'jarvis_use';

export interface ProfileCard<T = Record<string, unknown>> {
  area: ProfileArea;
  state: CardState;
  data: T | null;
  reason?: string;
}

export interface QuizScore { correct: number; total: number; duration_ms?: number; day?: string | null }

export interface BibleData {
  streak_current: number;
  streak_longest: number;
  days_read: number;
  chapters_read: number;
  books_read: number;
  last_read_at?: string | null;
  reference?: string | null;
  window?: Array<{ day: string; count: number }>;
  achievements: string[];
  points: number;
  quiz_today: QuizScore | null;
}

export interface ActivityData {
  window?: string;
  steps_today?: number;
  steps_average?: number;
  steps_total?: number;
  workout_count?: number;
  workout_distance_miles?: number;
  drive_distance_miles?: number;
  points?: number;
  achievements_earned?: number;
}

export interface HealthHabit { key: string; title: string; current: number; best: number; today_done: boolean }
export interface HealthData {
  habits?: HealthHabit[];
  week?: { days_logged: number; averages: Record<string, number | null>; changes: Array<{ metric: string; direction: string; delta?: number }>; best_sleep?: { day: string; minutes?: number | null; score?: number | null } | null };
  scopes?: string[];
  sleep?: { duration_minutes?: number | null; score?: number | null; label?: string | null; logged?: boolean };
  nutrition?: { logged?: boolean; calories?: number | null };
  fasting?: Record<string, unknown> | null;
}

export interface GardenData { streak_current: number; streak_longest: number; care_logs: number; achievements: string[]; points: number }

export interface RecipeStat { id: string; title: string; made_by_me: number; mine: number | null; rating: number | null; last_made?: string | null }
export interface RecipesData { times_cooked: number; recipes_cooked: number; ratings_given: number; most_made: RecipeStat[]; favourites: RecipeStat[] }

export interface GameStat {
  slug: string; title: string; collection: 'family' | 'arcade'; best: number; rank: number | null; plays: number;
  top_score?: number | null; top_initials?: string | null;
}
export interface GamesData {
  best_score: number; crowns: number; podiums: number; total_games: number; games: GameStat[];
  bible_quiz_today: QuizScore | null; arcade_unavailable?: boolean;
}

export interface UseData { days: number; total: number; top: Array<{ label: string; count: number }>; window_start?: string }

export interface SharingState {
  areas: ShareArea[] | null;
  available: ShareArea[];
  activity_sharing: { enabled: boolean; audience: string; share: string[] } | null;
  health_shares: Array<{ viewer: string; scope: string }> | null;
}

export interface ProfileStats {
  username: string;
  own: boolean;
  person: { username: string; display_name: string };
  cards: ProfileCard[];
  sharing?: SharingState | null;
}

export const AREA_TITLES: Record<ProfileArea, string> = {
  bible: 'Bible',
  activity: 'Activity and Wander',
  health: 'Health',
  garden: 'Garden',
  recipes: 'Recipes',
  games: 'Games and quizzes',
  jarvis_use: 'Using Jarvis',
};

/** Where an area's card header leads: the page that owns it. */
export const AREA_HOME: Record<ProfileArea, string> = {
  bible: '/bible?tab=progress',
  activity: '/fitness?tab=fitness',
  health: '/fitness',
  garden: '/garden',
  recipes: '/recipes',
  games: '/family?tab=games',
  jarvis_use: '/',
};

/** The areas a person turns on here, with what each lets the family see. */
export const SHARE_AREA_TEXT: Record<ShareArea, { title: string; detail: string }> = {
  garden: { title: 'Garden', detail: 'Care streaks, plants cared for and garden badges.' },
  recipes: { title: 'Recipes', detail: 'What you cook most and the recipes you rated highest.' },
  jarvis_use: { title: 'Using Jarvis', detail: 'Which parts of Jarvis you use most (counts only).' },
};

/** The reader at a place ("John 3"), or the Bible page. */
export function bibleReaderLink(reference?: string | null): string {
  return reference ? `/bible?tab=read&ref=${encodeURIComponent(reference)}` : '/bible?tab=read';
}

/**
 * Today's daily quiz. Someone else's score turns it into a challenge: the
 * quiz page reads `beat`, `score` and `of` and offers "Beat Robin's 8/10".
 */
export function bibleQuizLink(score?: QuizScore | null, challenger?: string | null): string {
  if (!score || !challenger) return '/bible?tab=play';
  const q = new URLSearchParams({ tab: 'play', beat: challenger, score: String(score.correct), of: String(score.total) });
  return `/bible?${q.toString()}`;
}

export function recipeLink(id: string): string {
  return `/recipes?recipe=${encodeURIComponent(id)}`;
}

/** A Family Game opens on the family shelf directly; an arcade game on the games tab. */
export function gameLink(game: Pick<GameStat, 'slug' | 'collection'>): string {
  return game.collection === 'family'
    ? `/family?tab=games&play=${encodeURIComponent(game.slug)}`
    : '/family?tab=games';
}

const USE_PAGES: Record<string, string> = {
  'Music, video and podcasts': '/media',
  Bible: '/bible',
  Games: '/family?tab=games',
  Recipes: '/recipes',
  Notes: '/notes',
  Calendar: '/calendar',
  'Family and chat': '/family',
  Health: '/fitness',
  Dashboard: '/',
  Map: '/wander',
  Remote: '/remote',
  Settings: '/settings',
};

/** A "used most" label (services/common/jarvis_use.py GROUPS) to its page, or null. */
export function linkForUse(label: string): string | null {
  return USE_PAGES[label] ?? null;
}

/** The challenge a quiz link carries, read back on the quiz page. */
export interface QuizChallenge { name: string; score: number; of: number }

export function readQuizChallenge(params: URLSearchParams): QuizChallenge | null {
  const name = (params.get('beat') || '').trim().slice(0, 40);
  const score = Number(params.get('score'));
  const of = Number(params.get('of'));
  if (!name || !Number.isInteger(score) || !Number.isInteger(of) || of <= 0 || score < 0 || score > of) return null;
  return { name, score, of };
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n.toLocaleString()} ${n === 1 ? one : many}`;
}

export function minutesText(minutes?: number | null): string {
  if (minutes == null) return '-';
  const h = Math.floor(minutes / 60);
  const m = Math.round(minutes % 60);
  return h ? `${h} h ${m} min` : `${m} min`;
}

/** First name for a challenge ("Robin" from "Robin Lee"). */
export function firstName(display: string): string {
  return (display || '').trim().split(/\s+/)[0] || display;
}

export function cardOf<T>(stats: ProfileStats | undefined, area: ProfileArea): ProfileCard<T> | undefined {
  return stats?.cards.find((c) => c.area === area) as ProfileCard<T> | undefined;
}
