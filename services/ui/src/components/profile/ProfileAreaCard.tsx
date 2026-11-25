import { Link } from 'react-router-dom';
import {
  BookOpen, ChefHat, Flower, Footprints, Gamepad2, HeartPulse, Sparkles, Trophy, Crown, type LucideIcon,
} from 'lucide-react';
import MemberCard from '../member/MemberCard';
import { StatRow, StatTile, TileGrid } from './StatTile';
import {
  AREA_HOME, AREA_TITLES, bibleQuizLink, bibleReaderLink, firstName, gameLink, minutesText, plural, recipeLink, linkForUse,
  type ActivityData, type BibleData, type GamesData, type GardenData, type HealthData, type ProfileArea, type ProfileCard,
  type RecipesData, type UseData,
} from '../../lib/profileStats';

const ICONS: Record<ProfileArea, { icon: LucideIcon; tone: string }> = {
  bible: { icon: BookOpen, tone: 'text-amber-300' },
  activity: { icon: Footprints, tone: 'text-emerald-300' },
  health: { icon: HeartPulse, tone: 'text-rose-300' },
  garden: { icon: Flower, tone: 'text-lime-300' },
  recipes: { icon: ChefHat, tone: 'text-orange-300' },
  games: { icon: Gamepad2, tone: 'text-sky-300' },
  jarvis_use: { icon: Sparkles, tone: 'text-purple-300' },
};

const EMPTY: Record<ProfileArea, string> = {
  bible: 'No reading recorded yet.',
  activity: 'No steps or workouts this week yet.',
  health: 'Nothing logged in Health this week.',
  garden: 'No plant care logged yet.',
  recipes: 'No recipes cooked or rated yet.',
  games: 'No game scores yet.',
  jarvis_use: 'Nothing recorded in the last 30 days.',
};

interface Who { username: string; name: string; own: boolean }

/** One area of the profile page in its card, with every stat a way into it. */
export default function ProfileAreaCard({ card, who }: { card: ProfileCard; who: Who }) {
  const { icon, tone } = ICONS[card.area];
  const open = (
    <Link
      to={AREA_HOME[card.area]}
      className="inline-flex min-h-11 items-center rounded-lg px-2 text-xs text-slate-300 hover:bg-white/5 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-purple-400/70"
      data-testid={`profile-open-${card.area}`}
    >
      Open
    </Link>
  );
  return (
    <MemberCard
      title={AREA_TITLES[card.area]}
      icon={icon}
      tone={tone}
      testId={`profile-card-${card.area}`}
      aside={open}
      wide={card.area === 'games'}
      error={card.state === 'unavailable' ? card.reason || 'This part of Jarvis is not answering just now.' : null}
      empty={card.state === 'empty' && !hasContent(card) ? EMPTY[card.area] : null}
    >
      <AreaBody card={card} who={who} />
    </MemberCard>
  );
}

function hasContent(card: ProfileCard): boolean {
  if (card.area === 'games') {
    const g = card.data as GamesData | null;
    return Boolean(g && (g.games.length || g.bible_quiz_today));
  }
  return false;
}

function AreaBody({ card, who }: { card: ProfileCard; who: Who }) {
  switch (card.area) {
    case 'bible': return <BibleBody data={card.data as unknown as BibleData} who={who} />;
    case 'activity': return <ActivityBody data={card.data as ActivityData} who={who} />;
    case 'health': return <HealthBody data={card.data as HealthData} who={who} />;
    case 'garden': return <GardenBody data={card.data as unknown as GardenData} />;
    case 'recipes': return <RecipesBody data={card.data as unknown as RecipesData} who={who} />;
    case 'games': return <GamesBody data={card.data as unknown as GamesData} who={who} />;
    case 'jarvis_use': return <UseBody data={card.data as unknown as UseData} />;
    default: return null;
  }
}

function quizTile(score: BibleData['quiz_today'], who: Who, testId: string) {
  const challenge = who.own ? null : firstName(who.name);
  return (
    <StatTile
      to={bibleQuizLink(score, challenge)}
      testId={testId}
      value={score ? `${score.correct}/${score.total}` : 'Not yet'}
      label="Daily Bible quiz today"
      hint={score && !who.own ? `Beat ${challenge}'s score` : who.own && !score ? 'Take today\'s quiz' : undefined}
      accent="text-amber-200"
    />
  );
}

