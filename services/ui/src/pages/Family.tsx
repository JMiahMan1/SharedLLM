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
 * Chat is live on Nextcloud Talk (the same rooms as the Nextcloud app) with
 * text and voice messages. Games and Create are the next slices — see
 * docs/FAMILY_HUB.md — and are shown here as a roadmap rather than dead
 * buttons, so nobody taps something that does nothing.
 */
export default function Family() {
  const [tab, setTab] = useState<Tab>('chat');

  return (
    <div className="space-y-3 sm:space-y-4 max-w-6xl mx-auto pb-28 px-1 sm:px-0" data-testid="family-page">
      {/* One compact row, so the conversations get the screen. */}
      <div className="flex items-center gap-3 px-1">
        <h1 className="text-2xl font-bold tracking-tight text-white">Family</h1>
        <div
          className="ml-auto flex rounded-full border border-white/10 bg-slate-900/80 p-1"
          role="tablist"
          aria-label="Family sections"
        >
          {TABS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              role="tab"
              aria-selected={tab === id}
              onClick={() => setTab(id)}
              className={`flex min-h-9 items-center gap-1.5 rounded-full px-3 text-[13px] font-medium transition ${
                tab === id ? 'bg-gradient-to-br from-purple-500 to-indigo-600 text-white shadow' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              <Icon size={14} /> {label}
            </button>
          ))}
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
          <DrawCanvas />
          <MakeMusic />
          <Recipes />
          <div className="glass-panel p-4 rounded-2xl border border-white/5">
            <h2 className="text-sm font-semibold text-slate-200">Create</h2>
            <p className="text-xs text-slate-400 mt-1">
              Drawings, pictures, songs and recipes live in shared family space, and anything you make can be sent
              straight into a conversation.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
