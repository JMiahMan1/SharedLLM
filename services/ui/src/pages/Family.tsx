import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Brain, Brush, ChevronRight, Crown, Gamepad2, MessageCircle } from 'lucide-react';
import ChatPanel from '../components/chat/ChatPanel';
import ArcadeGames from '../components/family/ArcadeGames';
import DrawCanvas from '../components/family/DrawCanvas';
import MakeMusic from '../components/family/MakeMusic';
import Recipes from './Recipes';

type Tab = 'chat' | 'games' | 'create';

/** The Bible games that exist today, one tap away from the family hub. */
const BIBLE_GAMES = [
  { icon: Crown, name: 'Daily Bible Quiz', detail: 'Ten questions, the same for the whole family, with a leaderboard', to: '/bible?tab=play' },
  { icon: Gamepad2, name: 'Bible games', detail: 'Verse Race, Which Book?, Fill the Gap, What Comes Next?', to: '/bible?tab=play' },
  { icon: Brain, name: 'Memory verses', detail: 'Learn verses by heart with spaced review', to: '/bible?tab=memorize' },
];

const TABS: Array<{ id: Tab; label: string; icon: typeof MessageCircle }> = [
  { id: 'chat', label: 'Chat', icon: MessageCircle },
  { id: 'games', label: 'Games', icon: Gamepad2 },
  { id: 'create', label: 'Create', icon: Brush },
];

/**
 * Family hub: chat, games and making things together.
 *
 * Chat is Nextcloud Talk (the same rooms as the Nextcloud app). Games are the
 * arcade and the Bible games; Create is drawing, songs and the family
 * cookbook, and a drawing or a song can be sent straight into a conversation.
 * See docs/FAMILY_HUB.md.
 */
export default function Family() {
  const [tab, setTab] = useState<Tab>('chat');

  return (
    <div
      // On a phone the chat tab is exactly the shell's height and only the
      // conversation list scrolls; the other tabs scroll the page as usual.
      className={`mx-auto flex max-w-6xl flex-col gap-3 px-1 sm:gap-4 sm:px-0 ${
        tab === 'chat' ? 'max-lg:h-full max-lg:min-h-0 lg:pb-28' : 'pb-28'
      }`}
      data-testid="family-page"
    >
      {/* One compact row, so the conversations get the screen. */}
      {/* The tabs sit beside the title when they fit at full size and wrap to
          a full-width row of their own when they do not (a large system font
          on a narrow phone ran them past the right edge). They never shrink
          their labels to "Ch…". */}
      <div className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-2 px-1">
        <h1 className="shrink-0 text-2xl font-bold tracking-tight text-white">Family</h1>
        <div
          className="flex grow justify-end"
        >
        <div
          className="flex w-full max-w-sm rounded-full border border-white/10 bg-slate-900/80 p-1 sm:w-auto"
          role="tablist"
          aria-label="Family sections"
        >
          {TABS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              role="tab"
              aria-selected={tab === id}
              onClick={() => setTab(id)}
              className={`flex min-h-11 flex-1 items-center justify-center gap-1.5 whitespace-nowrap rounded-full px-3 text-[13px] font-medium transition ${
                tab === id ? 'bg-gradient-to-br from-purple-500 to-indigo-600 text-white shadow' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              <Icon size={14} className="shrink-0" /> {label}
            </button>
          ))}
        </div>
        </div>
      </div>

      {tab === 'chat' && <ChatPanel onOpenTab={setTab} />}

      {tab === 'games' && (
        <div className="space-y-4" data-testid="family-games">
          <ArcadeGames />
          <div className="grid gap-3 sm:grid-cols-2">
            {BIBLE_GAMES.map(({ icon: Icon, name, detail, to }) => (
              <Link
                key={name}
                to={to}
                className="glass-panel flex items-start gap-3 rounded-2xl border border-white/5 p-4 hover:border-purple-400/40"
              >
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-purple-500/15 text-purple-200">
                  <Icon size={18} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block font-semibold text-slate-100">{name}</span>
                  <span className="mt-1 block text-xs text-slate-400">{detail}</span>
                </span>
                <ChevronRight size={16} className="mt-1 shrink-0 text-slate-500" />
              </Link>
            ))}
          </div>
        </div>
      )}

      {tab === 'create' && (
        <div className="space-y-4" data-testid="family-create">
          <p className="px-1 text-xs text-slate-400">
            Draw, make a song or keep a recipe. Drawings and songs can be sent straight into a conversation.
          </p>
          <DrawCanvas />
          <MakeMusic />
          <Recipes />
        </div>
      )}
    </div>
  );
}