function BibleBody({ data, who }: { data: BibleData; who: Who }) {
  const days = data.window ?? [];
  const max = Math.max(1, ...days.map((d) => d.count));
  return (
    <div className="space-y-3">
      <TileGrid>
        <StatTile to={bibleReaderLink(who.own ? data.reference : null)} testId="profile-bible-streak"
          value={plural(data.streak_current, 'day')} label="Reading streak"
          hint={data.streak_longest ? `Best ${plural(data.streak_longest, 'day')}` : undefined} accent="text-amber-200" />
        <StatTile to="/bible?tab=progress" value={data.days_read.toLocaleString()} label="Days read" />
        <StatTile to="/bible?tab=progress" value={data.chapters_read.toLocaleString()} label="Chapters read"
          hint={data.books_read ? plural(data.books_read, 'book') + ' finished' : undefined} />
        {quizTile(data.quiz_today, who, 'profile-bible-quiz')}
        <StatTile to="/bible?tab=progress" value={data.points.toLocaleString()} label="Bible points"
          hint={data.achievements.length ? plural(data.achievements.length, 'badge') : undefined} />
        {who.own && data.reference ? (
          <StatTile to={bibleReaderLink(data.reference)} value={data.reference} label="Pick up where you left off" />
        ) : null}
      </TileGrid>
      {days.length > 0 && (
        <Link to="/bible?tab=progress" aria-label={`Reading over the last ${days.length} days`}
          className="block min-h-11 rounded-xl px-1 py-2 hover:bg-white/[0.04] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-purple-400/70">
          <div className="flex h-10 items-end gap-[3px]" aria-hidden>
            {days.map((d) => (
              <span key={d.day} title={`${d.day}: ${d.count}`}
                className="flex-1 rounded-sm bg-amber-300/70" style={{ height: `${Math.max(8, (d.count / max) * 100)}%` }} />
            ))}
          </div>
          <span className="mt-1 block text-[11px] text-slate-500">Reading, last {days.length} active days</span>
        </Link>
      )}
      {data.achievements.length > 0 && (
        <p className="text-xs text-slate-400">
          Latest badges: {data.achievements.slice(-3).join(', ')}
        </p>
      )}
    </div>
  );
}

function ActivityBody({ data, who }: { data: ActivityData; who: Who }) {
  const tiles = [];
  if (data.steps_today != null) tiles.push(<StatTile key="today" to="/fitness?tab=fitness" testId="profile-steps-today" value={data.steps_today.toLocaleString()} label="Steps today" accent="text-emerald-200" />);
  if (data.steps_average != null) tiles.push(<StatTile key="avg" to="/fitness?tab=fitness" value={Math.round(data.steps_average).toLocaleString()} label="Steps a day this week" />);
  if (data.workout_count != null) tiles.push(<StatTile key="wk" to="/fitness?tab=fitness" value={data.workout_count} label="Workouts this week" />);
  if (data.workout_distance_miles) tiles.push(<StatTile key="wd" to="/fitness?tab=fitness" value={`${data.workout_distance_miles.toFixed(1)} mi`} label="Walked, run or ridden" />);
  if (data.drive_distance_miles != null) tiles.push(<StatTile key="dr" to={`/wander?person=${encodeURIComponent(who.username)}`} value={`${Math.round(data.drive_distance_miles)} mi`} label="Driven this week" hint="Open in Wander" />);
  if (data.points != null) tiles.push(<StatTile key="pts" to="/fitness?tab=fitness" value={data.points.toLocaleString()} label="Activity points" hint={data.achievements_earned ? plural(data.achievements_earned, 'achievement') : undefined} />);
  if (!tiles.length) return <p className="text-xs text-slate-400">Nothing shared here yet.</p>;
  return <TileGrid>{tiles}</TileGrid>;
}

function HealthBody({ data, who }: { data: HealthData; who: Who }) {
  if (!who.own) {
    const tiles = [];
    if (data.sleep) tiles.push(<StatTile key="sleep" to="/fitness?tab=sleep" value={data.sleep.logged ? minutesText(data.sleep.duration_minutes) : 'Not logged'} label="Sleep last night" hint={data.sleep.label ?? undefined} accent="text-rose-200" />);
    if (data.nutrition) tiles.push(<StatTile key="food" to="/fitness?tab=nutrition" value={data.nutrition.logged ? 'Logged' : 'Not yet'} label="Meals today" hint={data.nutrition.calories != null ? `${Math.round(data.nutrition.calories)} kcal` : undefined} />);
    if (data.fasting) tiles.push(<StatTile key="fast" to="/fitness" value="Fasting" label="Shared fasting" />);
    return tiles.length ? <TileGrid>{tiles}</TileGrid> : <p className="text-xs text-slate-400">Shared, nothing logged today.</p>;
  }
  const habits = data.habits ?? [];
  const week = data.week;
  return (
    <div className="space-y-3">
      <TileGrid>
        {habits.map((h) => (
          <StatTile key={h.key} to="/fitness" testId={`profile-habit-${h.key}`} value={plural(h.current, 'day')} label={h.title}
            hint={h.best ? `Best ${h.best}` : undefined} accent={h.today_done ? 'text-rose-200' : 'text-white'} />
        ))}
        {week?.averages?.sleep_minutes != null && (
          <StatTile to="/fitness?tab=sleep" value={minutesText(week.averages.sleep_minutes)} label="Average sleep this week" />
        )}
        {week && (
          <StatTile to="/fitness" value={`${week.days_logged}/7`} label="Days logged this week" />
        )}
      </TileGrid>
    </div>
  );
}

function GardenBody({ data }: { data: GardenData }) {
  return (
    <div className="space-y-3">
      <TileGrid>
        <StatTile to="/garden" testId="profile-garden-streak" value={plural(data.streak_current, 'day')} label="Plant care streak"
          hint={data.streak_longest ? `Best ${plural(data.streak_longest, 'day')}` : undefined} accent="text-lime-200" />
        <StatTile to="/garden" value={data.care_logs.toLocaleString()} label="Care logged" />
        <StatTile to="/garden" value={data.points.toLocaleString()} label="Garden points" hint={data.achievements.length ? plural(data.achievements.length, 'badge') : undefined} />
      </TileGrid>
      {data.achievements.length > 0 && <p className="text-xs text-slate-400">Badges: {data.achievements.slice(-4).join(', ')}</p>}
    </div>
  );
}

function RecipesBody({ data, who }: { data: RecipesData; who: Who }) {
  const lead = who.own ? 'You' : firstName(who.name);
  return (
    <div className="space-y-3">
      <TileGrid>
        <StatTile to="/recipes" value={data.times_cooked.toLocaleString()} label="Times cooked" accent="text-orange-200" />
        <StatTile to="/recipes" value={data.recipes_cooked.toLocaleString()} label="Different recipes" />
        <StatTile to="/recipes" value={data.ratings_given.toLocaleString()} label="Recipes rated" />
      </TileGrid>
      {data.most_made.length > 0 && (
        <div>
          <p className="px-1 pb-1 text-[11px] font-medium uppercase tracking-wider text-slate-500">{lead} cook{who.own ? '' : 's'} these most</p>
          <div className="space-y-0.5">
            {data.most_made.map((r) => (
              <StatRow key={r.id} to={recipeLink(r.id)} testId={`profile-recipe-${r.id}`} title={r.title}
                detail={`${plural(r.made_by_me, 'time')}${r.mine ? `, ${r.mine} stars` : ''}`} />
            ))}
          </div>
        </div>
      )}
      {data.favourites.length > 0 && (
        <p className="text-xs text-slate-400">Five stars: {data.favourites.map((r) => r.title).join(', ')}</p>
      )}
    </div>
  );
}

function Rank({ rank }: { rank: number | null }) {
  if (rank == null) return <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-white/5 text-xs text-slate-400">-</span>;
  const tone = rank === 1 ? 'bg-amber-400/20 text-amber-200' : rank <= 3 ? 'bg-sky-400/15 text-sky-200' : 'bg-white/5 text-slate-300';
  return (
    <span className={`flex h-8 w-8 items-center justify-center rounded-lg text-xs font-semibold ${tone}`} aria-label={`Rank ${rank}`}>
      {rank === 1 ? <Crown size={14} aria-hidden /> : `#${rank}`}
    </span>
  );
}

function GamesBody({ data, who }: { data: GamesData; who: Who }) {
  const name = firstName(who.name);
  return (
    <div className="space-y-3">
      <TileGrid>
        {quizTile(data.bible_quiz_today, who, 'profile-games-quiz')}
        <StatTile to="/family?tab=games" value={data.crowns.toLocaleString()} label="Games won outright" hint={data.podiums ? `${plural(data.podiums, 'podium')}` : undefined} accent="text-sky-200" />
        <StatTile to="/family?tab=games" value={data.best_score.toLocaleString()} label="Best score" />
      </TileGrid>
      {data.arcade_unavailable && <p className="text-xs text-amber-300">The arcade is not answering, so game scores are missing for now.</p>}
      {data.games.length > 0 && (
        <div className="grid gap-0.5 md:grid-cols-2">
          {data.games.map((g) => (
            <StatRow
              key={`${g.collection}-${g.slug}`}
              to={gameLink(g)}
              testId={`profile-game-${g.slug}`}
              badge={<Rank rank={g.rank} />}
              title={g.title}
              detail={`Best ${g.best.toLocaleString()} in ${plural(g.plays, 'play')}${!who.own ? `. Beat ${name}'s score` : ''}`}
              ariaLabel={`${g.title}: ${who.own ? 'your' : `${name}'s`} best is ${g.best}. Open the game`}
            />
          ))}
        </div>
      )}
      {!data.games.length && !data.arcade_unavailable && (
        <p className="flex items-center gap-1.5 text-xs text-slate-400"><Trophy size={12} aria-hidden />No arcade scores yet.</p>
      )}
    </div>
  );
}

function UseBody({ data }: { data: UseData }) {
  const max = Math.max(1, ...data.top.map((t) => t.count));
  return (
    <div className="space-y-1">
      <p className="px-1 text-xs text-slate-400">{plural(data.total, 'thing')} done in Jarvis over {data.days} days</p>
      {data.top.map((t) => {
        const to = linkForUse(t.label);
        const bar = (
          <span className="mt-1 block h-1.5 rounded-full bg-white/5" aria-hidden>
            <span className="block h-full rounded-full bg-purple-400/70" style={{ width: `${(t.count / max) * 100}%` }} />
          </span>
        );
        return to ? (
          <StatRow key={t.label} to={to} title={t.label} detail={<>{plural(t.count, 'time')}{bar}</>} />
        ) : (
          <div key={t.label} className="px-2.5 py-2 text-sm text-slate-100">{t.label}<span className="block text-[11px] text-slate-400">{plural(t.count, 'time')}</span>{bar}</div>
        );
      })}
    </div>
  );
}
